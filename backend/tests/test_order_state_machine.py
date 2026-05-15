"""Order status FSM guards."""

from __future__ import annotations

from common.enums import OrderStatus  # noqa: E402
from engine.orders.order_state_machine import (  # noqa: E402
    TERMINAL_ORDER_STATUSES,
    merge_order_status,
)


def test_terminal_blocks_regression() -> None:
    assert merge_order_status(
        OrderStatus.FILLED,
        OrderStatus.PARTIAL,
        order_id="c1",
    ) is OrderStatus.FILLED


def test_ack_to_partial_and_filled() -> None:
    assert merge_order_status(OrderStatus.ACK, OrderStatus.PARTIAL) is OrderStatus.PARTIAL
    assert merge_order_status(OrderStatus.PARTIAL, OrderStatus.FILLED) is OrderStatus.FILLED


def test_expired_is_terminal() -> None:
    assert OrderStatus.EXPIRED in TERMINAL_ORDER_STATUSES
    assert merge_order_status(
        OrderStatus.EXPIRED,
        OrderStatus.ACK,
        order_id="c2",
    ) is OrderStatus.EXPIRED


def test_ack_to_expired() -> None:
    assert merge_order_status(OrderStatus.ACK, OrderStatus.EXPIRED) is OrderStatus.EXPIRED
