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
