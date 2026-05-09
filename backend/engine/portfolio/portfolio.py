"""Portfolio aggregation.

The Portfolio is the engine's source of truth for equity, cash, and
exposure. It is read by:
    - the risk manager (to evaluate pre-trade limits)
    - the API layer (to serve the equity card / equity curve)
    - the stop-loss monitor (to compute drawdown vs session start)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from time import time

from common.enums import EventType
from common.events import Event, EventBus
from common.types import Position

from ..position.position_tracker import PositionTracker

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class EquityPoint:
    """One sample on the equity curve."""

    ts: float
    equity: float


@dataclass
class PortfolioSnapshot:
    cash: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    gross_notional: float = 0.0
    net_notional: float = 0.0
    positions: list[Position] = field(default_factory=list)

    @property
    def equity(self) -> float:
        return self.cash + self.realized_pnl + self.unrealized_pnl


class Portfolio:
    """Maintains cash + positions + equity curve."""

    def __init__(
        self,
        bus: EventBus,
        position_tracker: PositionTracker,
        equity_curve_size: int = 256,
    ) -> None:
        self._bus = bus
        self._tracker = position_tracker
        self._cash: float = 0.0
        self._equity_curve: list[EquityPoint] = []
        self._curve_size = equity_curve_size
        self._session_start_equity: float = 0.0
        self._lock = asyncio.Lock()

    # --- Lifecycle ---

    def seed_cash(self, cash: float) -> None:
        self._cash = cash
        self._session_start_equity = self.snapshot().equity
        logger.info("portfolio seeded cash=%.2f equity=%.2f", cash, self._session_start_equity)

    @property
    def session_start_equity(self) -> float:
        return self._session_start_equity

    # --- Reads ---

    def snapshot(self) -> PortfolioSnapshot:
        positions = self._tracker.all()
        unrealized = sum(p.unrealized_pnl for p in positions)
        realized = sum(p.realized_pnl for p in positions)
        gross = sum(p.notional for p in positions)
        net = sum(p.notional * (1 if p.qty > 0 else -1) for p in positions)
        return PortfolioSnapshot(
            cash=self._cash,
            realized_pnl=realized,
            unrealized_pnl=unrealized,
            gross_notional=gross,
            net_notional=net,
            positions=positions,
        )

    def equity_curve(self) -> list[EquityPoint]:
        return list(self._equity_curve)

    # --- Periodic recompute ---

    async def mark_to_market(self) -> EquityPoint:
        """Recompute equity and append a curve point.

        Called from the engine clock at ~1Hz. Cheap because it only
        re-aggregates already-current position objects.
        """
        async with self._lock:
            snap = self.snapshot()
            point = EquityPoint(ts=time(), equity=snap.equity)
            self._equity_curve.append(point)
            if len(self._equity_curve) > self._curve_size:
                # Bound the in-memory curve so long sessions don't bloat.
                self._equity_curve = self._equity_curve[-self._curve_size:]

        await self._bus.publish(
            Event(
                type=EventType.EQUITY,
                payload={
                    "ts": point.ts,
                    "equity": point.equity,
                    "cash": snap.cash,
                    "realized_pnl": snap.realized_pnl,
                    "unrealized_pnl": snap.unrealized_pnl,
                    "gross_notional": snap.gross_notional,
                    "net_notional": snap.net_notional,
                },
            )
        )
        return point
