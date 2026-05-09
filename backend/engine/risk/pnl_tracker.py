"""Realised + unrealised PnL aggregation.

Most of the heavy lifting already happens in `PositionTracker`; this
module is a thin facade that lets the risk manager and the dashboard
ask "what's my PnL right now?" without needing to know the position
schema.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..portfolio.portfolio import Portfolio


@dataclass(frozen=True, slots=True)
class PnLSnapshot:
    realized: float
    unrealized: float

    @property
    def total(self) -> float:
        return self.realized + self.unrealized


class PnLTracker:
    def __init__(self, portfolio: Portfolio) -> None:
        self._portfolio = portfolio

    def snapshot(self) -> PnLSnapshot:
        snap = self._portfolio.snapshot()
        return PnLSnapshot(realized=snap.realized_pnl, unrealized=snap.unrealized_pnl)

    def drawdown_pct(self) -> float:
        """Drawdown from session-start equity, in (0, 1)."""
        start = self._portfolio.session_start_equity
        if start <= 0:
            return 0.0
        current = self._portfolio.snapshot().equity
        if current >= start:
            return 0.0
        return (start - current) / start
