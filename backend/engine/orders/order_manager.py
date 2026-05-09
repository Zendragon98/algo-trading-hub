"""Order management.

Tracks every parent and child order in flight, mediates between the
execution layer (which creates orders) and the gateway (which transmits
them), and forwards lifecycle updates onto the EventBus so the API
layer can stream them to the dashboard.

The OMS is the only component allowed to mutate `ChildOrder.status`,
`filled_qty`, and `avg_fill_price`. Other modules read snapshots.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import asdict
from typing import Iterable

from common.enums import EventType, OrderStatus
from common.events import Event, EventBus
from common.types import ChildOrder, Fill, ParentOrder
from gateways.gateway_interface import GatewayInterface

logger = logging.getLogger(__name__)


def new_client_order_id(prefix: str = "ALPHA7") -> str:
    """Generate a venue-friendly client order id.

    Binance allows up to 36 chars, alphanumerics + `_-.`. We use a short
    UUID slice rather than the full UUID for terser dashboard rendering.
    """
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


class OrderManager:
    """In-memory OMS."""

    def __init__(self, gateway: GatewayInterface, bus: EventBus) -> None:
        self._gateway = gateway
        self._bus = bus
        self._parents: dict[str, ParentOrder] = {}
        self._children: dict[str, ChildOrder] = {}
        # client_order_id -> parent_id, used to resolve user-data updates
        # which only carry the client id.
        self._child_to_parent: dict[str, str] = {}
        self._lock = asyncio.Lock()

    # --- Submission ---

    def register_parent(self, parent: ParentOrder) -> None:
        self._parents[parent.id] = parent

    async def submit_child(self, child: ChildOrder) -> ChildOrder:
        """Send a child to the venue and remember it locally.

        Returns the order with `venue_order_id` populated. Raises only on
        hard rejection so callers (the VWAP executor) can decide whether
        to retry or abort the parent.
        """
        async with self._lock:
            self._children[child.id] = child
            self._child_to_parent[child.id] = child.parent_id

        try:
            placed = await self._gateway.place_order(child)
        except Exception:
            logger.exception("place_order failed for %s", child.id)
            child.status = OrderStatus.REJECTED
            await self._publish_order(child)
            raise

        # Update with venue-supplied fields.
        async with self._lock:
            self._children[child.id] = placed

        await self._publish_order(placed)
        return placed

    # --- Updates from the user-data stream ---

    async def on_order_update(self, update: ChildOrder) -> None:
        """Merge a venue-side order update into our state."""
        async with self._lock:
            existing = self._children.get(update.id)
            if existing is None:
                # Order we didn't originate (e.g. manual UI cancel). Track
                # it so cancel/flatten still works, but don't link to a parent.
                self._children[update.id] = update
                merged = update
            else:
                existing.status = update.status
                existing.filled_qty = update.filled_qty
                existing.avg_fill_price = update.avg_fill_price
                if update.venue_order_id and not existing.venue_order_id:
                    existing.venue_order_id = update.venue_order_id
                merged = existing

        await self._publish_order(merged)

    async def on_fill(self, fill: Fill) -> None:
        # Resolve parent if we know it; the gateway can't because the
        # ORDER_TRADE_UPDATE payload doesn't carry the parent id.
        async with self._lock:
            fill.parent_id = self._child_to_parent.get(fill.child_id)

        await self._bus.publish(
            Event(type=EventType.FILL, payload=_fill_to_dict(fill))
        )

    # --- Cancel / flatten ---

    async def cancel(self, child_id: str) -> None:
        child = self._children.get(child_id)
        if child is None:
            return
        if child.status in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED):
            return
        await self._gateway.cancel_order(child.symbol, child_id)

    async def cancel_parent(self, parent_id: str) -> None:
        targets = [c for c in self._children.values() if c.parent_id == parent_id]
        await asyncio.gather(*(self.cancel(c.id) for c in targets), return_exceptions=True)

    async def cancel_all(self) -> None:
        await asyncio.gather(
            *(self.cancel(c.id) for c in list(self._children.values())),
            return_exceptions=True,
        )

    # --- Read-only views ---

    def working_children(self) -> Iterable[ChildOrder]:
        return [
            c for c in self._children.values()
            if c.status in (OrderStatus.NEW, OrderStatus.ACK, OrderStatus.PARTIAL)
        ]

    def parent(self, parent_id: str) -> ParentOrder | None:
        return self._parents.get(parent_id)

    def children_of(self, parent_id: str) -> list[ChildOrder]:
        return [c for c in self._children.values() if c.parent_id == parent_id]

    # --- Internal ---

    async def _publish_order(self, order: ChildOrder) -> None:
        await self._bus.publish(
            Event(type=EventType.ORDER_UPDATE, payload=_order_to_dict(order))
        )


def _order_to_dict(order: ChildOrder) -> dict:
    d = asdict(order)
    # Enums serialise as bare strings for the React console.
    d["side"] = order.side.value
    d["status"] = order.status.value
    d["order_type"] = order.order_type.value
    return d


def _fill_to_dict(fill: Fill) -> dict:
    d = asdict(fill)
    d["side"] = fill.side.value
    # Surface both the venue price (audit) and the engine-effective price
    # (after synthetic impact) so the dashboard can display both.
    d["venue_price"] = fill.venue_price or fill.price
    return d
