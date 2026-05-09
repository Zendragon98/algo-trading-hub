"""MarketDataGuard: stale-tick + wide-spread vetoes."""

from __future__ import annotations

import time

from engine.risk.market_data_guard import MarketDataGuard


def _static(**kw: float | bool) -> MarketDataGuard:
    return MarketDataGuard(
        max_tick_age_sec=float(kw.get("max_tick_age_sec", 5.0)),
        max_entry_spread_bps=float(kw.get("max_entry_spread_bps", 25.0)),
        cooldown_sec=float(kw.get("cooldown_sec", 10.0)),
        spread_dynamic_enabled=False,
    )


def test_no_breach_when_data_is_fresh_and_tight() -> None:
    g = _static()
    breach = g.evaluate(symbol="BTCUSDT", tick_ts=time.time(), spread_bps=2.0)
    assert breach is None


def test_stale_tick_trips_breach() -> None:
    g = MarketDataGuard(
        max_tick_age_sec=2.0,
        max_entry_spread_bps=25.0,
        cooldown_sec=10.0,
        spread_dynamic_enabled=False,
    )
    breach = g.evaluate(symbol="BTCUSDT", tick_ts=time.time() - 10, spread_bps=2.0)
    assert breach is not None
    assert breach.code == "stale_tick"
    assert breach.target == "BTCUSDT"


def test_wide_spread_trips_breach_static() -> None:
    g = MarketDataGuard(
        max_tick_age_sec=5.0,
        max_entry_spread_bps=10.0,
        cooldown_sec=10.0,
        spread_dynamic_enabled=False,
    )
    breach = g.evaluate(symbol="ETHUSDT", tick_ts=time.time(), spread_bps=50.0)
    assert breach is not None
    assert breach.code == "wide_spread"
    assert breach.target == "ETHUSDT"


def test_missing_inputs_do_not_fire() -> None:
    g = _static(max_entry_spread_bps=10.0)
    # Cold-start symbol: no tick_ts and no spread yet -> never fires.
    assert g.evaluate(symbol="BTCUSDT", tick_ts=None, spread_bps=None) is None


def test_disabled_thresholds_do_not_fire() -> None:
    g = MarketDataGuard(
        max_tick_age_sec=0.0,
        max_entry_spread_bps=0.0,
        cooldown_sec=10.0,
        spread_dynamic_enabled=False,
    )
    assert g.evaluate(symbol="BTCUSDT", tick_ts=time.time() - 9999, spread_bps=9999) is None


def test_dynamic_allows_typical_alt_spread_after_warmup() -> None:
    """Baseline EWMA tracks ~40 bps; 35 bps quote should pass."""
    g = MarketDataGuard(
        max_tick_age_sec=5.0,
        max_entry_spread_bps=25.0,
        cooldown_sec=10.0,
        spread_dynamic_enabled=True,
        spread_baseline_alpha=0.2,
        spread_wide_multiplier=2.5,
        spread_wide_floor_bps=8.0,
        spread_wide_ceiling_bps=400.0,
    )
    sym = "MEMEUSDT"
    now = time.time()
    for _ in range(15):
        assert g.evaluate(symbol=sym, tick_ts=now, spread_bps=40.0) is None
    assert g.evaluate(symbol=sym, tick_ts=now, spread_bps=35.0) is None


def test_dynamic_trips_spike_vs_baseline() -> None:
    """Stable ~10 bps EWMA -> threshold ~25; a 60 bps print trips."""
    g = MarketDataGuard(
        max_tick_age_sec=5.0,
        max_entry_spread_bps=25.0,
        cooldown_sec=10.0,
        spread_dynamic_enabled=True,
        spread_baseline_alpha=0.15,
        spread_wide_multiplier=2.5,
        spread_wide_floor_bps=8.0,
        spread_wide_ceiling_bps=400.0,
    )
    sym = "BTCUSDT"
    now = time.time()
    for _ in range(20):
        assert g.evaluate(symbol=sym, tick_ts=now, spread_bps=10.0) is None
    breach = g.evaluate(symbol=sym, tick_ts=now, spread_bps=60.0)
    assert breach is not None
    assert breach.code == "wide_spread"
    assert breach.target == sym


def test_dynamic_hard_ceiling() -> None:
    g = MarketDataGuard(
        max_tick_age_sec=5.0,
        max_entry_spread_bps=25.0,
        cooldown_sec=10.0,
        spread_dynamic_enabled=True,
        spread_baseline_alpha=0.5,
        spread_wide_multiplier=10.0,
        spread_wide_floor_bps=1.0,
        spread_wide_ceiling_bps=100.0,
    )
    breach = g.evaluate(symbol="WEIRDUSDT", tick_ts=time.time(), spread_bps=500.0)
    assert breach is not None
    assert breach.code == "wide_spread"
