"""ExecutionTracker per-parent stats + completion lifecycle."""

from __future__ import annotations

import pytest

from common.enums import AlgoMode, EventType, Side
from common.events import EventBus
from common.types import ParentOrder
from engine.execution.execution_metrics import ExecutionTracker


def _parent(side: Side = Side.BUY, qty: float = 10.0) -> ParentOrder:
    return ParentOrder(
        id="P-test",
        symbol="BTCUSDT",
        side=side,
        qty=qty,
        algo_mode=AlgoMode.NORMAL,
    )


@pytest.mark.asyncio
async def test_partial_then_complete_emits_execution_event() -> None:
    bus = EventBus()
    tracker = ExecutionTracker(bus=bus)

    parent = _parent()
    tracker.on_parent_submit(parent, arrival_price=100.0)
    assert len(tracker.open_reports()) == 1

    async with bus.subscribe(types=[EventType.PARENT_UPDATE, EventType.EXECUTION_REPORT]) as q:
        await tracker.on_fill(parent.id, Side.BUY, qty=4.0, venue_price=101.0, impact_bps=2.0)
        progress = await q.get()
        assert progress.type is EventType.PARENT_UPDATE
        assert progress.payload["fill_ratio"] == pytest.approx(0.4)

        await tracker.on_fill(parent.id, Side.BUY, qty=6.0, venue_price=102.0, impact_bps=3.0)
        completed = await q.get()
        assert completed.type is EventType.EXECUTION_REPORT
        report = completed.payload
        # qty-weighted vwap = (101*4 + 102*6) / 10 = 101.6
        assert report["vwap_price"] == pytest.approx(101.6)
        # Buy paying 101.6 vs 100 arrival = +160 bps slippage.
        assert report["slippage_bps"] == pytest.approx(160.0, rel=1e-6)
        # Impact qty-weighted: (2*4 + 3*6) / 10 = 2.6 bps.
        assert report["impact_bps"] == pytest.approx(2.6, rel=1e-6)
        assert report["fill_ratio"] == pytest.approx(1.0)

    assert tracker.open_reports() == []
    assert len(tracker.history()) == 1


@pytest.mark.asyncio
async def test_sell_slippage_sign_convention() -> None:
    """A sell receiving less than arrival should report positive slippage."""
    bus = EventBus()
    tracker = ExecutionTracker(bus=bus)
    parent = _parent(side=Side.SELL, qty=1.0)
    tracker.on_parent_submit(parent, arrival_price=100.0)

    await tracker.on_fill(parent.id, Side.SELL, qty=1.0, venue_price=99.5, impact_bps=0.0)

    history = tracker.history()
    assert len(history) == 1
    # Sell at 99.5 vs 100.0 arrival = receiving 50 bps less = +50 bps adverse.
    assert history[0].slippage_bps == pytest.approx(50.0, rel=1e-6)


@pytest.mark.asyncio
async def test_close_parent_force_completes() -> None:
    bus = EventBus()
    tracker = ExecutionTracker(bus=bus)
    parent = _parent(qty=10.0)
    tracker.on_parent_submit(parent, arrival_price=100.0)

    await tracker.on_fill(parent.id, Side.BUY, qty=2.0, venue_price=101.0, impact_bps=0.0)
    await tracker.close_parent(parent.id)

    assert tracker.open_reports() == []
    history = tracker.history()
    assert len(history) == 1
    assert history[0].fill_ratio == pytest.approx(0.2)


@pytest.mark.asyncio
async def test_netted_contributions_persisted_on_submit() -> None:
    bus = EventBus()
    tracker = ExecutionTracker(bus=bus)
    parent = ParentOrder(
        id="P-net",
        symbol="ETHUSDT",
        side=Side.BUY,
        qty=0.01,
        algo_mode=AlgoMode.NORMAL,
        strategy_name="__netted__",
    )
    contribs = {"flow_momentum": 0.01, "sma_crossover": -0.005}
    report = tracker.on_parent_submit(
        parent,
        arrival_price=3000.0,
        strategy_contributions=contribs,
    )
    assert report.strategy_contributions == contribs
    assert tracker.open_reports()[0].strategy_contributions == contribs


@pytest.mark.asyncio
async def test_aggregate_summarises_history() -> None:
    bus = EventBus()
    tracker = ExecutionTracker(bus=bus)
    for i in range(3):
        parent = ParentOrder(
            id=f"P-{i}", symbol="BTCUSDT", side=Side.BUY, qty=1.0,
            algo_mode=AlgoMode.NORMAL,
        )
        tracker.on_parent_submit(parent, arrival_price=100.0)
        await tracker.on_fill(parent.id, Side.BUY, qty=1.0, venue_price=100.5, impact_bps=1.0)

    agg = tracker.aggregate()
    assert agg["count"] == 3
    assert agg["avg_fill_ratio"] == pytest.approx(1.0)
    assert agg["avg_impact_bps"] == pytest.approx(1.0)
    # Buy at 100.5 vs 100.0 arrival = +50 bps slippage on every parent.
    assert agg["avg_slippage_bps"] == pytest.approx(50.0, rel=1e-6)
