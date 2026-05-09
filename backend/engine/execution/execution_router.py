"""Entry point from signals/exits to the execution layer.

Sequence per parent order:

    Signal/ExitIntent -> ParentOrder
        -> AlgoWheel.choose(features) annotates ParentOrder.algo_mode
        -> ExecutionTracker.on_parent_submit captures arrival mid
        -> VwapExecutor.execute(parent) spawns the schedule

The router is the only place that constructs `ParentOrder` ids, so all
log lines and dashboard rows can reference the same id end-to-end.
"""

from __future__ import annotations

import logging
import uuid

from common.enums import Side
from common.types import ParentOrder

from ..market_data.feature_store import FeatureStore
from .algo_wheel import AlgoWheel
from .execution_metrics import ExecutionTracker
from .vwap_executor import VwapExecutor

logger = logging.getLogger(__name__)


def _new_parent_id() -> str:
    return f"P-{uuid.uuid4().hex[:10]}"


class ExecutionRouter:
    def __init__(
        self,
        wheel: AlgoWheel,
        executor: VwapExecutor,
        features: FeatureStore,
        tracker: ExecutionTracker,
    ) -> None:
        self._wheel = wheel
        self._executor = executor
        self._features = features
        self._tracker = tracker

    async def submit(
        self,
        symbol: str,
        side: Side,
        qty: float,
        notes: str = "",
        max_slippage_bps: float = 5.0,
    ) -> ParentOrder:
        parent = ParentOrder(
            id=_new_parent_id(),
            symbol=symbol,
            side=side,
            qty=qty,
            notes=notes,
            max_slippage_bps=max_slippage_bps,
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
