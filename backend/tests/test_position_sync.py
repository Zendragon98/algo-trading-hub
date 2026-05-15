from __future__ import annotations

import pytest

from common.events import EventBus
from common.types import Position
from engine.position.position_tracker import PositionTracker


@pytest.mark.asyncio
async def test_sync_from_venue_drops_missing_symbols() -> None:
    bus = EventBus()
    tracker = PositionTracker(bus)
    tracker.seed(
        [
            Position(symbol="BTCUSDT", qty=1.0),
            Position(symbol="ETHUSDT", qty=2.0),
        ]
    )
    await tracker.sync_from_venue([Position(symbol="BTCUSDT", qty=0.5)])
    assert tracker.get("ETHUSDT") is None
    assert tracker.get("BTCUSDT") is not None
    assert tracker.get("BTCUSDT").qty == 0.5
