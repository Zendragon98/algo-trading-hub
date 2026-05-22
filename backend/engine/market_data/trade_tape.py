"""Rolling trade tape used to compute the bid-hit / ask-hit ratio.

The AlgoWheel keys off this metric: a high ask-hit ratio means buyers
have been aggressive over the last N seconds, which is a hint that
prices may continue grinding higher and a buy parent should frontload.

We classify trades using the `aggressor` field already populated by
`MarketConnection` from Binance's `m` flag — no need to peek at the book
here.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from time import time

from common.enums import Side
from common.types import TapeTrade


@dataclass(slots=True)
class TapeStats:
    """Snapshot returned from `TradeTape.stats()`."""

    bid_hit_qty: float
    ask_hit_qty: float
    bid_hit_count: int
    ask_hit_count: int
    last_price: float | None = None
    last_trade_ts: float = 0.0
    trades_per_sec: float = 0.0
    qty_per_sec: float = 0.0
    vpin: float = 0.5
    large_trade_share: float = 0.0

    @property
    def total_qty(self) -> float:
        return self.bid_hit_qty + self.ask_hit_qty

    @property
    def bid_hit_ratio(self) -> float:
        """Fraction of volume initiated by sellers (hit the bid)."""
        return self.bid_hit_qty / self.total_qty if self.total_qty > 0 else 0.0

    @property
    def ask_hit_ratio(self) -> float:
        """Fraction of volume initiated by buyers (lifted the ask)."""
        return self.ask_hit_qty / self.total_qty if self.total_qty > 0 else 0.0


class TradeTape:
    """Per-symbol rolling window of trades.

    Memory bound: trades older than `window_sec` are evicted on every
    write, so the deque size is roughly proportional to message rate.
    Reads are O(1) because aggregates are maintained incrementally.
    """

    def __init__(self, window_sec: float, *, large_trade_mult: float = 3.0) -> None:
        self._window = float(window_sec)
        self._large_mult = max(1.0, float(large_trade_mult))
        self._tapes: dict[str, deque[TapeTrade]] = {}
        self._sums: dict[str, _RunningSums] = {}
        self._last_price: dict[str, float] = {}
        self._last_ts: dict[str, float] = {}

    def set_window_sec(self, window_sec: float, *, large_trade_mult: float | None = None) -> None:
        self._window = max(1.0, float(window_sec))
        if large_trade_mult is not None:
            self._large_mult = max(1.0, float(large_trade_mult))

    def record(self, trade: TapeTrade) -> None:
        tape = self._tapes.setdefault(trade.symbol, deque())
        sums = self._sums.setdefault(trade.symbol, _RunningSums())

        tape.append(trade)
        sums.add(trade)
        self._last_price[trade.symbol] = trade.price
        self._last_ts[trade.symbol] = trade.ts

        cutoff = trade.ts - self._window
        while tape and tape[0].ts < cutoff:
            sums.remove(tape.popleft())

    def stats(self, symbol: str, *, now: float | None = None) -> TapeStats:
        # Lazy-evict on read so callers always see a window relative to `now`.
        tape = self._tapes.get(symbol)
        sums = self._sums.get(symbol)
        if tape is None or sums is None:
            return TapeStats(0.0, 0.0, 0, 0)

        if now is not None:
            cutoff = now - self._window
            while tape and tape[0].ts < cutoff:
                sums.remove(tape.popleft())

        tps, qps = _velocity(tape, now or time())
        vpin = sums.ask_qty / sums.total_qty if sums.total_qty > 0 else 0.5
        large_share = _large_trade_share(tape, self._large_mult)
        return TapeStats(
            bid_hit_qty=sums.bid_qty,
            ask_hit_qty=sums.ask_qty,
            bid_hit_count=sums.bid_count,
            ask_hit_count=sums.ask_count,
            last_price=self._last_price.get(symbol),
            last_trade_ts=self._last_ts.get(symbol, 0.0),
            trades_per_sec=tps,
            qty_per_sec=qps,
            vpin=vpin,
            large_trade_share=large_share,
        )


def _velocity(tape: deque[TapeTrade], now: float) -> tuple[float, float]:
    if len(tape) < 2:
        return 0.0, 0.0
    span = max(1e-6, tape[-1].ts - tape[0].ts)
    n = len(tape)
    qty = sum(t.qty for t in tape)
    return n / span, qty / span


def _large_trade_share(tape: deque[TapeTrade], mult: float) -> float:
    if not tape:
        return 0.0
    avg = sum(t.qty for t in tape) / len(tape)
    if avg <= 0:
        return 0.0
    thresh = mult * avg
    large = sum(t.qty for t in tape if t.qty >= thresh)
    total = sum(t.qty for t in tape)
    return large / total if total > 0 else 0.0


class _RunningSums:
    """Incremental aggregates kept in sync with the deque contents."""

    __slots__ = ("bid_qty", "ask_qty", "bid_count", "ask_count")

    def __init__(self) -> None:
        self.bid_qty = 0.0
        self.ask_qty = 0.0
        self.bid_count = 0
        self.ask_count = 0

    def add(self, trade: TapeTrade) -> None:
        if trade.aggressor is Side.SELL:
            self.bid_qty += trade.qty
            self.bid_count += 1
        else:
            self.ask_qty += trade.qty
            self.ask_count += 1

    def remove(self, trade: TapeTrade) -> None:
        if trade.aggressor is Side.SELL:
            self.bid_qty -= trade.qty
            self.bid_count -= 1
        else:
            self.ask_qty -= trade.qty
            self.ask_count -= 1

    @property
    def total_qty(self) -> float:
        return self.bid_qty + self.ask_qty
