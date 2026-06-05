"""Equity curve metrics surfaced on the dashboard KPIs.

Pure aggregation over the existing Portfolio + position state. Computed
on-demand so we don't store derived data we can recompute cheaply.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from common.types import Fill

from ..portfolio.portfolio import Portfolio
from .fill_classification import FillClassification
from .strategy_attribution import split_pnl_by_strategy


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
    strategy_name: str = ""
    exclude_from_streak: bool = False


@dataclass(slots=True)
class _PendingParentClose:
    symbol: str
    side: str
    total_pnl: float = 0.0
    total_qty: float = 0.0
    exit_notional: float = 0.0
    entry_price: float | None = None
    ts_first: float = 0.0
    ts_last: float = 0.0
    strategy_name: str = ""
    strategy_contributions: dict[str, float] = field(default_factory=dict)
    exclude_from_streak: bool = False


class PerformanceTracker:
    """Maintains a rolling fill tape plus a win-rate window over realized PnL rows.

    ``_fills`` is every venue fill (open + close) for RECENT TRADES. KPIs use
    ``_realized`` only — closes with a PnL figure (venue ``rp`` or computed),
    capped separately so opens cannot evict realized history.

    Session KPIs count **every** reducing fill with realized PnL since process
    start. The rolling ``_realized`` window still rolls VWAP parent slices into
    **one** close when the parent completes (see ``finalize_parent_close``).
    """

    def __init__(self, portfolio: Portfolio, history_size: int = 200) -> None:
        # Keep `PERFORMANCE_TRADE_HISTORY_CAP` in `src/hooks/useAlgoStream.ts` aligned
        # with this default so dashboard rollups match the engine after WS replay.
        self._portfolio = portfolio
        self._fills: list[TradeRecord] = []
        self._realized: list[TradeRecord] = []
        self._history_size = history_size
        self._pending_parent_closes: dict[str, _PendingParentClose] = {}
        # Cumulative session stats (since process start); independent of the 200 cap.
        self._session_wins = 0
        self._session_losses = 0
        self._session_breakevens = 0
        self._session_gross_win = 0.0
        self._session_gross_loss = 0.0
        self._strategy_realized: dict[str, float] = {}

    def record_fill(
        self,
        fill: Fill,
        classification: FillClassification,
        *,
        strategy_name: str = "",
        strategy_contributions: dict[str, float] | None = None,
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
            strategy_name=strategy_name,
            exclude_from_streak=exclude_from_streak,
        )
        self._fills.append(record)
        if len(self._fills) > self._history_size:
            self._fills = self._fills[-self._history_size :]

        if classification.action == "close" and classification.pnl is not None:
            self._bump_session(classification.pnl)
            parent_id = fill.parent_id
            if parent_id:
                self._accumulate_parent_close(
                    parent_id,
                    record,
                    classification.pnl,
                    strategy_contributions=strategy_contributions,
                    exclude_from_streak=exclude_from_streak,
                )
            else:
                self._attribute_strategy_pnl(
                    classification.pnl,
                    strategy_name,
                    strategy_contributions,
                )
                self._append_realized(record)

        return record

    def finalize_parent_close(self, parent_id: str) -> TradeRecord | None:
        """Roll buffered slice PnL into one realized close for win-rate KPIs."""

        acc = self._pending_parent_closes.pop(parent_id, None)
        if acc is None or acc.total_qty <= 0.0:
            return None
        exit_vwap = acc.exit_notional / acc.total_qty if acc.total_qty > 0 else acc.exit_notional
        record = TradeRecord(
            id=parent_id,
            ts=acc.ts_last,
            symbol=acc.symbol,
            side=acc.side,
            qty=acc.total_qty,
            price=exit_vwap,
            action="close",
            entry_price=acc.entry_price,
            exit_price=exit_vwap,
            pnl=acc.total_pnl,
            strategy_name=acc.strategy_name,
            exclude_from_streak=acc.exclude_from_streak,
        )
        self._attribute_strategy_pnl(
            acc.total_pnl,
            acc.strategy_name,
            acc.strategy_contributions or None,
        )
        self._append_realized(record)
        return record

    def _accumulate_parent_close(
        self,
        parent_id: str,
        record: TradeRecord,
        pnl: float,
        *,
        strategy_contributions: dict[str, float] | None,
        exclude_from_streak: bool,
    ) -> None:
        acc = self._pending_parent_closes.get(parent_id)
        if acc is None:
            acc = _PendingParentClose(
                symbol=record.symbol,
                side=record.side,
                ts_first=record.ts,
            )
            self._pending_parent_closes[parent_id] = acc
        acc.total_pnl += pnl
        acc.total_qty += record.qty
        if record.exit_price is not None:
            acc.exit_notional += record.exit_price * record.qty
        if acc.entry_price is None and record.entry_price is not None:
            acc.entry_price = record.entry_price
        if not acc.strategy_name and record.strategy_name:
            acc.strategy_name = record.strategy_name
        if strategy_contributions and not acc.strategy_contributions:
            acc.strategy_contributions = dict(strategy_contributions)
        acc.ts_last = record.ts
        if exclude_from_streak:
            acc.exclude_from_streak = True

    def _attribute_strategy_pnl(
        self,
        pnl: float,
        strategy_name: str,
        strategy_contributions: dict[str, float] | None,
    ) -> None:
        for strat, amount in split_pnl_by_strategy(
            pnl,
            strategy_name,
            strategy_contributions,
        ).items():
            self._strategy_realized[strat] = self._strategy_realized.get(strat, 0.0) + amount

    def realized_pnl_by_strategy(self) -> dict[str, float]:
        return dict(self._strategy_realized)

    def _append_realized(self, record: TradeRecord) -> None:
        self._realized.append(record)
        if len(self._realized) > self._history_size:
            self._realized = self._realized[-self._history_size :]

    def _bump_session(self, pnl: float) -> None:
        if pnl > 0.0:
            self._session_wins += 1
            self._session_gross_win += pnl
        elif pnl < 0.0:
            self._session_losses += 1
            self._session_gross_loss += -pnl
        else:
            self._session_breakevens += 1

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
