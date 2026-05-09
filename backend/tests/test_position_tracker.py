"""PositionTracker fold-fill semantics: weighted entry, realised PnL, flips."""

from __future__ import annotations

import pytest

from common.enums import Side
from common.events import EventBus
from common.types import Fill
from engine.position.position_tracker import PositionTracker


@pytest.mark.asyncio
async def test_open_long_then_partial_close_realises_pnl() -> None:
    bus = EventBus()
    tracker = PositionTracker(bus)

    await tracker.on_fill(Fill(child_id="c1", parent_id=None, symbol="BTCUSDT",
                               side=Side.BUY, qty=1.0, price=100.0, fee=0.0, fee_asset="USDT"))
    pos = tracker.get("BTCUSDT")
    assert pos is not None
    assert pos.qty == 1.0
    assert pos.avg_entry_price == 100.0
    assert pos.realized_pnl == 0.0

    await tracker.on_fill(Fill(child_id="c2", parent_id=None, symbol="BTCUSDT",
                               side=Side.SELL, qty=0.5, price=110.0, fee=0.0, fee_asset="USDT"))
    pos = tracker.get("BTCUSDT")
    assert pos is not None
    assert pos.qty == 0.5
    assert pytest.approx(pos.realized_pnl) == 5.0  # 0.5 * (110-100)


@pytest.mark.asyncio
async def test_short_then_flip_long() -> None:
    bus = EventBus()
    tracker = PositionTracker(bus)

    await tracker.on_fill(Fill(child_id="c1", parent_id=None, symbol="ETHUSDT",
                               side=Side.SELL, qty=1.0, price=100.0, fee=0.0, fee_asset="USDT"))
    await tracker.on_fill(Fill(child_id="c2", parent_id=None, symbol="ETHUSDT",
                               side=Side.BUY, qty=2.0, price=90.0, fee=0.0, fee_asset="USDT"))

    pos = tracker.get("ETHUSDT")
    assert pos is not None
    # Closed 1.0 short @ 90 vs entry 100 -> +10 PnL. Residual 1.0 long @ 90.
    assert pytest.approx(pos.realized_pnl) == 10.0
    assert pos.qty == 1.0
    assert pos.avg_entry_price == 90.0


@pytest.mark.asyncio
async def test_add_to_long_weights_entry() -> None:
    bus = EventBus()
    tracker = PositionTracker(bus)

    await tracker.on_fill(Fill(child_id="c1", parent_id=None, symbol="BTCUSDT",
                               side=Side.BUY, qty=1.0, price=100.0, fee=0.0, fee_asset="USDT"))
    await tracker.on_fill(Fill(child_id="c2", parent_id=None, symbol="BTCUSDT",
                               side=Side.BUY, qty=3.0, price=110.0, fee=0.0, fee_asset="USDT"))

    pos = tracker.get("BTCUSDT")
    assert pos is not None
    assert pos.qty == 4.0
    # Weighted entry = (1*100 + 3*110)/4 = 107.5
    assert pytest.approx(pos.avg_entry_price) == 107.5
    assert pos.realized_pnl == 0.0
