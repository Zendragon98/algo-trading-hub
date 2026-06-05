"""OMS fill handling preserves parent attribution when child map is missing."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from common.enums import Side
from common.events import EventBus
from common.types import Fill
from engine.orders.order_manager import OrderManager


@pytest.mark.asyncio
async def test_on_fill_keeps_parent_id_when_child_map_missing() -> None:
    bus = EventBus()
    oms = OrderManager(MagicMock(), bus)
    fill = Fill(
        child_id="orphan-child",
        parent_id="P-known-parent",
        symbol="BTCUSDT",
        side=Side.BUY,
        qty=1.0,
        price=100.0,
        fee=0.0,
        fee_asset="USDT",
    )
    assert await oms.on_fill(fill) is True
    assert fill.parent_id == "P-known-parent"
