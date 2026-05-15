"""Validated child-order status transitions.

Venue updates are merged through ``merge_order_status`` so terminal
states cannot regress (e.g. FILLED → PARTIAL) when the exchange sends
out-of-order user-data events.
"""

from __future__ import annotations

import logging

from common.enums import OrderStatus

logger = logging.getLogger(__name__)

TERMINAL_ORDER_STATUSES: frozenset[OrderStatus] = frozenset({
    OrderStatus.FILLED,
    OrderStatus.CANCELLED,
    OrderStatus.REJECTED,
    OrderStatus.EXPIRED,
})

WORKING_ORDER_STATUSES: frozenset[OrderStatus] = frozenset({
    OrderStatus.NEW,
    OrderStatus.ACK,
    OrderStatus.PARTIAL,
})

_ALLOWED: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.NEW: frozenset({
        OrderStatus.NEW,
        OrderStatus.ACK,
        OrderStatus.PARTIAL,
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
        OrderStatus.EXPIRED,
    }),
    OrderStatus.ACK: frozenset({
        OrderStatus.ACK,
        OrderStatus.PARTIAL,
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
        OrderStatus.EXPIRED,
    }),
    OrderStatus.PARTIAL: frozenset({
        OrderStatus.PARTIAL,
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
        OrderStatus.EXPIRED,
    }),
}


def merge_order_status(
    current: OrderStatus,
    incoming: OrderStatus,
    *,
    order_id: str = "",
) -> OrderStatus:
    """Return the status to apply, or ``current`` when ``incoming`` is illegal."""
    if current == incoming:
        return incoming
    if current in TERMINAL_ORDER_STATUSES:
        logger.warning(
            "ignoring status %s -> %s for terminal order %s",
            current.value,
            incoming.value,
            order_id or "?",
        )
        return current
    allowed = _ALLOWED.get(current)
    if allowed is None or incoming not in allowed:
        logger.warning(
            "ignoring illegal status %s -> %s for order %s",
            current.value,
            incoming.value,
            order_id or "?",
        )
        return current
    return incoming
