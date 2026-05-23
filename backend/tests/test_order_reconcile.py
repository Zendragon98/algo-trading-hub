"""Order-level reconciliation."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("BINANCE_API_KEY", "test")
os.environ.setdefault("BINANCE_API_SECRET", "test")

from common.enums import OrderStatus, OrderType, Side  # noqa: E402
from common.events import EventBus  # noqa: E402
from common.types import ChildOrder, Kline, Position  # noqa: E402
from engine.core.order_reconciliation import OrderReconciler  # noqa: E402
from engine.orders.order_manager import OrderManager  # noqa: E402
from engine.risk.circuit_breaker import (  # noqa: E402
    Breach,
    BreakerScope,
    BreakerSeverity,
    CircuitBreaker,
)
from gateways.gateway_interface import GatewayInterface  # noqa: E402


class _Gw(GatewayInterface):
    def __init__(self, open_orders: list[ChildOrder]) -> None:
        self._open = open_orders
        self.cancelled: list[str] = []
        self.n_fetch_open_orders = 0

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def subscribe_market_data(self, *a, **kw) -> None: ...
    async def subscribe_user_data(self, *a, **kw) -> None: ...
    async def place_order(self, order: ChildOrder) -> ChildOrder:
        return order
    async def cancel_order(self, symbol: str, client_order_id: str) -> None:
        self.cancelled.append(client_order_id)
        self._open = [o for o in self._open if o.id != client_order_id]
    async def fetch_open_orders(self, symbol: str | None = None) -> list[ChildOrder]:
        self.n_fetch_open_orders += 1
        return list(self._open)
    async def fetch_positions(self) -> list[Position]:
        return []
    async def fetch_balance(self) -> float:
        return 0.0
    async def book_snapshot(self, symbol: str, depth: int = 100) -> dict:
        return {"lastUpdateId": 0, "bids": [], "asks": []}
    async def klines(self, symbol: str, interval: str, limit: int = 200) -> list[Kline]:
        return []


class _GwWithLookup(_Gw):
    """Gateway mock that resolves stale locals via ``fetch_order_by_client_id``."""

    def __init__(
        self,
        open_orders: list[ChildOrder],
        lookups: dict[str, ChildOrder],
    ) -> None:
        super().__init__(open_orders)
        self._lookups = lookups

    async def fetch_order_by_client_id(self, symbol: str, client_order_id: str) -> ChildOrder | None:
        return self._lookups.get(client_order_id)


@pytest.mark.asyncio
async def test_order_reconcile_skips_rest_when_user_data_fresh() -> None:
    bus = EventBus()
    gw = _Gw(open_orders=[])
    oms = OrderManager(gateway=gw, bus=bus)
    oms.touch_ws_user_data_activity()
    rec = OrderReconciler(gw, oms, CircuitBreaker(bus=bus), skip_rest_poll=lambda: True)
    await rec.reconcile_once()
    assert gw.n_fetch_open_orders == 0


@pytest.mark.asyncio
async def test_order_reconcile_force_rest_bypasses_skip() -> None:
    bus = EventBus()
    gw = _Gw(open_orders=[])
    oms = OrderManager(gateway=gw, bus=bus)
    oms.touch_ws_user_data_activity()
    rec = OrderReconciler(gw, oms, CircuitBreaker(bus=bus), skip_rest_poll=lambda: True)
    await rec.reconcile_once(force_rest=True, trip_on_mismatch=False)
    assert gw.n_fetch_open_orders == 1


@pytest.mark.asyncio
async def test_local_orphan_marked_rejected() -> None:
    bus = EventBus()
    breaker = CircuitBreaker(bus=bus)
    gw = _Gw(open_orders=[])
    oms = OrderManager(gateway=gw, bus=bus)
    child = ChildOrder(
        id="ALPHA7-abc-00",
        parent_id="P-1",
        symbol="BTCUSDT",
        side=Side.BUY,
        qty=0.01,
        price=100.0,
        order_type=OrderType.LIMIT,
        status=OrderStatus.ACK,
    )
    oms._children[child.id] = child  # noqa: SLF001
    oms._child_to_parent[child.id] = child.parent_id  # noqa: SLF001

    rec = OrderReconciler(gw, oms, breaker, cancel_orphans=False)
    await rec.reconcile_once(trip_on_mismatch=False)

    updated = oms.child(child.id)
    assert updated is not None
    assert updated.status is OrderStatus.REJECTED


@pytest.mark.asyncio
async def test_local_orphan_rest_refresh_merges_venue_row() -> None:
    """OMS working + empty openOrders should merge REST query before REJECT/breaker."""
    bus = EventBus()
    breaker = CircuitBreaker(bus=bus)
    cid = "ALPHA7-abc-00"
    child = ChildOrder(
        id=cid,
        parent_id="P-1",
        symbol="BTCUSDT",
        side=Side.BUY,
        qty=0.01,
        price=100.0,
        order_type=OrderType.LIMIT,
        status=OrderStatus.ACK,
    )
    venue_row = ChildOrder(
        id=cid,
        parent_id="",
        symbol="BTCUSDT",
        side=Side.BUY,
        qty=0.01,
        price=100.0,
        order_type=OrderType.LIMIT,
        status=OrderStatus.FILLED,
        filled_qty=0.01,
        avg_fill_price=100.0,
        venue_order_id="999",
    )
    gw = _GwWithLookup(open_orders=[], lookups={cid: venue_row})
    oms = OrderManager(gateway=gw, bus=bus)
    oms._children[child.id] = child  # noqa: SLF001
    oms._child_to_parent[child.id] = child.parent_id  # noqa: SLF001

    rec = OrderReconciler(gw, oms, breaker, cancel_orphans=False)
    await rec.reconcile_once(trip_on_mismatch=False)

    updated = oms.child(cid)
    assert updated is not None
    assert updated.status is OrderStatus.FILLED
    assert updated.venue_order_id == "999"
    assert rec.last_result["ok"] is True


@pytest.mark.asyncio
async def test_venue_orphans_cancelled_and_breaker_cleared() -> None:
    bus = EventBus()
    breaker = CircuitBreaker(bus=bus)
    orphan = ChildOrder(
        id="ALPHA7-orphan-00",
        parent_id="P-x",
        symbol="BTCUSDT",
        side=Side.BUY,
        qty=0.01,
        price=100.0,
        order_type=OrderType.LIMIT,
        status=OrderStatus.ACK,
    )
    gw = _Gw(open_orders=[orphan])
    oms = OrderManager(gateway=gw, bus=bus)
    rec = OrderReconciler(gw, oms, breaker, cancel_orphans=True)
    breaker.trip(
        Breach(
            code="order_reconcile_mismatch",
            scope=BreakerScope.ENGINE,
            severity=BreakerSeverity.MINOR,
        )
    )
    await rec.reconcile_once(trip_on_mismatch=False)
    assert gw.cancelled == ["ALPHA7-orphan-00"]
    assert rec.last_result["ok"] is True
    assert not breaker.is_blocked(BreakerScope.ENGINE)
