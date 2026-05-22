"""Post-fill markout tracking for adverse selection gating."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from common.enums import Side
from common.types import Fill


@dataclass(slots=True)
class MarkoutStats:
    adverse_ewma_bps: float = 0.0
    last_fill_adverse_bps: float = 0.0
    fill_count: int = 0


@dataclass(slots=True)
class _PendingMarkout:
    side: Side
    fill_price: float
    mid_at_fill: float
    ts: float
    horizons_done: set[float]


class MarkoutTracker:
    def __init__(self, alpha: float = 0.15) -> None:
        self._alpha = max(1e-6, min(alpha, 1.0))
        self._pending: dict[str, deque[_PendingMarkout]] = {}
        self._adverse_ewma: dict[str, float] = {}
        self._last_adverse: dict[str, float] = {}
        self._fill_count: dict[str, int] = {}

    def on_fill(self, symbol: str, fill: Fill, mid_at_fill: float, ts: float) -> None:
        if mid_at_fill <= 0 or fill.price <= 0:
            return
        q = self._pending.setdefault(symbol, deque(maxlen=64))
        q.append(
            _PendingMarkout(
                side=fill.side,
                fill_price=fill.price,
                mid_at_fill=mid_at_fill,
                ts=ts,
                horizons_done=set(),
            )
        )
        self._fill_count[symbol] = self._fill_count.get(symbol, 0) + 1

    def on_mid(self, symbol: str, mid: float, ts: float) -> None:
        q = self._pending.get(symbol)
        if not q or mid <= 0:
            return
        for item in list(q):
            for horizon in (1.0, 5.0):
                if horizon in item.horizons_done:
                    continue
                if ts - item.ts < horizon:
                    continue
                item.horizons_done.add(horizon)
                bps = _signed_markout_bps(item.side, item.fill_price, mid)
                if bps > 0:
                    prev = self._adverse_ewma.get(symbol, 0.0)
                    self._adverse_ewma[symbol] = self._alpha * bps + (1.0 - self._alpha) * prev
                    self._last_adverse[symbol] = bps

    def stats(self, symbol: str) -> MarkoutStats:
        return MarkoutStats(
            adverse_ewma_bps=self._adverse_ewma.get(symbol, 0.0),
            last_fill_adverse_bps=self._last_adverse.get(symbol, 0.0),
            fill_count=self._fill_count.get(symbol, 0),
        )


def _signed_markout_bps(side: Side, fill_price: float, mid: float) -> float:
    """Positive = adverse for the fill side."""
    if side is Side.BUY:
        return (mid - fill_price) / fill_price * 10_000.0
    return (fill_price - mid) / fill_price * 10_000.0
