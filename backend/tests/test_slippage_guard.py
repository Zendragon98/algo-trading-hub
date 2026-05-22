"""SlippageGuard: parent abort when realised VWAP blows past cap."""

from __future__ import annotations

import pytest

from common.enums import Side
from common.events import EventBus
from common.types import ParentOrder
from engine.execution.execution_metrics import ExecutionTracker
from engine.execution.slippage_guard import SlippageGuard
from engine.risk.circuit_breaker import BreakerScope, CircuitBreaker


def _parent(arrival: float = 100.0, max_slip_bps: float = 10.0) -> ParentOrder:
    return ParentOrder(
        id="P-1",
        symbol="BTCUSDT",
        side=Side.BUY,
        qty=1.0,
        max_slippage_bps=max_slip_bps,
    )


@pytest.mark.asyncio
async def test_no_abort_when_within_cap() -> None:
    bus = EventBus()
    tracker = ExecutionTracker(bus=bus)
    parent = _parent()
    tracker.on_parent_submit(parent, arrival_price=100.0)
    cancelled: list[str] = []

    async def _cancel(pid: str) -> None:
        cancelled.append(pid)

    breaker = CircuitBreaker()
    guard = SlippageGuard(breaker=breaker, tracker=tracker, cancel_parent=_cancel)
    # Fill at 100.05 (5 bps slippage) — well inside 10 bps cap.
    await tracker.on_fill(
        parent_id="P-1", side=Side.BUY, qty=0.1, venue_price=100.05, impact_bps=0.0,
    )
    await guard.on_fill("P-1", parent.max_slippage_bps)
    assert cancelled == []
    assert not breaker.is_blocked(BreakerScope.PARENT, "P-1")


@pytest.mark.asyncio
async def test_aborts_when_slippage_exceeds_cap() -> None:
    bus = EventBus()
    tracker = ExecutionTracker(bus=bus)
    parent = _parent(max_slip_bps=10.0)
    tracker.on_parent_submit(parent, arrival_price=100.0)
    cancelled: list[str] = []

    async def _cancel(pid: str) -> None:
        cancelled.append(pid)

    breaker = CircuitBreaker()
    guard = SlippageGuard(breaker=breaker, tracker=tracker, cancel_parent=_cancel)
    # Buy at 100.5 -> 50 bps slippage > 10 bps cap.
    await tracker.on_fill(
        parent_id="P-1", side=Side.BUY, qty=0.1, venue_price=100.5, impact_bps=0.0,
    )
    await guard.on_fill("P-1", parent.max_slippage_bps)
    assert cancelled == ["P-1"]
    assert breaker.is_blocked(BreakerScope.PARENT, "P-1")


@pytest.mark.asyncio
async def test_idempotent_after_first_abort() -> None:
    bus = EventBus()
    tracker = ExecutionTracker(bus=bus)
    parent = _parent(max_slip_bps=10.0)
    tracker.on_parent_submit(parent, arrival_price=100.0)
    cancelled: list[str] = []

    async def _cancel(pid: str) -> None:
        cancelled.append(pid)

    breaker = CircuitBreaker()
    guard = SlippageGuard(breaker=breaker, tracker=tracker, cancel_parent=_cancel)
    await tracker.on_fill(
        parent_id="P-1", side=Side.BUY, qty=0.1, venue_price=100.5, impact_bps=0.0,
    )
    await guard.on_fill("P-1", 10.0)
    await guard.on_fill("P-1", 10.0)  # second call must not re-cancel.
    assert cancelled == ["P-1"]
