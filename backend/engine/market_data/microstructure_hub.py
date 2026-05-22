"""Facade wiring mid, tape, depletion, markout, and toxicity."""

from __future__ import annotations

from dataclasses import dataclass
from time import time

from common.config import Settings
from common.types import Fill, TapeTrade

from .book_depletion import BookDepletionTracker, DepletionStats
from .markout_tracker import MarkoutStats, MarkoutTracker
from .mid_tracker import MidReturnTracker, MidStats
from .orderbook import OrderBookStore
from .toxicity import ToxicityScorer, ToxicityStats
from .trade_tape import TapeStats, TradeTape


@dataclass(slots=True)
class MicrostructureSnapshot:
    mid: MidStats
    tape: TapeStats
    depletion: DepletionStats
    markout: MarkoutStats
    toxicity: ToxicityStats


class MicrostructureHub:
    def __init__(
        self,
        books: OrderBookStore,
        tape: TradeTape,
        settings: Settings,
    ) -> None:
        self._books = books
        self._tape = tape
        self._mid = MidReturnTracker(settings)
        self._depletion = BookDepletionTracker(settings)
        self._markout = MarkoutTracker()
        self._toxicity = ToxicityScorer(settings)
        self._settings = settings

    def apply_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._mid.apply_settings(settings)
        self._depletion.apply_settings(settings)
        self._toxicity.apply_settings(settings)
        self._tape.set_window_sec(
            settings.trade_tape_window_sec,
            large_trade_mult=settings.mm_large_trade_mult,
        )

    def on_trade(self, trade: TapeTrade) -> None:
        self._tape.record(trade)
        self._depletion.on_trade(trade.symbol, trade)

    def on_mid(
        self,
        symbol: str,
        mid: float,
        ts: float,
        *,
        own_bid_qty: float = 0.0,
        own_ask_qty: float = 0.0,
    ) -> None:
        self._mid.on_mid(symbol, mid, ts)
        self._markout.on_mid(symbol, mid, ts)
        book = self._books.get(symbol)
        if book.ready():
            self._depletion.on_depth(
                symbol,
                book,
                own_bid_qty=own_bid_qty,
                own_ask_qty=own_ask_qty,
                ts=ts,
            )

    def on_fill(self, symbol: str, fill: Fill, mid_at_fill: float, ts: float) -> None:
        self._markout.on_fill(symbol, fill, mid_at_fill, ts)

    def last_fill_adverse_bps(self, symbol: str) -> float:
        return self._markout.stats(symbol).last_fill_adverse_bps

    def snapshot(
        self,
        symbol: str,
        *,
        own_bid_qty: float = 0.0,
        own_ask_qty: float = 0.0,
    ) -> MicrostructureSnapshot:
        now = time()
        book = self._books.get(symbol)
        if book.ready():
            mid_val = book.mid() or 0.0
            self._depletion.on_depth(
                symbol,
                book,
                own_bid_qty=own_bid_qty,
                own_ask_qty=own_ask_qty,
                ts=now,
            )
            if mid_val > 0:
                self._mid.on_mid(symbol, mid_val, now)
                self._markout.on_mid(symbol, mid_val, now)

        tape = self._tape.stats(symbol, now=now)
        return MicrostructureSnapshot(
            mid=self._mid.stats(symbol, now=now),
            tape=tape,
            depletion=self._depletion.stats(symbol),
            markout=self._markout.stats(symbol),
            toxicity=self._toxicity.score(
                tape=tape,
                depletion=self._depletion.stats(symbol),
                markout=self._markout.stats(symbol),
                mid=self._mid.stats(symbol, now=now),
            ),
        )
