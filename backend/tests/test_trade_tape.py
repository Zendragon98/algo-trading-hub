"""TradeTape rolling window + bid/ask hit ratios."""

from __future__ import annotations

from common.enums import Side
from common.types import TapeTrade
from engine.market_data.trade_tape import TradeTape


def test_eviction_outside_window() -> None:
    tape = TradeTape(window_sec=10.0)
    tape.record(TapeTrade(symbol="BTCUSDT", price=1.0, qty=1.0, aggressor=Side.BUY, ts=0.0))
    tape.record(TapeTrade(symbol="BTCUSDT", price=1.0, qty=1.0, aggressor=Side.SELL, ts=5.0))
    tape.record(TapeTrade(symbol="BTCUSDT", price=1.0, qty=1.0, aggressor=Side.BUY, ts=20.0))

    stats = tape.stats("BTCUSDT", now=20.0)
    # First two trades are stale relative to ts=20, only the most recent buy remains.
    assert stats.ask_hit_qty == 1.0
    assert stats.bid_hit_qty == 0.0
    assert stats.ask_hit_ratio == 1.0


def test_ratios_balanced() -> None:
    tape = TradeTape(window_sec=60.0)
    for ts in range(10):
        tape.record(TapeTrade("ETHUSDT", 1.0, 1.0, Side.BUY, ts=float(ts)))
        tape.record(TapeTrade("ETHUSDT", 1.0, 1.0, Side.SELL, ts=float(ts)))
    stats = tape.stats("ETHUSDT", now=10.0)
    assert abs(stats.bid_hit_ratio - 0.5) < 1e-9
    assert abs(stats.ask_hit_ratio - 0.5) < 1e-9
