"""ConnectionMonitor: stale market-data / user-data auto-pause."""

from __future__ import annotations

import time

from engine.core.connection_monitor import ConnectionMonitor
from engine.risk.circuit_breaker import BreakerScope, CircuitBreaker


def test_fresh_data_does_not_trip() -> None:
    breaker = CircuitBreaker()
    cm = ConnectionMonitor(breaker=breaker, ws_stale_pause_sec=5.0, cooldown_sec=10.0)
    now = time.time()
    cm.evaluate(
        now=now, last_tick_ts=now - 1.0, last_user_data_ts=now - 1.0,
        engine_running=True,
    )
    assert not breaker.is_blocked(BreakerScope.ENGINE)


def test_stale_market_data_trips_minor_engine_breach() -> None:
    breaker = CircuitBreaker()
    cm = ConnectionMonitor(breaker=breaker, ws_stale_pause_sec=2.0, cooldown_sec=0.05)
    now = time.time()
    cm.evaluate(
        now=now, last_tick_ts=now - 10.0, last_user_data_ts=0.0,
        engine_running=True,
    )
    assert breaker.is_blocked(BreakerScope.ENGINE)
    # Cooldown elapses -> breach clears.
    time.sleep(0.06)
    breaker.tick()
    assert not breaker.is_blocked(BreakerScope.ENGINE)


def test_zero_user_data_ts_does_not_trip() -> None:
    """Before any user-data event has landed, last_user_data_ts is 0
    and must NOT be treated as 'stale'."""
    breaker = CircuitBreaker()
    cm = ConnectionMonitor(breaker=breaker, ws_stale_pause_sec=2.0, cooldown_sec=10.0)
    now = time.time()
    cm.evaluate(
        now=now, last_tick_ts=now - 0.5, last_user_data_ts=0.0,
        engine_running=True,
    )
    assert not breaker.is_blocked(BreakerScope.ENGINE)


def test_paused_engine_skips_evaluation() -> None:
    breaker = CircuitBreaker()
    cm = ConnectionMonitor(breaker=breaker, ws_stale_pause_sec=1.0, cooldown_sec=10.0)
    now = time.time()
    cm.evaluate(
        now=now, last_tick_ts=now - 60.0, last_user_data_ts=now - 60.0,
        engine_running=False,
    )
    assert not breaker.is_blocked(BreakerScope.ENGINE)
