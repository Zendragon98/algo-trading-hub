"""ExecutionQualityGuard trips exec_quality on rolling slippage blowout."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("BINANCE_API_KEY", "test")
os.environ.setdefault("BINANCE_API_SECRET", "test")

from common.config import Settings  # noqa: E402
from common.enums import Side  # noqa: E402
from common.events import EventBus  # noqa: E402
from common.types import ParentOrder  # noqa: E402
from engine.execution.execution_metrics import ExecutionTracker  # noqa: E402
from engine.execution.quality_guard import ExecutionQualityGuard  # noqa: E402
from engine.risk.circuit_breaker import BreakerScope, CircuitBreaker  # noqa: E402


@pytest.mark.asyncio
async def test_exec_quality_trips_on_high_rolling_slippage() -> None:
    settings = Settings(
        binance_api_key="x",
        binance_api_secret="y",
        exec_quality_kill_bps=20.0,
        exec_quality_window=3,
    )
    bus = EventBus()
    breaker = CircuitBreaker(bus=bus)
    tracker = ExecutionTracker(bus=bus)
    guard = ExecutionQualityGuard.from_settings(settings, breaker, tracker)

    for i in range(3):
        parent = ParentOrder(
            id=f"p{i}",
            symbol="BTCUSDT",
            side=Side.BUY,
            qty=1.0,
        )
        tracker.on_parent_submit(parent, arrival_price=100.0)
        await tracker.on_fill(
            parent.id,
            Side.BUY,
            qty=1.0,
            venue_price=103.0,
            impact_bps=0.0,
        )
        await tracker.close_parent(parent.id)

    guard.evaluate()

    assert breaker.is_blocked(BreakerScope.ENGINE)
    codes = {s.code for s in breaker.active()}
    assert "exec_quality" in codes


@pytest.mark.asyncio
async def test_exec_quality_rearm_history_prevents_immediate_retrip() -> None:
    settings = Settings(
        binance_api_key="x",
        binance_api_secret="y",
        exec_quality_kill_bps=20.0,
        exec_quality_window=3,
    )
    bus = EventBus()
    breaker = CircuitBreaker(bus=bus)
    tracker = ExecutionTracker(bus=bus)
    guard = ExecutionQualityGuard.from_settings(settings, breaker, tracker)

    for i in range(3):
        parent = ParentOrder(
            id=f"p{i}",
            symbol="BTCUSDT",
            side=Side.BUY,
            qty=1.0,
        )
        tracker.on_parent_submit(parent, arrival_price=100.0)
        await tracker.on_fill(
            parent.id,
            Side.BUY,
            qty=1.0,
            venue_price=103.0,
            impact_bps=0.0,
        )
        await tracker.close_parent(parent.id)

    guard.evaluate()
    assert breaker.is_blocked(BreakerScope.ENGINE)
    breaker.rearm(code="exec_quality")
    tracker.clear_completed_history_after_rearm()
    guard.evaluate()
    assert not breaker.is_blocked(BreakerScope.ENGINE)
