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

from common.config import Settings
from common.enums import Side, Urgency
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
        settings: Settings,
        submit_guard: _ParentGate | None = None,
    ) -> None:
        self._wheel = wheel
        self._executor = executor
        self._features = features
        self._tracker = tracker
        self._settings = settings
        self._submit_guard = submit_guard

    def attach_submit_guard(self, guard: _ParentGate) -> None:
        self._submit_guard = guard

    async def submit(
        self,
        symbol: str,
        side: Side,
        qty: float,
        notes: str = "",
        max_slippage_bps: float | None = None,
        reduce_only: bool = False,
        *,
        urgency: Urgency | None = None,
        signal_score: float = 0.0,
        group_id: str | None = None,
        strategy_name: str = "",
    ) -> ParentOrder:
        if self._submit_guard is not None:
            # Reduce-only exits bypass the *symbol* gate so a paused symbol
            # can still close out — the engine breaker still catches it.
            allowed, reason = self._submit_guard.can_submit_parent(symbol)
            if not allowed and not reduce_only:
                logger.warning("router rejected %s submit: %s", symbol, reason)
                raise ParentSubmissionRejected(reason)
        resolved_urgency = urgency or self._resolve_urgency(
            reduce_only=reduce_only, signal_score=signal_score,
        )
        slip_bps = max_slippage_bps
        if slip_bps is None:
            slip_bps = (
                self._settings.urgent_max_slippage_bps
                if resolved_urgency is Urgency.AGGRESSIVE
                else 5.0
            )
        parent = ParentOrder(
            id=_new_parent_id(),
            symbol=symbol,
            side=side,
            qty=qty,
            notes=notes,
            max_slippage_bps=slip_bps,
            reduce_only=reduce_only,
            urgency=resolved_urgency,
            signal_score=signal_score,
            group_id=group_id,
            strategy_name=strategy_name,
        )
        feat = self._features.snapshot(symbol)
        parent.algo_mode = self._wheel.choose(parent, feat, self._settings)
        # Arrival = mid at the moment we decided to trade. Falls back to 0
        # if the book hasn't warmed up; the tracker treats 0 as "unknown"
        # and emits a 0 slippage rather than a divide-by-zero spike.
        arrival = feat.mid or 0.0
        self._tracker.on_parent_submit(parent, arrival_price=arrival)
        await self._executor.execute(parent)
        return parent

    def _resolve_urgency(self, *, reduce_only: bool, signal_score: float) -> Urgency:
        if reduce_only:
            return Urgency.AGGRESSIVE
        if signal_score >= float(self._settings.urgent_score_threshold):
            return Urgency.AGGRESSIVE
        return Urgency.PASSIVE

    async def cancel(self, parent_id: str) -> None:
        await self._executor.cancel_parent(parent_id)
        await self._tracker.close_parent(parent_id)

    async def shutdown(self) -> None:
        await self._executor.shutdown()
