"""Summary metrics for a completed backtest run."""

from __future__ import annotations

from dataclasses import dataclass

from .simulator import SimState


@dataclass(slots=True)
class BacktestMetrics:
    total_return_pct: float
    max_drawdown_pct: float
    trade_count: int
    win_rate: float
    realized_pnl: float
    final_equity: float


def compute_metrics(state: SimState) -> BacktestMetrics:
    curve = state.equity_curve
    if not curve:
        return BacktestMetrics(0.0, 0.0, 0, 0.0, 0.0, state.cash)

    start = curve[0]
    end = curve[-1]
    ret = ((end - start) / start * 100.0) if start > 0 else 0.0
    peak = curve[0]
    max_dd = 0.0
    for eq in curve:
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)
    closes = [f for f in state.fills if f.action == "close"]
    wins = sum(1 for f in closes if f.pnl > 0)
    win_rate = wins / len(closes) if closes else 0.0
    realized = sum(f.pnl for f in closes)
    return BacktestMetrics(
        total_return_pct=ret,
        max_drawdown_pct=max_dd * 100.0,
        trade_count=len(state.fills),
        win_rate=win_rate,
        realized_pnl=realized,
        final_equity=end,
    )
