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
    exclude_from_streak: bool = False


class PerformanceTracker:
    """Maintains a rolling fill tape plus a win-rate window over realized PnL rows.

    ``_fills`` is every venue fill (open + close) for RECENT TRADES. KPIs use
    ``_realized`` only — closes with a PnL figure (venue ``rp`` or computed),
    capped separately so opens cannot evict realized history.
    """

    def __init__(self, portfolio: Portfolio, history_size: int = 200) -> None:
        # Keep `PERFORMANCE_TRADE_HISTORY_CAP` in `src/hooks/useAlgoStream.ts` aligned
        # with this default so dashboard rollups match the engine after WS replay.
        self._portfolio = portfolio
        self._fills: list[TradeRecord] = []
        self._realized: list[TradeRecord] = []
        self._history_size = history_size
        # Cumulative session stats (since process start); independent of the 200 cap.
        self._session_wins = 0
        self._session_losses = 0
        self._session_breakevens = 0
        self._session_gross_win = 0.0
        self._session_gross_loss = 0.0

    def record_fill(
        self,
        fill: Fill,
        classification: FillClassification,
        *,
        exclude_from_streak: bool = False,
    ) -> TradeRecord:
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
            exclude_from_streak=exclude_from_streak,
        )
        self._fills.append(record)
        if len(self._fills) > self._history_size:
            self._fills = self._fills[-self._history_size :]

        if classification.action == "close" and classification.pnl is not None:
            self._realized.append(record)
            if len(self._realized) > self._history_size:
                self._realized = self._realized[-self._history_size :]
            pnl = classification.pnl
            if pnl > 0.0:
                self._session_wins += 1
                self._session_gross_win += pnl
            elif pnl < 0.0:
                self._session_losses += 1
                self._session_gross_loss += -pnl
            else:
                self._session_breakevens += 1

        return record

    def trades(self) -> list[TradeRecord]:
        # Newest first — matches the dashboard's expectation.
        return list(reversed(self._fills))

    def realized_transactions(self) -> list[TradeRecord]:
        """Newest first — last N closes with realized PnL (transaction-style rows)."""

        return list(reversed(self._realized))

    def win_rate(self) -> float:
        if not self._realized:
            return 0.0
        wins = sum(1 for f in self._realized if (f.pnl or 0) > 0)
        return wins / len(self._realized) * 100.0

    def gross_pnls(self) -> tuple[float, float]:
        """Sum of realized PnL on winning vs losing closes (losses as a positive magnitude)."""

        gross_win = sum(f.pnl for f in self._realized if (f.pnl or 0.0) > 0.0)
        gross_loss = sum(-f.pnl for f in self._realized if (f.pnl or 0.0) < 0.0)
        return gross_win, gross_loss

    def profit_factor(self) -> float | None:
        """Gross wins / gross losses; None when there are no losing closes (avoid divide-by-zero)."""

        gross_win, gross_loss = self.gross_pnls()
        if gross_loss <= 0.0:
            return None
        return gross_win / gross_loss

    @property
    def session_wins(self) -> int:
        return self._session_wins

    @property
    def session_losses(self) -> int:
        return self._session_losses

    @property
    def session_breakevens(self) -> int:
        return self._session_breakevens

    def win_rate_session(self) -> float:
        n = self._session_wins + self._session_losses + self._session_breakevens
        if n == 0:
            return 0.0
        return self._session_wins / n * 100.0

    def gross_pnls_session(self) -> tuple[float, float]:
        return self._session_gross_win, self._session_gross_loss

    def profit_factor_session(self) -> float | None:
        gw, gl = self.gross_pnls_session()
        if gl <= 0.0:
            return None
        return gw / gl
