from __future__ import annotations

import os

os.environ.setdefault("BINANCE_API_KEY", "test")
os.environ.setdefault("BINANCE_API_SECRET", "test")

from common.config import Settings  # noqa: E402
from common.enums import Side  # noqa: E402
from engine.market_data.feature_store import Features  # noqa: E402
from engine.strategies import sma_crossover as sma_mod  # noqa: E402
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


def test_sma_bar_interval_samples_once_per_bar(monkeypatch) -> None:
    """Bar mode appends one close per interval; windows count bars not heartbeats."""
    clock = [1_000_000.0]

    def fake_time() -> float:
        return clock[0]

    monkeypatch.setattr(sma_mod.time, "time", fake_time)

    settings = Settings(
        binance_api_key="x",
        binance_api_secret="y",
        strategy="sma",
        sma_symbol="BTCUSDT",
        sma_bar_interval_sec=100.0,
        sma_fast_window=2,
        sma_slow_window=3,
        sma_qty=1.0,
        sma_cooldown_sec=0,
    )
    strat = SmaCrossoverStrategy(settings)
    st = strat._state_for("BTCUSDT")

    # Same bucket: no deque growth.
    assert list(strat.on_tick(_features(100.0))) == []
    assert len(st.mids) == 0
    clock[0] += 50.0
    assert list(strat.on_tick(_features(100.5))) == []
    assert len(st.mids) == 0

    # Cross into next bar: one close (last mid of previous bar).
    clock[0] += 55.0
    assert list(strat.on_tick(_features(101.0))) == []
    assert len(st.mids) == 1
    assert st.mids[-1] == 100.5

    clock[0] += 100.0
    assert list(strat.on_tick(_features(102.0))) == []
    assert len(st.mids) == 2

    clock[0] += 100.0
    assert list(strat.on_tick(_features(80.0))) == []
    assert len(st.mids) == 3
    assert st.mids[-1] == 102.0

    # Warm-up complete: downward cross should emit SELL (was long from earlier crosses).
    clock[0] += 100.0
    sigs = list(strat.on_tick(_features(50.0)))
    assert len(st.mids) == 3
    assert len(sigs) == 1
    assert sigs[0].side is Side.SELL


def test_sma_closes_long_before_short_entry() -> None:
    """A cross-down while long must reduce-only close before opening short."""
    settings = Settings(
        binance_api_key="x",
        binance_api_secret="y",
        sma_symbol="BTCUSDT",
        sma_fast_window=3,
        sma_slow_window=5,
        sma_qty=1.0,
        sma_cooldown_sec=0,
    )
    strat = SmaCrossoverStrategy(settings)
    strat.attach_position_provider(lambda _sym: 0.05)

    # Uptrend arms fast>slow, then downtrend crosses down while still long.
    path = [90.0, 91.0, 92.0, 93.0, 94.0, 110.0, 111.0, 112.0, 100.0, 99.0, 98.0, 97.0]
    for mid in path:
        sigs = list(strat.on_tick(_features(mid)))
        if sigs:
            assert len(sigs) == 1
            assert sigs[0].reduce_only is True
            assert sigs[0].side is Side.SELL
            assert sigs[0].qty == 0.05
            return
    raise AssertionError("expected a close signal on cross down while long")

