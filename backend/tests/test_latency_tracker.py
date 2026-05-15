"""LatencyTracker histogram emission."""

from __future__ import annotations

import asyncio

import pytest

from common.enums import EventType  # noqa: E402
from common.events import EventBus  # noqa: E402
from engine.observability.latency_tracker import LatencyTracker  # noqa: E402


@pytest.mark.asyncio
async def test_emits_status_with_metrics() -> None:
    bus = EventBus()
    tracker = LatencyTracker(bus=bus)

    async with bus.subscribe(types=[EventType.STATUS]) as q:
        tracker.on_tick("BTCUSDT")
        tracker.on_signal("BTCUSDT")
        tracker.on_risk_passed("BTCUSDT")
        tracker.on_child_submitted("BTCUSDT")
        tracker.on_venue_ack("BTCUSDT")
        await tracker.maybe_emit(interval_sec=0.0)

        deadline = asyncio.get_event_loop().time() + 2.0
        while asyncio.get_event_loop().time() < deadline:
            ev = await asyncio.wait_for(q.get(), timeout=1.0)
            if ev.payload.get("kind") == "latency_metrics":
                assert "tick_to_ack_ms" in ev.payload
                assert "signal_to_risk_ms" in ev.payload
                assert "risk_to_submit_ms" in ev.payload
                return
    pytest.fail("latency_metrics STATUS not received")
