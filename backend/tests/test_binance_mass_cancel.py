"""Binance mass cancel uses one allOpenOrders call per symbol, not per child."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ChildOrder
from gateways.binance.binance_gateway import BinanceGateway


@pytest.mark.asyncio
async def test_cancel_all_open_orders_one_call_per_symbol() -> None:
    gw = BinanceGateway.__new__(BinanceGateway)
    gw._rest = MagicMock()
    gw._rest.cancel_all_open_orders = AsyncMock(return_value={})
    gw.fetch_open_orders = AsyncMock(
        return_value=[
            ChildOrder(
                id="a1",
                parent_id="p1",
                symbol="BTCUSDT",
                side=None,
                qty=1.0,
                price=1.0,
                order_type=None,
            ),
            ChildOrder(
                id="a2",
                parent_id="p2",
                symbol="BTCUSDT",
                side=None,
                qty=1.0,
                price=1.0,
                order_type=None,
            ),
            ChildOrder(
                id="b1",
                parent_id="p3",
                symbol="ETHUSDT",
                side=None,
                qty=1.0,
                price=1.0,
                order_type=None,
            ),
        ],
    )

    await BinanceGateway.cancel_all_open_orders(gw)

    assert gw._rest.cancel_all_open_orders.await_count == 2
    symbols = {c.args[0] for c in gw._rest.cancel_all_open_orders.await_args_list}
    assert symbols == {"BTCUSDT", "ETHUSDT"}
