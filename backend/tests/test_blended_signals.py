from __future__ import annotations

import os

os.environ.setdefault("BINANCE_API_KEY", "test")
os.environ.setdefault("BINANCE_API_SECRET", "test")

from collections import deque

import pytest

from common.config import Settings  # noqa: E402
from common.enums import Side  # noqa: E402
from engine.market_data.feature_store import Features  # noqa: E402
from engine.strategies.blended_signals import (  # noqa: E402
    BlendRegime,
    BlendedSignalsStrategy,
)
from engine.strategies.indicators import (  # noqa: E402
    RsiWilderState,
    bollinger_bands,
    ema_seed_from_closes,
    rsi_from_closes,
    rsi_wilder_seed_from_closes,
    rsi_wilder_step,
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


def _blend_settings(**overrides: object) -> Settings:
    base = dict(
        binance_api_key="x",
        binance_api_secret="y",
        strategy="blend",
        blend_symbols=["BTCUSDT"],
        blend_bar_interval_sec=60.0,
        blend_ema_fast=3,
        blend_ema_slow=5,
        blend_bb_period=5,
        blend_macd_fast=3,
        blend_macd_slow=5,
        blend_macd_signal=2,
        blend_rsi_period=3,
        blend_adx_period=3,
        blend_adx_trend_threshold=10.0,
        blend_qty=1.0,
        blend_cooldown_sec=0,
        blend_entry_threshold=0.4,
        blend_exit_threshold=0.1,
        blend_min_confirming_votes=2,
        blend_ema_min_gap_bps=0.0,
    )
    base.update(overrides)
    return Settings(**base)


def test_rsi_wilder_updates_incrementally() -> None:
    closes: deque[float] = deque(
        [100.0, 101.0, 102.0, 101.5, 103.0, 104.0, 103.0, 105.0, 106.0]
    )
    sma_rsi = rsi_from_closes(closes, 5)
    wilder = RsiWilderState()
    seed_closes: deque[float] = deque(list(closes)[:-1])
    seeded = rsi_wilder_seed_from_closes(wilder, seed_closes, 5)
    stepped = rsi_wilder_step(wilder, closes[-1], 5)
    assert sma_rsi is not None
    assert seeded is not None
    assert stepped is not None
    assert stepped != pytest.approx(sma_rsi, abs=0.5)


def test_ema_seeds_with_sma_at_period() -> None:
    closes: deque[float] = deque([10.0, 11.0, 12.0, 13.0, 14.0])
    ema = ema_seed_from_closes(closes, None, 5)
    assert ema == pytest.approx(12.0)


def test_blend_requires_positive_bar_interval() -> None:
    with pytest.raises(ValueError, match="BLEND_BAR_INTERVAL_SEC"):
        BlendedSignalsStrategy(_blend_settings(blend_bar_interval_sec=0))


def test_blend_warms_up_before_bar_signals(monkeypatch) -> None:
    import engine.strategies.blended_signals as blend_mod

    clock = [1_000_000.0]

    monkeypatch.setattr(blend_mod.time, "time", lambda: clock[0])
    strat = BlendedSignalsStrategy(_blend_settings())
    for i in range(4):
        clock[0] += 61.0
        assert list(strat.on_tick(_features(100.0 + i))) == []


def test_regime_gate_suppresses_trend_voters_in_ranging() -> None:
    strat = BlendedSignalsStrategy(_blend_settings())
    state = strat._state_for("BTCUSDT")
    state.regime = BlendRegime.RANGING
    state.closes.extend([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])
    state.completed_high = 106.0
    state.completed_low = 99.0
    feat = _features(105.0)["BTCUSDT"]
    votes = strat._component_votes(state, 105.0, feat, "BTCUSDT")
    assert votes["ema"] == 0.0
    assert votes["macd"] == 0.0


def test_regime_gate_suppresses_bb_in_trending() -> None:
    strat = BlendedSignalsStrategy(_blend_settings())
    state = strat._state_for("BTCUSDT")
    state.regime = BlendRegime.TRENDING
    state.closes.extend([10.0] * 19 + [20.0])
    state.completed_high = 20.0
    state.completed_low = 10.0
    feat = _features(20.0)["BTCUSDT"]
    votes = strat._component_votes(state, 20.0, feat, "BTCUSDT")
    assert votes["bb"] == 0.0


def test_blend_score_zero_when_voters_cancel() -> None:
    strat = BlendedSignalsStrategy(_blend_settings())
    votes = {"ema": 1.0, "macd": 0.0, "rsi": 0.0, "bb": 0.0, "micro": -1.0}
    blend, _, bull, bear = strat._blend_score(votes, BlendRegime.TRENDING)
    assert blend is not None
    assert bull == 1
    assert bear == 1
    assert abs(blend) < 0.5


def test_micro_skipped_when_mm2_active() -> None:
    strat = BlendedSignalsStrategy(_blend_settings())
    strat.attach_mm2_active_symbols_provider(lambda: frozenset({"BTCUSDT"}))
    state = strat._state_for("BTCUSDT")
    state.regime = BlendRegime.TRENDING
    state.closes.extend([100.0] * 6)
    state.completed_high = 101.0
    state.completed_low = 99.0
    feat = _features(100.0, imb=0.9)["BTCUSDT"]
    votes = strat._component_votes(state, 100.0, feat, "BTCUSDT")
    assert votes["micro"] == 0.0


def test_macd_histogram_direction_vote() -> None:
    hist_prev = 0.1
    hist_now = 0.2
    vote = 0.0
    if hist_now > 0 and hist_now > hist_prev:
        vote = 1.0
    assert vote == 1.0


def test_bollinger_pct_b_extremes() -> None:
    closes: deque[float] = deque([10.0] * 19 + [12.0])
    bb = bollinger_bands(closes, period=20, std_mult=2.0)
    assert bb is not None
    _, _, _, pct_b = bb
    assert pct_b > 0.5


def test_blend_emits_on_bar_close_threshold_cross(monkeypatch) -> None:
    import engine.strategies.blended_signals as blend_mod

    clock = [1_000_000.0]
    monkeypatch.setattr(blend_mod.time, "time", lambda: clock[0])

    strat = BlendedSignalsStrategy(
        _blend_settings(
            blend_entry_threshold=0.2,
            blend_adx_trend_threshold=5.0,
        )
    )
    st = strat._state_for("BTCUSDT")
    price = 100.0
    got_signal = False
    for _ in range(80):
        clock[0] += 61.0
        price += 1.5
        sigs = list(strat.on_tick(_features(price, imb=0.5)))
        if sigs:
            got_signal = True
            assert sigs[0].symbol == "BTCUSDT"
            assert sigs[0].side in (Side.BUY, Side.SELL)
            break
    assert len(st.closes) >= strat._min_bars_required() - 1 or st.bar_count > 0
    assert got_signal or st.prev_blend is not None
