"""SubmitGuard: repeat-reject pause, open-parent cap, throttle."""

from __future__ import annotations

import time

import pytest

from common.enums import OrderStatus
from engine.execution.submit_guard import SubmitGuard
from engine.risk.circuit_breaker import BreakerScope, CircuitBreaker


def _guard(
    open_count: int = 0,
    *,
    max_open_parents: int = 8,
    submit_rate_per_sec: float = 1000.0,
    max_consecutive_rejects: int = 3,
    reject_cooldown_sec: float = 0.05,
) -> tuple[SubmitGuard, CircuitBreaker]:
    breaker = CircuitBreaker()
    guard = SubmitGuard(
        breaker=breaker,
        open_parent_count=lambda: open_count,
        max_open_parents=max_open_parents,
        submit_rate_per_sec=submit_rate_per_sec,
        max_consecutive_rejects=max_consecutive_rejects,
        reject_cooldown_sec=reject_cooldown_sec,
    )
    return guard, breaker


def test_can_submit_parent_blocks_on_engine_breaker() -> None:
    guard, breaker = _guard()
    from engine.risk.circuit_breaker import Breach, BreakerSeverity

    breaker.trip(Breach(
        code="kill", scope=BreakerScope.ENGINE, severity=BreakerSeverity.MAJOR,
    ))
    ok, reason = guard.can_submit_parent("BTCUSDT")
    assert not ok
    assert reason == "engine_breaker"


def test_can_submit_parent_blocks_on_open_cap() -> None:
    guard, _ = _guard(open_count=8, max_open_parents=8)
    ok, reason = guard.can_submit_parent("BTCUSDT")
    assert not ok
    assert reason == "max_open_parents"


def test_repeat_rejects_trip_symbol_breaker() -> None:
    guard, breaker = _guard(max_consecutive_rejects=3, reject_cooldown_sec=0.05)
    for _ in range(3):
        guard.record_status("BTCUSDT", OrderStatus.REJECTED)
    assert breaker.is_blocked(BreakerScope.SYMBOL, "BTCUSDT")
    # Cooldown elapses -> tick clears.
    time.sleep(0.06)
    breaker.tick()
    assert not breaker.is_blocked(BreakerScope.SYMBOL, "BTCUSDT")


def test_successful_submit_resets_streak() -> None:
    guard, breaker = _guard(max_consecutive_rejects=3)
    guard.record_status("BTCUSDT", OrderStatus.REJECTED)
    guard.record_status("BTCUSDT", OrderStatus.REJECTED)
    guard.record_status("BTCUSDT", OrderStatus.ACK)
    guard.record_status("BTCUSDT", OrderStatus.REJECTED)
    # Streak was reset to 0; we're at 1, no breach yet.
    assert not breaker.is_blocked(BreakerScope.SYMBOL, "BTCUSDT")


@pytest.mark.asyncio
async def test_reduce_only_bypasses_breakers() -> None:
    guard, breaker = _guard()
    from engine.risk.circuit_breaker import Breach, BreakerSeverity

    breaker.trip(Breach(
        code="kill", scope=BreakerScope.ENGINE, severity=BreakerSeverity.MAJOR,
    ))
    ok, _ = await guard.gate_child("BTCUSDT", reduce_only=True)
    assert ok


@pytest.mark.asyncio
async def test_throttle_delays_burst_submits() -> None:
    # 2 tokens per second; capacity ~2; 5 quick submits should take >=1.5s
    # (first 2 free, then ~0.5s per token).
    guard, _ = _guard(submit_rate_per_sec=2.0)
    start = time.monotonic()
    for _ in range(5):
        ok, _ = await guard.gate_child("BTCUSDT", reduce_only=False)
        assert ok
    elapsed = time.monotonic() - start
    assert elapsed >= 1.0
