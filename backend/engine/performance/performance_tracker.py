"""Equity curve metrics surfaced on the dashboard KPIs.

Pure aggregation over the existing Portfolio + position state. Computed
on-demand so we don't store derived data we can recompute cheaply.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from common.types import Fill

from ..portfolio.portfolio import Portfolio
from .fill_classification import FillClassification


@dataclass(slots=True)
class TradeRecord:
    """One fill surfaced in the RECENT TRADES table."""

    id: str
    ts: float
    symbol: str
    side: str
    qty: float
    price: float
    action: Literal["open", "close"]
    entry_price: float | None
    exit_price: float | None
    pnl: float | None


class PerformanceTracker:
    """Maintains a rolling history of fills + computes win rate."""

    def __init__(self, portfolio: Portfolio, history_size: int = 200) -> None:
        self._portfolio = portfolio
        self._fills: list[TradeRecord] = []
        self._history_size = history_size

    def record_fill(self, fill: Fill, classification: FillClassification) -> TradeRecord:
        record = TradeRecord(
            id=fill.trade_id or fill.child_id,
            ts=fill.ts,
            symbol=fill.symbol,
            side=fill.side.value,
            qty=fill.qty,
            price=fill.price,
            action=classification.action,
            entry_price=classification.entry_price,
            exit_price=classification.exit_price,
            pnl=classification.pnl,
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
