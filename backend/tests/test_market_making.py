from __future__ import annotations

import os

os.environ.setdefault("BINANCE_API_KEY", "test")
os.environ.setdefault("BINANCE_API_SECRET", "test")

from common.config import Settings  # noqa: E402
from engine.market_data.feature_store import Features  # noqa: E402
from engine.market_data.own_quote_book import OwnBookState  # noqa: E402
from engine.strategies.market_making import MarketMakingStrategy  # noqa: E402


def _feat(mid: float = 100.0, micro: float = 100.0) -> dict[str, Features]:
    return {
        "BTCUSDT": Features(
            symbol="BTCUSDT",
            mid=mid,
            spread_bps=10.0,
            micro_price=micro,
            imbalance_topn=0.0,
            bid_hit_ratio=0.5,
            ask_hit_ratio=0.5,
            bid_depth_ratio=1.0,
            ask_depth_ratio=1.0,
        )
    }


def test_mm_on_tick_returns_no_signals() -> None:
    strat = MarketMakingStrategy(Settings(mm_symbols=["BTCUSDT"]))
    assert list(strat.on_tick(_feat())) == []


def test_mm_on_tick_quotes_posts_bid_and_ask() -> None:
    strat = MarketMakingStrategy(
        Settings(
            binance_api_key="x",
            binance_api_secret="y",
            mm_symbols=["BTCUSDT"],
            mm_min_samples=1,
            mm_quote_half_spread_bps=5.0,
        )
    )
    strat.attach_own_book_provider(lambda _s: OwnBookState(symbol="BTCUSDT"))
    intents = []
    for _ in range(3):
        intents = strat.on_tick_quotes(_feat())
        if intents and intents[0].bid_price and intents[0].ask_price:
            break
    assert len(intents) == 1
    assert intents[0].bid_price is not None
    assert intents[0].ask_price is not None
    assert intents[0].ask_price > intents[0].bid_price


def test_mm_manages_own_risk_when_enabled() -> None:
    strat = MarketMakingStrategy(Settings(mm_institutional_risk_enabled=True))
    assert strat.manages_own_risk() is True
