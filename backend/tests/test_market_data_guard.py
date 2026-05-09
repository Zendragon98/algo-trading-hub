"""MarketDataGuard: stale-tick + wide-spread vetoes."""

from __future__ import annotations

import time

from engine.risk.market_data_guard import MarketDataGuard


def test_no_breach_when_data_is_fresh_and_tight() -> None:
    g = MarketDataGuard(max_tick_age_sec=5.0, max_entry_spread_bps=25.0, cooldown_sec=10.0)
    breach = g.evaluate(symbol="BTCUSDT", tick_ts=time.time(), spread_bps=2.0)
    assert breach is None


def test_stale_tick_trips_breach() -> None:
    g = MarketDataGuard(max_tick_age_sec=2.0, max_entry_spread_bps=25.0, cooldown_sec=10.0)
    breach = g.evaluate(symbol="BTCUSDT", tick_ts=time.time() - 10, spread_bps=2.0)
    assert breach is not None
    assert breach.code == "stale_tick"
    assert breach.target == "BTCUSDT"


def test_wide_spread_trips_breach() -> None:
    g = MarketDataGuard(max_tick_age_sec=5.0, max_entry_spread_bps=10.0, cooldown_sec=10.0)
    breach = g.evaluate(symbol="ETHUSDT", tick_ts=time.time(), spread_bps=50.0)
    assert breach is not None
    assert breach.code == "wide_spread"
    assert breach.target == "ETHUSDT"


def test_missing_inputs_do_not_fire() -> None:
    g = MarketDataGuard(max_tick_age_sec=5.0, max_entry_spread_bps=10.0, cooldown_sec=10.0)
    # Cold-start symbol: no tick_ts and no spread yet -> never fires.
    assert g.evaluate(symbol="BTCUSDT", tick_ts=None, spread_bps=None) is None


def test_disabled_thresholds_do_not_fire() -> None:
    g = MarketDataGuard(max_tick_age_sec=0.0, max_entry_spread_bps=0.0, cooldown_sec=10.0)
    assert g.evaluate(symbol="BTCUSDT", tick_ts=time.time() - 9999, spread_bps=9999) is None
