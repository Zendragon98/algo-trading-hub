"""QuoteExecutor posts/cancels post-only quotes."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("BINANCE_API_KEY", "test")
os.environ.setdefault("BINANCE_API_SECRET", "test")

from common.config import Settings  # noqa: E402
from common.enums import MmExecutionMode, OrderStatus, Side  # noqa: E402
from common.events import EventBus  # noqa: E402
from common.types import ChildOrder, QuoteIntent  # noqa: E402
from engine.execution.quote_executor import QuoteExecutor  # noqa: E402
from engine.market_data.own_quote_book import OwnQuoteBook  # noqa: E402
from engine.orders.order_manager import OrderManager  # noqa: E402
from gateways.gateway_interface import GatewayInterface, SymbolFilters  # noqa: E402


class _MockGateway(GatewayInterface):
    def __init__(self) -> None:
        self.placed: list[ChildOrder] = []

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def subscribe_market_data(self, *a, **kw) -> None: ...
    async def subscribe_user_data(self, *a, **kw) -> None: ...

    async def place_order(self, order: ChildOrder) -> ChildOrder:
        order.venue_order_id = "V1"
        order.status = OrderStatus.ACK
        self.placed.append(order)
        return order

    async def cancel_order(self, symbol: str, client_order_id: str) -> None:
        return

    def get_symbol_filters(self, symbol: str) -> SymbolFilters | None:
        return None

    async def fetch_positions(self):
        return []

    async def fetch_balance(self) -> float:
        return 10_000.0

    async def book_snapshot(self, symbol: str, depth: int = 100) -> dict:
        return {"lastUpdateId": 0, "bids": [], "asks": []}

    async def klines(self, *a, **kw):
        return []


@pytest.mark.asyncio
async def test_refresh_submits_bid_and_ask() -> None:
    bus = EventBus()
    gw = _MockGateway()
    om = OrderManager(gw, bus)
    own = OwnQuoteBook()
    ex = QuoteExecutor(om, own, Settings(mm_quote_enabled=True))
    intent = QuoteIntent(
        symbol="BTCUSDT",
        strategy_name="market_making",
        bid_price=99.0,
        bid_qty=0.01,
        ask_price=101.0,
        ask_qty=0.01,
        reason="test",
    )
    await ex.refresh([intent])
    assert len(gw.placed) == 2
    sides = {o.side for o in gw.placed}
    assert Side.BUY in sides and Side.SELL in sides


@pytest.mark.asyncio
async def test_refresh_bumps_qty_to_venue_min_notional() -> None:
    bus = EventBus()
    gw = _MockGateway()

    def _filters(symbol: str) -> SymbolFilters:
        return SymbolFilters(symbol=symbol, step_size=0.0001, min_notional=50.0)

    gw.get_symbol_filters = _filters  # type: ignore[method-assign]
    om = OrderManager(gw, bus)
    own = OwnQuoteBook()
    ex = QuoteExecutor(
        om,
        own,
        Settings(mm_quote_enabled=True),
        symbol_filters=gw.get_symbol_filters,
    )
    # 0.0002 BTC @ 80_000 = $16 — below $50 floor; should bump to ~0.000625
    intent = QuoteIntent(
        symbol="BTCUSDT",
        strategy_name="market_making_v2",
        bid_price=80_000.0,
        bid_qty=0.0002,
        ask_price=None,
        ask_qty=0.0,
        reason="test",
    )
    await ex.refresh([intent])
    assert len(gw.placed) == 1
    assert gw.placed[0].qty * gw.placed[0].price >= 50.0 - 1e-6


@pytest.mark.asyncio
async def test_refresh_reduce_only_does_not_bump_to_min_notional() -> None:
    bus = EventBus()
    gw = _MockGateway()

    def _filters(symbol: str) -> SymbolFilters:
        return SymbolFilters(symbol=symbol, step_size=0.1, min_notional=15.0, min_qty=0.1)

    gw.get_symbol_filters = _filters  # type: ignore[method-assign]
    om = OrderManager(gw, bus)
    own = OwnQuoteBook()
    ex = QuoteExecutor(
        om,
        own,
        Settings(mm_quote_enabled=True),
        symbol_filters=gw.get_symbol_filters,
    )
    intent = QuoteIntent(
        symbol="FILUSDT",
        strategy_name="market_making_v2",
        bid_price=0.95,
        bid_qty=0.031,
        ask_price=None,
        ask_qty=0.0,
        reason="test",
        reduce_only_bid=True,
    )
    await ex.refresh([intent])
    assert len(gw.placed) == 1
    assert gw.placed[0].reduce_only is True
    assert gw.placed[0].qty == pytest.approx(0.031, rel=1e-6)


@pytest.mark.asyncio
async def test_ladder_mode_places_multiple_bids() -> None:
    bus = EventBus()
    gw = _MockGateway()
    om = OrderManager(gw, bus)
    own = OwnQuoteBook()
    ex = QuoteExecutor(
        om,
        own,
        Settings(
            mm_quote_enabled=True,
            mm_execution_mode="ladder",
            mm_ladder_levels=3,
            mm_place_range_bps=0.0,
        ),
    )
    intent = QuoteIntent(
        symbol="BTCUSDT",
        strategy_name="market_making",
        bid_price=100.0,
        bid_qty=0.03,
        ask_price=None,
        ask_qty=0.0,
        reason="test",
        bid_execution_mode=MmExecutionMode.LADDER,
        best_bid=99.9,
        best_ask=100.1,
        venue_mid=100.0,
    )
    await ex.refresh([intent])
    buys = [o for o in gw.placed if o.side is Side.BUY]
    assert len(buys) == 3
