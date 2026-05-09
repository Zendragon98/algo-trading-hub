"""Entry point from signals/exits to the execution layer.

Sequence per parent order:

    Signal/ExitIntent -> ParentOrder
        -> SubmitGuard.can_submit_parent (engine/symbol breaker, open-parent cap)
        -> AlgoWheel.choose(features) annotates ParentOrder.algo_mode
        -> ExecutionTracker.on_parent_submit captures arrival mid
        -> VwapExecutor.execute(parent) spawns the schedule

The router is the only place that constructs `ParentOrder` ids, so all
log lines and dashboard rows can reference the same id end-to-end.
"""

from __future__ import annotations

import logging
import uuid
from typing import Protocol

from common.enums import Side
from common.types import ParentOrder

from ..market_data.feature_store import FeatureStore
from .algo_wheel import AlgoWheel
from .execution_metrics import ExecutionTracker
from .vwap_executor import VwapExecutor

logger = logging.getLogger(__name__)


def _new_parent_id() -> str:
    return f"P-{uuid.uuid4().hex[:10]}"


class _ParentGate(Protocol):
    """Minimal SubmitGuard surface required by the router."""

    def can_submit_parent(self, symbol: str) -> tuple[bool, str]: ...


class ParentSubmissionRejected(RuntimeError):
    """Raised when a parent fails the pre-router safety gate.

    Surfaces the gating reason so callers (the engine's group-dispatch
    path) can log a clean abort instead of letting a generic runtime
    error bubble up.
    """


class ExecutionRouter:
    def __init__(
        self,
        wheel: AlgoWheel,
        executor: VwapExecutor,
        features: FeatureStore,
        tracker: ExecutionTracker,
        submit_guard: _ParentGate | None = None,
    ) -> None:
        self._wheel = wheel
        self._executor = executor
        self._features = features
        self._tracker = tracker
        self._submit_guard = submit_guard

    def attach_submit_guard(self, guard: _ParentGate) -> None:
        self._submit_guard = guard

    async def submit(
        self,
        symbol: str,
        side: Side,
        qty: float,
        notes: str = "",
        max_slippage_bps: float = 5.0,
        reduce_only: bool = False,
    ) -> ParentOrder:
        if self._submit_guard is not None:
            # Reduce-only exits bypass the *symbol* gate so a paused symbol
            # can still close out — the engine breaker still catches it.
            allowed, reason = self._submit_guard.can_submit_parent(symbol)
            if not allowed and not reduce_only:
                logger.warning("router rejected %s submit: %s", symbol, reason)
                raise ParentSubmissionRejected(reason)
        parent = ParentOrder(
            id=_new_parent_id(),
            symbol=symbol,
            side=side,
            qty=qty,
            notes=notes,
            max_slippage_bps=max_slippage_bps,
            reduce_only=reduce_only,
        )
        feat = self._features.snapshot(symbol)
        parent.algo_mode = self._wheel.choose(parent, feat)
        # Arrival = mid at the moment we decided to trade. Falls back to 0
        # if the book hasn't warmed up; the tracker treats 0 as "unknown"
        # and emits a 0 slippage rather than a divide-by-zero spike.
        arrival = feat.mid or 0.0
        self._tracker.on_parent_submit(parent, arrival_price=arrival)
        await self._executor.execute(parent)
        return parent

    async def cancel(self, parent_id: str) -> None:
        await self._executor.cancel_parent(parent_id)
        await self._tracker.close_parent(parent_id)

    async def shutdown(self) -> None:
        await self._executor.shutdown()
