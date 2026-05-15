"""Per-parent execution-quality tracking.

For each `ParentOrder` in flight we record:
    - arrival_price       mid at parent submission time
    - vwap_price          volume-weighted average of venue fill prices
    - slippage_bps        adverse move from arrival -> vwap, signed so
                          positive = bad for the trader
    - impact_bps          legacy field (always zero); TCA uses slippage above
    - fill_ratio          filled_qty / requested_qty
    - duration_sec        time from submit to last fill (or now if open)

A parent is considered complete when `fill_ratio >= 1.0 - epsilon` or
when the executor reports no more outstanding children. Completed
reports are appended to a bounded ring; the live working set is held
separately so the API can render both.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from time import time

from common.enums import EventType, Side
from common.events import Event, EventBus
from common.types import ParentOrder

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ExecutionReport:
    """Snapshot of a parent order's execution quality."""

    parent_id: str
    symbol: str
    side: str                 # "buy" | "sell"
    requested_qty: float
    filled_qty: float = 0.0
    arrival_price: float = 0.0
    vwap_price: float = 0.0
    slippage_bps: float = 0.0
    fee_bps: float = 0.0
    fee_adjusted_slippage_bps: float = 0.0
    impact_bps: float = 0.0  # reserved for API compatibility; use slippage_bps for TCA
    fill_ratio: float = 0.0
    duration_sec: float = 0.0
    algo_mode: str | None = None
    started_at: float = field(default_factory=time)
    completed_at: float | None = None

    @property
    def is_complete(self) -> bool:
        return self.completed_at is not None


class ExecutionTracker:
    """Maintains live + historical execution reports."""

    _COMPLETE_EPSILON = 1e-9

    def __init__(self, bus: EventBus, history_size: int = 100) -> None:
        self._bus = bus
        self._open: dict[str, ExecutionReport] = {}
        self._history: list[ExecutionReport] = []
        self._history_size = history_size

    # --- Lifecycle ---

    def on_parent_submit(self, parent: ParentOrder, arrival_price: float) -> ExecutionReport:
        report = ExecutionReport(
            parent_id=parent.id,
            symbol=parent.symbol,
            side=parent.side.value,
            requested_qty=parent.qty,
            arrival_price=arrival_price,
            algo_mode=parent.algo_mode.value if parent.algo_mode else None,
        )
        self._open[parent.id] = report
        return report

    async def on_fill(
        self,
        parent_id: str,
        side: Side,
        qty: float,
        venue_price: float,
        impact_bps: float,
        *,
        fee: float = 0.0,
    ) -> None:
        report = self._open.get(parent_id)
        if report is None:
            return

        prev_filled = report.filled_qty
        new_filled = prev_filled + qty
        # Volume-weighted update of vwap_price.
        if new_filled > 0:
            report.vwap_price = (
                report.vwap_price * prev_filled + venue_price * qty
            ) / new_filled
        report.filled_qty = new_filled
        report.fill_ratio = (
            new_filled / report.requested_qty if report.requested_qty > 0 else 0.0
        )
        # Aggregate impact as a qty-weighted average so it stays comparable.
        report.impact_bps = (
            (report.impact_bps * prev_filled + impact_bps * qty) / new_filled
            if new_filled > 0
            else 0.0
        )
        report.slippage_bps = _slippage_bps(side, report.arrival_price, report.vwap_price)
        notional = new_filled * report.vwap_price if report.vwap_price > 0 else 0.0
        if notional > 0 and fee > 0:
            fee_bps = fee / notional * 10_000.0
            report.fee_bps = (
                (report.fee_bps * prev_filled + fee_bps * qty) / new_filled
                if new_filled > 0
                else 0.0
            )
        report.fee_adjusted_slippage_bps = report.slippage_bps + report.fee_bps
        report.duration_sec = max(0.0, time() - report.started_at)

        if report.fill_ratio >= 1.0 - self._COMPLETE_EPSILON:
            await self._mark_complete(report)
        else:
            await self._publish_progress(report)

    async def close_parent(self, parent_id: str) -> None:
        """Force-close an open report (e.g. operator cancel before full fill)."""
        report = self._open.get(parent_id)
        if report is None:
            return
        if (
            report.requested_qty > 0
            and report.fill_ratio < 1.0 - self._COMPLETE_EPSILON
        ):
            await self._bus.publish(
                Event(
                    type=EventType.STATUS,
                    payload={
                        "kind": "parent_underfill",
                        "parent_id": parent_id,
                        "fill_ratio": report.fill_ratio,
                    },
                )
            )
        await self._mark_complete(report)

    # --- Reads ---

    def open_reports(self) -> list[ExecutionReport]:
        return list(self._open.values())

    def history(self) -> list[ExecutionReport]:
        # Newest first; matches the dashboard's expectation.
        return list(reversed(self._history))

    def aggregate(self) -> dict[str, float]:
        """Aggregate stats across the completed history."""
        completed = self._history
        if not completed:
            return {
                "count": 0,
                "avg_slippage_bps": 0.0,
                "avg_impact_bps": 0.0,
                "avg_fill_ratio": 0.0,
                "avg_duration_sec": 0.0,
                "total_traded_notional": 0.0,
            }
        n = len(completed)
        return {
            "count": float(n),
            "avg_slippage_bps": sum(r.slippage_bps for r in completed) / n,
            "avg_impact_bps": sum(r.impact_bps for r in completed) / n,
            "avg_fill_ratio": sum(r.fill_ratio for r in completed) / n,
            "avg_duration_sec": sum(r.duration_sec for r in completed) / n,
            "total_traded_notional": sum(r.filled_qty * r.vwap_price for r in completed),
        }

    # --- Internal ---

    async def _mark_complete(self, report: ExecutionReport) -> None:
        report.completed_at = time()
        report.duration_sec = max(0.0, report.completed_at - report.started_at)
        self._open.pop(report.parent_id, None)
        self._history.append(report)
        if len(self._history) > self._history_size:
            self._history = self._history[-self._history_size :]
        await self._bus.publish(
            Event(type=EventType.EXECUTION_REPORT, payload=asdict(report))
        )
        logger.info(
            "parent %s done: filled=%.4f@%.4f arrival=%.4f slip=%.2fbps impact=%.2fbps in %.1fs",
            report.parent_id,
            report.filled_qty,
            report.vwap_price,
            report.arrival_price,
            report.slippage_bps,
            report.impact_bps,
            report.duration_sec,
        )

    async def _publish_progress(self, report: ExecutionReport) -> None:
        await self._bus.publish(
            Event(type=EventType.PARENT_UPDATE, payload=asdict(report))
        )


def _slippage_bps(side: Side, arrival: float, vwap: float) -> float:
    """Signed slippage in bps. Positive means the trader paid worse than arrival."""
    if arrival <= 0 or vwap <= 0:
        return 0.0
    # For a BUY, paying more (vwap > arrival) is bad -> positive slippage.
    # For a SELL, receiving less (vwap < arrival) is bad -> positive slippage.
    return (vwap - arrival) / arrival * 10_000.0 * side.sign
