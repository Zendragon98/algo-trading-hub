"""L2 order book maintenance.

Owns one `OrderBook` instance per subscribed symbol. Bids are stored in
descending price order, asks ascending, both as plain `list[tuple]` so
slicing the top-N for imbalance is O(N) without any external deps.

Apply protocol:
    1. Caller seeds the book via `apply_snapshot()` (REST `/depth`).
    2. Subsequent `apply_diff()` calls fold WebSocket diffs into the book.
    3. Diffs older than the snapshot's `last_update_id` are ignored to
       avoid double-application during the snapshot/stream race.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from gateways.gateway_interface import DepthDiff


@dataclass(slots=True)
class BookLevel:
    price: float
    qty: float


@dataclass
class OrderBook:
    """A single symbol's L2 book."""

    symbol: str
    bids: list[BookLevel] = field(default_factory=list)
    asks: list[BookLevel] = field(default_factory=list)
    last_update_id: int = 0
    _ready: bool = False

    # --- Mutators ---

    def invalidate(self) -> None:
        """Drop sync state after a market WebSocket reconnect."""
        self.bids.clear()
        self.asks.clear()
        self.last_update_id = 0
        self._ready = False

    def apply_snapshot(self, bids: Iterable[tuple[float, float]],
                       asks: Iterable[tuple[float, float]],
                       last_update_id: int) -> None:
        self.bids = sorted((BookLevel(p, q) for p, q in bids if q > 0),
                           key=lambda lvl: -lvl.price)
        self.asks = sorted((BookLevel(p, q) for p, q in asks if q > 0),
                           key=lambda lvl: lvl.price)
        self.last_update_id = last_update_id
        self._ready = True

    def apply_diff(self, diff: DepthDiff) -> None:
        # Diffs older than the snapshot are stale, skip them.
        if diff.final_update_id <= self.last_update_id:
            return

        for price, qty in diff.bids:
            _upsert(self.bids, price, qty, descending=True)
        for price, qty in diff.asks:
            _upsert(self.asks, price, qty, descending=False)
        self.last_update_id = diff.final_update_id

    # --- Accessors ---

    def ready(self) -> bool:
        return self._ready and bool(self.bids) and bool(self.asks)

    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None

    def mid(self) -> float | None:
        bb, ba = self.best_bid(), self.best_ask()
        return (bb + ba) / 2.0 if bb is not None and ba is not None else None

    def spread_bps(self) -> float | None:
        bb, ba = self.best_bid(), self.best_ask()
        if bb is None or ba is None or bb <= 0:
            return None
        return (ba - bb) / bb * 10_000.0

    def imbalance(self, top_n: int) -> float:
        """Top-N depth imbalance in [-1, +1].

        Positive = more bid-side liquidity (book leaning long), negative =
        more ask-side liquidity. Returns 0 if either side is missing.
        """
        if not self.bids or not self.asks:
            return 0.0
        bid_vol = sum(lvl.qty for lvl in self.bids[:top_n])
        ask_vol = sum(lvl.qty for lvl in self.asks[:top_n])
        denom = bid_vol + ask_vol
        if denom <= 0:
            return 0.0
        return (bid_vol - ask_vol) / denom

    def micro_price(self, top_n: int = 1) -> float | None:
        """Volume-weighted mid that accounts for top-of-book imbalance."""
        if not self.bids or not self.asks:
            return None
        bid_vol = sum(lvl.qty for lvl in self.bids[:top_n])
        ask_vol = sum(lvl.qty for lvl in self.asks[:top_n])
        denom = bid_vol + ask_vol
        if denom <= 0:
            return self.mid()
        bb = self.bids[0].price
        ba = self.asks[0].price
        # Heavier ask volume drags the price toward the bid (buyers are
        # cheaper relative to the available size on the ask) — and vice versa.
        return (bb * ask_vol + ba * bid_vol) / denom


def _upsert(side: list[BookLevel], price: float, qty: float, *, descending: bool) -> None:
    """Insert/update/delete a level keeping the side sorted.

    `qty == 0` removes the level. We use a linear scan because the top of
    the book is what matters and L2 books on Binance Futures rarely
    exceed a few hundred levels per side.
    """
    if qty <= 0:
        for i, lvl in enumerate(side):
            if lvl.price == price:
                del side[i]
                return
        return

    for i, lvl in enumerate(side):
        if lvl.price == price:
            lvl.qty = qty
            return
        if (descending and price > lvl.price) or (not descending and price < lvl.price):
            side.insert(i, BookLevel(price, qty))
            return
    side.append(BookLevel(price, qty))


class OrderBookStore:
    """Convenience map of symbol -> OrderBook used by the engine."""

    def __init__(self, symbols: list[str]) -> None:
        self._books: dict[str, OrderBook] = {sym: OrderBook(symbol=sym) for sym in symbols}

    def get(self, symbol: str) -> OrderBook:
        if symbol not in self._books:
            self._books[symbol] = OrderBook(symbol=symbol)
        return self._books[symbol]

    def __iter__(self):
        return iter(self._books.values())

    def __contains__(self, symbol: str) -> bool:
        return symbol in self._books
