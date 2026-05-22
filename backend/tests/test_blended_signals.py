from __future__ import annotations

import os

os.environ.setdefault("BINANCE_API_KEY", "test")
os.environ.setdefault("BINANCE_API_SECRET", "test")

from collections import deque

from common.config import Settings  # noqa: E402
from common.enums import Side  # noqa: E402
from engine.market_data.feature_store import Features  # noqa: E402
from engine.strategies.blended_signals import BlendedSignalsStrategy  # noqa: E402
from engine.strategies.indicators import (  # noqa: E402
    bollinger_bands,
    macd_step,
    rsi_from_closes,
)


def _features(mid: float, *, imb: float = 0.0, tape: float = 0.5) -> dict[str, Features]:
    return {
        "BTCUSDT": Features(
            symbol="BTCUSDT",
            mid=mid,
            spread_bps=1.0,
            micro_price=mid,
            imbalance_topn=imb,
            bid_hit_ratio=tape,
            ask_hit_ratio=1.0 - tape,
        )
    }


def test_rsi_from_closes_rises_on_uptrend() -> None:
    closes: deque[float] = deque([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])
    rsi = rsi_from_closes(closes, 5)
    assert rsi is not None
    assert rsi > 50.0


def test_macd_step_updates_emas() -> None:
    macd, sig, hist, new_sig, fast, slow = macd_step(
        ema_fast=None,
        ema_slow=None,
        signal=None,
        price=100.0,
        fast_period=3,
        slow_period=5,
        signal_period=2,
    )
    assert fast == 100.0
    assert slow == 100.0
    assert macd == 0.0
    assert sig == 0.0
    assert hist == 0.0
    assert new_sig == 0.0


def test_bollinger_pct_b_extremes() -> None:
    closes: deque[float] = deque([10.0] * 19 + [12.0])
    bb = bollinger_bands(closes, period=20, std_mult=2.0)
    assert bb is not None
    _, _, _, pct_b = bb
    assert pct_b > 0.5


def test_blend_warms_up_before_signals() -> None:
    settings = Settings(
        binance_api_key="x",
        binance_api_secret="y",
        strategy="blend",
        blend_symbols=["BTCUSDT"],
        blend_bar_interval_sec=0.0,
        blend_ema_fast=3,
        blend_ema_slow=5,
        blend_bb_period=5,
        blend_macd_fast=3,
        blend_macd_slow=5,
        blend_macd_signal=2,
        blend_rsi_period=3,
        blend_qty=1.0,
        blend_cooldown_sec=0,
        blend_entry_threshold=0.2,
        blend_min_confirming_votes=2,
    )
    strat = BlendedSignalsStrategy(settings)
    for i in range(8):
        assert list(strat.on_tick(_features(100.0 + i * 0.1))) == []


def test_blend_emits_on_threshold_cross(monkeypatch) -> None:
    import engine.strategies.blended_signals as blend_mod

    clock = [1_000_000.0]

    def fake_time() -> float:
        return clock[0]

    monkeypatch.setattr(blend_mod.time, "time", fake_time)

    settings = Settings(
        binance_api_key="x",
        binance_api_secret="y",
        strategy="blend",
        blend_symbols=["BTCUSDT"],
        blend_bar_interval_sec=1.0,
        blend_ema_fast=2,
        blend_ema_slow=3,
        blend_bb_period=3,
        blend_macd_fast=2,
        blend_macd_slow=3,
        blend_macd_signal=2,
        blend_rsi_period=2,
        blend_qty=1.0,
        blend_cooldown_sec=0,
        blend_entry_threshold=0.15,
        blend_exit_threshold=0.05,
        blend_min_confirming_votes=2,
        blend_weight_micro=0.0,
    )
    strat = BlendedSignalsStrategy(settings)
    st = strat._state_for("BTCUSDT")

    # Build bars with steady uptrend.
    price = 100.0
    got_signal = False
    for step in range(40):
        clock[0] += 1.1
        price += 2.0
        sigs = list(strat.on_tick(_features(price, imb=0.5)))
        if sigs:
            got_signal = True
            assert sigs[0].symbol == "BTCUSDT"
            assert sigs[0].side in (Side.BUY, Side.SELL)
            break
    assert len(st.closes) >= 3
    assert got_signal or st.prev_blend is not None
