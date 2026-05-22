"""Book depletion vs EWMA baseline (effective depth after own quotes)."""

from __future__ import annotations

from dataclasses import dataclass

from common.config import Settings
from common.enums import Side
from common.types import TapeTrade

from .orderbook import OrderBook


@dataclass(slots=True)
class DepletionStats:
    bid_depth_ratio: float = 1.0
    ask_depth_ratio: float = 1.0
    bid_depletion_score: float = 0.0
    ask_depletion_score: float = 0.0
    depth_depletion_asym: float = 0.0
    depletion_velocity: float = 0.0


class BookDepletionTracker:
    def __init__(self, settings: Settings) -> None:
        self.apply_settings(settings)
        self._bid_ewma: dict[str, float] = {}
        self._ask_ewma: dict[str, float] = {}
        self._prev_bid: dict[str, float] = {}
        self._prev_ask: dict[str, float] = {}
        self._prev_ts: dict[str, float] = {}
        self._velocity: dict[str, float] = {}

    def apply_settings(self, settings: Settings) -> None:
        self._top_n = max(1, int(settings.mm_depletion_top_n))
        self._alpha = max(1e-6, min(float(settings.mm_depletion_baseline_alpha), 1.0))
        self._drop_pct = float(settings.mm_depletion_drop_pct)

    def on_depth(
        self,
        symbol: str,
        book: OrderBook,
        *,
        own_bid_qty: float = 0.0,
        own_ask_qty: float = 0.0,
        ts: float,
    ) -> None:
        if not book.ready():
            return
        bid_d, ask_d = book.depth_sum(self._top_n)
        bid_d = max(0.0, bid_d - own_bid_qty)
        ask_d = max(0.0, ask_d - own_ask_qty)

        prev_b = self._prev_bid.get(symbol)
        prev_a = self._prev_ask.get(symbol)
        prev_ts = self._prev_ts.get(symbol, ts)
        dt = max(1e-6, ts - prev_ts)
        if prev_b is not None and prev_b > 0:
            drop_b = max(0.0, (prev_b - bid_d) / prev_b)
            if drop_b >= self._drop_pct:
                self._velocity[symbol] = max(self._velocity.get(symbol, 0.0), (prev_b - bid_d) / dt)
        if prev_a is not None and prev_a > 0:
            drop_a = max(0.0, (prev_a - ask_d) / prev_a)
            if drop_a >= self._drop_pct:
                self._velocity[symbol] = max(self._velocity.get(symbol, 0.0), (prev_a - ask_d) / dt)

        self._prev_bid[symbol] = bid_d
        self._prev_ask[symbol] = ask_d
        self._prev_ts[symbol] = ts

        b_ew = self._bid_ewma.get(symbol, bid_d)
        a_ew = self._ask_ewma.get(symbol, ask_d)
        self._bid_ewma[symbol] = self._alpha * bid_d + (1.0 - self._alpha) * b_ew
        self._ask_ewma[symbol] = self._alpha * ask_d + (1.0 - self._alpha) * a_ew

    def on_trade(self, symbol: str, trade: TapeTrade) -> None:
        if trade.aggressor is Side.BUY:
            self._ask_ewma[symbol] = max(0.0, self._ask_ewma.get(symbol, 0.0) - trade.qty * 0.5)
        else:
            self._bid_ewma[symbol] = max(0.0, self._bid_ewma.get(symbol, 0.0) - trade.qty * 0.5)

    def stats(self, symbol: str) -> DepletionStats:
        b_ew = max(1e-12, self._bid_ewma.get(symbol, 0.0))
        a_ew = max(1e-12, self._ask_ewma.get(symbol, 0.0))
        bid_d = self._prev_bid.get(symbol, b_ew)
        ask_d = self._prev_ask.get(symbol, a_ew)
        bid_ratio = min(2.0, bid_d / b_ew) if b_ew > 0 else 1.0
        ask_ratio = min(2.0, ask_d / a_ew) if a_ew > 0 else 1.0
        bid_score = _depletion_score(bid_ratio)
        ask_score = _depletion_score(ask_ratio)
        return DepletionStats(
            bid_depth_ratio=bid_ratio,
            ask_depth_ratio=ask_ratio,
            bid_depletion_score=bid_score,
            ask_depletion_score=ask_score,
            depth_depletion_asym=ask_score - bid_score,
            depletion_velocity=self._velocity.get(symbol, 0.0),
        )


def _depletion_score(depth_ratio: float) -> float:
    return max(0.0, min(1.0, 1.0 - depth_ratio))
