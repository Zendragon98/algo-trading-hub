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
    # Process boot time — never reset on trading start/stop; drives uptime_sec.
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
        realized_trades: list[TradeRecord],
        win_rate: float,
        gross_win_pnl: float,
        gross_loss_pnl: float,
        profit_factor: float | None,
        win_rate_session: float,
        gross_win_pnl_session: float,
        gross_loss_pnl_session: float,
        profit_factor_session: float | None,
        session_close_wins: int,
        session_close_losses: int,
        session_close_breakevens: int,
        rolling_close_wins: int,
        rolling_close_losses: int,
        rolling_close_breakevens: int,
        session_fees_paid: float = 0.0,
        session_funding_net: float = 0.0,
        session_start_equity: float = 0.0,
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
        curve = portfolio.equity_curve()
        self.equity_curve = [pt.equity for pt in curve]
        self.equity_timestamps = [pt.ts for pt in curve]

        self.positions = position_tracker.all()
        self.trades = trades
        self.realized_trades = realized_trades
        self.win_rate = win_rate
        self.gross_win_pnl = gross_win_pnl
        self.gross_loss_pnl = gross_loss_pnl
        self.profit_factor = profit_factor
        self.win_rate_session = win_rate_session
        self.gross_win_pnl_session = gross_win_pnl_session
        self.gross_loss_pnl_session = gross_loss_pnl_session
        self.profit_factor_session = profit_factor_session
        self.session_close_wins = session_close_wins
        self.session_close_losses = session_close_losses
        self.session_close_breakevens = session_close_breakevens
        self.rolling_close_wins = rolling_close_wins
        self.rolling_close_losses = rolling_close_losses
        self.rolling_close_breakevens = rolling_close_breakevens
        self.session_fees_paid = session_fees_paid
        self.session_funding_net = session_funding_net
        self.session_start_equity = session_start_equity
