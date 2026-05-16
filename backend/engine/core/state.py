"""Single in-memory source of truth for the engine.

All other modules read state through narrow interfaces; the API layer
reads through `EngineState` snapshots so it never needs to know about
the underlying classes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import time

from common.enums import EngineStatus

from ..performance.performance_tracker import TradeRecord
from ..portfolio.portfolio import Portfolio
from ..position.position_tracker import PositionTracker


@dataclass
class EngineState:
    """Mutable bag of per-process state."""

    status: EngineStatus = EngineStatus.STOPPED
    started_at: float = field(default_factory=time)
    last_tick_ts: float = 0.0


class EngineSnapshot:
    """Read-only view used by the API layer."""

    def __init__(
        self,
        state: EngineState,
        position_tracker: PositionTracker,
        portfolio: Portfolio,
        trades: list[TradeRecord],
        win_rate: float,
        gross_win_pnl: float,
        gross_loss_pnl: float,
        profit_factor: float | None,
    ) -> None:
        self.status = state.status
        self.started_at = state.started_at
        self.uptime_sec = max(0.0, time() - state.started_at)
        self.last_tick_ts = state.last_tick_ts

        portfolio_snap = portfolio.snapshot()
        self.equity = portfolio_snap.equity
        self.cash = portfolio_snap.cash
        self.realized_pnl = portfolio_snap.realized_pnl
        self.unrealized_pnl = portfolio_snap.unrealized_pnl
        self.gross_notional = portfolio_snap.gross_notional
        self.net_notional = portfolio_snap.net_notional
        self.equity_curve = [pt.equity for pt in portfolio.equity_curve()]

        self.positions = position_tracker.all()
        self.trades = trades
        self.win_rate = win_rate
        self.gross_win_pnl = gross_win_pnl
        self.gross_loss_pnl = gross_loss_pnl
        self.profit_factor = profit_factor
