"""VwapExecutor end-to-end with a mock gateway.

Mocks live ONLY in tests per the project rules; the engine and gateway
code never imports these helpers.
"""

from __future__ import annotations

import asyncio
import os

import pytest

os.environ.setdefault("BINANCE_API_KEY", "test")
os.environ.setdefault("BINANCE_API_SECRET", "test")

from common.config import Settings  # noqa: E402
from common.enums import AlgoMode, OrderStatus, Side  # noqa: E402
from common.events import EventBus  # noqa: E402
from common.types import ChildOrder, ParentOrder, Position  # noqa: E402
from engine.execution.vwap_executor import ExecutorConfig, VwapExecutor  # noqa: E402
from engine.market_data.feature_store import FeatureStore  # noqa: E402
from engine.market_data.orderbook import OrderBookStore  # noqa: E402
from engine.market_data.trade_tape import TradeTape  # noqa: E402
from engine.orders.order_manager import OrderManager  # noqa: E402
from gateways.gateway_interface import GatewayInterface  # noqa: E402


class _MockGateway(GatewayInterface):
    def __init__(self) -> None:
        self.placed: list[ChildOrder] = []

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def subscribe_market_data(self, *a, **kw) -> None: ...
    async def subscribe_user_data(self, *a, **kw) -> None: ...

    async def place_order(self, order: ChildOrder) -> ChildOrder:
        order.venue_order_id = f"V-{len(self.placed)}"
        order.status = OrderStatus.FILLED
        order.filled_qty = order.qty
        order.avg_fill_price = order.price or 100.0
        self.placed.append(order)
        return order

    async def cancel_order(self, symbol: str, client_order_id: str) -> None:
        return

    async def fetch_positions(self) -> list[Position]:
        return []

    async def fetch_balance(self) -> float:
        return 1000.0


@pytest.mark.asyncio
async def test_executor_runs_full_schedule() -> None:
    bus = EventBus()
    settings = Settings(binance_api_key="x", binance_api_secret="y",
                        vwap_duration_sec=1, vwap_num_slices=3, symbols=["BTCUSDT"])
    gateway = _MockGateway()
    om = OrderManager(gateway, bus)
    books = OrderBookStore(["BTCUSDT"])
    books.get("BTCUSDT").apply_snapshot(
        bids=[(100.0, 1.0)], asks=[(100.5, 1.0)], last_update_id=1,
    )
    features = FeatureStore(books, TradeTape(window_sec=10), settings)

    executor = VwapExecutor(
        order_manager=om,
        features=features,
        price_provider=lambda _sym: 100.0,
        settings=settings,
        config=ExecutorConfig(duration_sec=0.3, n_slices=3, slice_timeout_sec=0.1),
    )

    parent = ParentOrder(id="P-1", symbol="BTCUSDT", side=Side.BUY, qty=0.6,
                          algo_mode=AlgoMode.NORMAL)
    await executor.execute(parent)
    # Wait for the schedule to drain.
    await asyncio.sleep(1.2)

    assert len(gateway.placed) == 3
    assert pytest.approx(sum(o.qty for o in gateway.placed)) == 0.6
