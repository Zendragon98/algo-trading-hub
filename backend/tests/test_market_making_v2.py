from __future__ import annotations

import os

os.environ.setdefault("BINANCE_API_KEY", "test")
os.environ.setdefault("BINANCE_API_SECRET", "test")

from common.config import Settings  # noqa: E402
from engine.market_data.feature_store import Features  # noqa: E402
from engine.market_data.own_quote_book import OwnBookState  # noqa: E402
from engine.strategies.market_making_v2 import MarketMakingV2Strategy  # noqa: E402


def test_mm2_spread_gate_pulls_quotes() -> None:
    strat = MarketMakingV2Strategy(
        Settings(
            binance_api_key="x",
            binance_api_secret="y",
            mm2_symbols=["BTCUSDT"],
            mm2_min_spread_bps=50.0,
            mm2_min_samples=1,
        )
    )
    strat.attach_own_book_provider(lambda _s: OwnBookState(symbol="BTCUSDT"))
    feat = {
        "BTCUSDT": Features(
            symbol="BTCUSDT",
            mid=100.0,
            spread_bps=2.0,
            micro_price=100.0,
        )
    }
    intents = strat.on_tick_quotes(feat)
    assert intents[0].bid_price is None
    assert intents[0].ask_price is None
    assert intents[0].reason == "mm2_spread_gate"


def test_mm2_spread_gate_uses_dynamic_quote_width() -> None:
    strat = MarketMakingV2Strategy(
        Settings(
            binance_api_key="x",
            binance_api_secret="y",
            mm2_symbols=["BTCUSDT"],
            mm2_min_spread_bps=0.0,
            mm2_min_skew_bps=0.0,
            mm2_min_samples=1,
            post_only_enabled=True,
            mm_quote_use_venue_spread_floor=True,
        )
    )
    strat.attach_own_book_provider(lambda _s: OwnBookState(symbol="BTCUSDT"))
    wide = {
        "BTCUSDT": Features(
            symbol="BTCUSDT",
            mid=100.0,
            spread_bps=14.0,
            micro_price=100.0,
        )
    }
    tight = {
        "BTCUSDT": Features(
            symbol="BTCUSDT",
            mid=100.0,
            spread_bps=5.0,
            micro_price=100.0,
        )
    }
    assert strat.on_tick_quotes(wide)[0].reason != "mm2_spread_gate"
    gated = strat.on_tick_quotes(tight)[0]
    assert gated.reason == "mm2_spread_gate"
    assert gated.venue_mid == 100.0


def test_mm2_skew_gate_pulls_quotes() -> None:
    strat = MarketMakingV2Strategy(
        Settings(
            binance_api_key="x",
            binance_api_secret="y",
            mm2_symbols=["BTCUSDT"],
            mm2_min_spread_bps=0.0,
            mm2_min_skew_bps=5.0,
            mm2_min_samples=1,
            mm2_skew_window_sec=300.0,
        )
    )
    strat.attach_own_book_provider(lambda _s: OwnBookState(symbol="BTCUSDT"))
    feat = {
        "BTCUSDT": Features(
            symbol="BTCUSDT",
            mid=100.0,
            spread_bps=20.0,
            micro_price=100.0,
        )
    }
    intents = strat.on_tick_quotes(feat)
    assert len(intents) == 1
    assert intents[0].bid_price is None
    assert intents[0].ask_price is None
    assert intents[0].reason == "mm2_skew_gate"
