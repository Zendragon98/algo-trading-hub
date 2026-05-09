"""Equity curve metrics surfaced on the dashboard KPIs.

Pure aggregation over the existing Portfolio + position state. Computed
on-demand so we don't store derived data we can recompute cheaply.
"""

from __future__ import annotations

from dataclasses import dataclass

from common.types import Fill

from ..portfolio.portfolio import Portfolio


@dataclass(slots=True)
class TradeRecord:
    """A closed-out unit of trading PnL surfaced in the trades table."""

    id: str
    ts: float
    symbol: str
    side: str
    qty: float
    price: float
    pnl: float | None


class PerformanceTracker:
    """Maintains a rolling history of fills + computes win rate."""

    def __init__(self, portfolio: Portfolio, history_size: int = 200) -> None:
        self._portfolio = portfolio
        self._fills: list[TradeRecord] = []
        self._history_size = history_size

    def record_fill(self, fill: Fill, realized_pnl: float | None) -> TradeRecord:
        record = TradeRecord(
            id=fill.trade_id or fill.child_id,
            ts=fill.ts,
            symbol=fill.symbol,
            side=fill.side.value,
            qty=fill.qty,
            price=fill.price,
            pnl=realized_pnl,
        )
        self._fills.append(record)
        if len(self._fills) > self._history_size:
            self._fills = self._fills[-self._history_size :]
        return record

    def trades(self) -> list[TradeRecord]:
        # Newest first — matches the dashboard's expectation.
        return list(reversed(self._fills))

    def win_rate(self) -> float:
        closed = [f for f in self._fills if f.pnl is not None]
        if not closed:
            return 0.0
        wins = sum(1 for f in closed if (f.pnl or 0) > 0)
        return wins / len(closed) * 100.0
