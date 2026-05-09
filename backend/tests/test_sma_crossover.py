from __future__ import annotations

import os

os.environ.setdefault("BINANCE_API_KEY", "test")
os.environ.setdefault("BINANCE_API_SECRET", "test")

from common.config import Settings  # noqa: E402
from common.enums import Side  # noqa: E402
from engine.market_data.feature_store import Features  # noqa: E402
from engine.strategies.sma_crossover import SmaCrossoverStrategy  # noqa: E402


def _features(mid: float) -> dict[str, Features]:
    return {
        "BTCUSDT": Features(
            symbol="BTCUSDT",
            mid=mid,
            spread_bps=1.0,
            micro_price=mid,
            imbalance_topn=0.0,
            bid_hit_ratio=0.5,
            ask_hit_ratio=0.5,
        )
    }


def test_sma_emits_only_on_crossovers() -> None:
    settings = Settings(
        binance_api_key="x",
        binance_api_secret="y",
        strategy="sma",
        sma_symbol="BTCUSDT",
        sma_fast_window=3,
        sma_slow_window=5,
        sma_qty=1.0,
        sma_cooldown_sec=0,
    )
    strat = SmaCrossoverStrategy(settings)

    # Warm up with flat prices: no signal.
    for _ in range(10):
        assert list(strat.on_tick(_features(100.0))) == []

    # Push a down move then up move to force a cross.
    for mid in (99.0, 98.0, 97.0, 110.0, 111.0, 112.0):
        sigs = list(strat.on_tick(_features(mid)))
        if sigs:
            assert len(sigs) == 1
            assert sigs[0].symbol == "BTCUSDT"
            assert sigs[0].side in (Side.BUY, Side.SELL)
            break

    # After the cross, stable up prices shouldn't emit repeatedly.
    for _ in range(10):
        assert list(strat.on_tick(_features(112.0))) == []

