"""OrderExposureGuard institutional limits."""

from __future__ import annotations

from common.enums import OrderStatus, OrderType, Side
from common.types import ChildOrder
from engine.risk.order_exposure_guard import OrderExposureGuard


def _child(symbol: str, qty: float, price: float) -> ChildOrder:
    return ChildOrder(
        id=f"C-{symbol}",
        parent_id="P-1",
        symbol=symbol,
        side=Side.BUY,
        qty=qty,
        price=price,
        order_type=OrderType.LIMIT,
        status=OrderStatus.ACK,
    )


def test_max_active_orders_blocks_at_cap() -> None:
    working = [_child("BTCUSDT", 1.0, 100.0)]
    guard = OrderExposureGuard(
        working_children=lambda: working,
        mid_for_symbol=lambda _s: 100.0,
        max_active_orders=1,
    )
    ok, reason = guard.check("ETHUSDT", qty=1.0, price=50.0)
    assert not ok
    assert reason == "max_active_orders"


def test_max_open_order_notional_blocks_overflow() -> None:
    working = [_child("BTCUSDT", 1.0, 100.0)]
    guard = OrderExposureGuard(
        working_children=lambda: working,
        mid_for_symbol=lambda _s: 100.0,
        max_open_order_notional_usd=150.0,
    )
    ok, reason = guard.check("ETHUSDT", qty=1.0, price=100.0)
    assert not ok
    assert reason == "max_open_order_notional"
