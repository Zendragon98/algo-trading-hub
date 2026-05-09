"""Snapshot of per-symbol features consumed by strategies + algo wheel.

Strategies should never reach into the order book or trade tape directly;
they consume `Features` snapshots so the surface stays small and easy to
mock in tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import time

from common.config import Settings

from .orderbook import OrderBookStore
from .trade_tape import TradeTape


@dataclass(slots=True)
class Features:
    """Microstructure features for a single symbol.

    All fields are populated from the live order book + tape at read
    time. None means "not enough data yet" (typically during the first
    few seconds after subscribe).
    """

    symbol: str
    ts: float = field(default_factory=time)
    mid: float | None = None
    spread_bps: float | None = None
    micro_price: float | None = None
    imbalance_topn: float = 0.0
    bid_hit_ratio: float = 0.0
    ask_hit_ratio: float = 0.0
    # Aggressor trade counts in the rolling tape window (`trade_tape_window_sec`,
    # default 300s): bid hits = sellers lifted bids; ask hits = buyers lifted offers.
    tape_bid_hit_count: int = 0
    tape_ask_hit_count: int = 0
    last_price: float | None = None


class FeatureStore:
    """Read-through view onto the order book + trade tape."""

    def __init__(
        self,
        books: OrderBookStore,
        tape: TradeTape,
        settings: Settings,
    ) -> None:
        self._books = books
        self._tape = tape
        self._top_n = settings.imbalance_top_n

    def apply_settings(self, settings: Settings) -> None:
        self._top_n = settings.imbalance_top_n

    def snapshot(self, symbol: str) -> Features:
        book = self._books.get(symbol)
        stats = self._tape.stats(symbol, now=time())

        if not book.ready():
            return Features(
                symbol=symbol,
                bid_hit_ratio=stats.bid_hit_ratio,
                ask_hit_ratio=stats.ask_hit_ratio,
                tape_bid_hit_count=stats.bid_hit_count,
                tape_ask_hit_count=stats.ask_hit_count,
            )

        return Features(
            symbol=symbol,
            mid=book.mid(),
            spread_bps=book.spread_bps(),
            micro_price=book.micro_price(top_n=1),
            imbalance_topn=book.imbalance(self._top_n),
            bid_hit_ratio=stats.bid_hit_ratio,
            ask_hit_ratio=stats.ask_hit_ratio,
            tape_bid_hit_count=stats.bid_hit_count,
            tape_ask_hit_count=stats.ask_hit_count,
        )
