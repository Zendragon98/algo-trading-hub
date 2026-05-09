from __future__ import annotations

import os

os.environ.setdefault("BINANCE_API_KEY", "test")
os.environ.setdefault("BINANCE_API_SECRET", "test")

from common.config import Settings  # noqa: E402
from common.enums import Side  # noqa: E402
from engine.market_data.feature_store import Features  # noqa: E402
from engine.strategies.market_making import MarketMakingStrategy  # noqa: E402


def _feat(
    mid: float,
    micro: float,
    imb: float,
    *,
    tape_bid_hits: int = 0,
    tape_ask_hits: int = 0,
) -> dict[str, Features]:
    return {
        "BTCUSDT": Features(
            symbol="BTCUSDT",
            mid=mid,
            spread_bps=10.0,
            micro_price=micro,
            imbalance_topn=imb,
            bid_hit_ratio=0.5,
            ask_hit_ratio=0.5,
            tape_bid_hit_count=tape_bid_hits,
            tape_ask_hit_count=tape_ask_hits,
        )
    }


def test_mm_fade_emits_buy_on_negative_composite() -> None:
    settings = Settings(
        binance_api_key="x",
        binance_api_secret="y",
        mm_symbols=["BTCUSDT"],
        mm_skew_window_sec=300.0,
        mm_skew_scale=1.0,
        mm_imbalance_scale=15.0,
        mm_entry_tilt=8.0,
        mm_signal_mode="fade",
        mm_min_samples=5,
        mm_qty=1.0,
        mm_cooldown_sec=0.0,
    )
    strat = MarketMakingStrategy(settings)
    # Negative skew + ask-heavy book -> strongly negative composite -> fade -> BUY
    f = _feat(mid=100.0, micro=99.5, imb=-0.5)
    sigs: list = []
    for _ in range(6):
        sigs = list(strat.on_tick(f))
        if sigs:
            break
    assert len(sigs) == 1
    assert sigs[0].side is Side.BUY
    assert sigs[0].symbol == "BTCUSDT"


def test_mm_fade_emits_sell_on_positive_composite() -> None:
    settings = Settings(
        binance_api_key="x",
        binance_api_secret="y",
        mm_symbols=["BTCUSDT"],
        mm_entry_tilt=5.0,
        mm_signal_mode="fade",
        mm_min_samples=5,
        mm_qty=1.0,
        mm_cooldown_sec=0.0,
    )
    strat = MarketMakingStrategy(settings)
    f = _feat(mid=100.0, micro=100.3, imb=0.6)
    sigs = []
    for _ in range(6):
        sigs = list(strat.on_tick(f))
        if sigs:
            break
    assert len(sigs) == 1
    assert sigs[0].side is Side.SELL


def test_mm_fade_tape_offer_lifts_drive_sell() -> None:
    """Many more offer lifts than bid hits => positive tape pressure => fade SELL."""
    settings = Settings(
        binance_api_key="x",
        binance_api_secret="y",
        mm_symbols=["BTCUSDT"],
        mm_skew_scale=0.0,
        mm_imbalance_scale=0.0,
        mm_tape_scale=20.0,
        mm_min_tape_trades=5,
        mm_entry_tilt=10.0,
        mm_signal_mode="fade",
        mm_min_samples=5,
        mm_qty=1.0,
        mm_cooldown_sec=0.0,
    )
    strat = MarketMakingStrategy(settings)
    f = _feat(100.0, 100.0, 0.0, tape_bid_hits=2, tape_ask_hits=10)
    sigs: list = []
    for _ in range(6):
        sigs = list(strat.on_tick(f))
        if sigs:
            break
    assert len(sigs) == 1
    assert sigs[0].side is Side.SELL
    assert "hits_ba=2/10" in sigs[0].reason


def test_mm_follow_mapping() -> None:
    settings = Settings(
        binance_api_key="x",
        binance_api_secret="y",
        mm_symbols=["BTCUSDT"],
        mm_entry_tilt=5.0,
        mm_signal_mode="follow",
        mm_min_samples=5,
        mm_qty=1.0,
        mm_cooldown_sec=0.0,
    )
    strat = MarketMakingStrategy(settings)
    f = _feat(mid=100.0, micro=100.3, imb=0.6)
    sigs = []
    for _ in range(6):
        sigs = list(strat.on_tick(f))
        if sigs:
            break
    assert sigs[0].side is Side.BUY
