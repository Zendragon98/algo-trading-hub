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
import time as _time
import uuid
from dataclasses import asdict
from typing import Iterable, Protocol

from common.enums import EventType, OrderStatus
from common.events import Event, EventBus
from common.types import ChildOrder, Fill, ParentOrder
from engine.orders.order_state_machine import (
    TERMINAL_ORDER_STATUSES,
    WORKING_ORDER_STATUSES,
    merge_order_status,
)
from gateways.gateway_interface import GatewayInterface

logger = logging.getLogger(__name__)


class _SubmitGuardLike(Protocol):
    """Minimal surface OrderManager needs from SubmitGuard.

    Kept as a Protocol so the OMS can stay testable without importing
    the execution package (which depends on OMS in turn).
    """

    async def gate_child(
        self, symbol: str, *, reduce_only: bool
    ) -> tuple[bool, str]: ...

    def record_status(self, symbol: str, status: OrderStatus) -> None: ...


def new_client_order_id(
    parent_id: str,
    slice_index: int,
    prefix: str = "ALPHA7",
) -> str:
    """Deterministic client order id for idempotent retries.

    Binance allows up to 36 chars, alphanumerics + `_-.`.
    """
    pid = parent_id.replace("P-", "")[:8]
    return f"{prefix}-{pid}-{slice_index:02d}"


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
        self._submit_guard: _SubmitGuardLike | None = None
        # Wall-clock timestamp of the most recent user-data signal (order
        # update or fill). Consumed by ConnectionMonitor to detect a
        # silent user-data stream so the engine can auto-pause.
        self._last_user_data_ts: float = 0.0
        self._seen_trade_ids: set[str] = set()
        self._max_seen_trades: int = 10_000

    def attach_submit_guard(self, guard: _SubmitGuardLike) -> None:
        """Bind the submission rate-limit / breaker guard.

        Wired by the engine after construction so the OMS doesn't have to
        know about CircuitBreaker directly.
        """
        self._submit_guard = guard

    @property
    def last_user_data_ts(self) -> float:
        return self._last_user_data_ts

    def touch_user_data_activity(self) -> None:
        """Mark the user-data stream as recently active (fills, orders, or account)."""
        self._last_user_data_ts = _time.time()

    # --- Submission ---

    def register_parent(self, parent: ParentOrder) -> None:
        self._parents[parent.id] = parent

    async def submit_child(self, child: ChildOrder) -> ChildOrder:
        """Send a child to the venue and remember it locally.

        Returns the order with `venue_order_id` populated. Raises only on
        hard rejection so callers (the VWAP executor) can decide whether
        to retry or abort the parent.

        When a SubmitGuard is attached the call may be (briefly) blocked
        by the global token-bucket throttle, or fast-rejected when an
        engine/symbol-scope breaker is latched.
        """
        if self._submit_guard is not None:
            allowed, reason = await self._submit_guard.gate_child(
                child.symbol, reduce_only=child.reduce_only,
            )
            if not allowed:
                child.status = OrderStatus.REJECTED
                logger.warning(
                    "submit_child gated for %s (symbol=%s reason=%s)",
                    child.id, child.symbol, reason,
                )
                await self._publish_order(child)
                raise RuntimeError(f"submit gated: {reason}")

        async with self._lock:
            self._children[child.id] = child
            self._child_to_parent[child.id] = child.parent_id

        try:
            placed = await self._gateway.place_order(child)
        except Exception as exc:
            # Venue has no position to reduce (-2022); common when local book lags flat.
            if getattr(exc, "code", None) == -2022:
                logger.warning(
                    "reduce_only rejected at venue for %s (symbol=%s qty=%.10f): %s",
                    child.id,
                    child.symbol,
                    child.qty,
                    exc,
                )
            else:
                # `logger.exception(...)` prints a traceback in console logs, but the UI log
                # stream only surfaces the formatted message. Include the exception text.
                logger.exception(
                    "place_order failed for %s (symbol=%s side=%s qty=%.10f type=%s price=%s): %s",
                    child.id,
                    child.symbol,
                    child.side.value,
                    child.qty,
                    child.order_type.value,
                    "-" if child.price is None else f"{child.price:.10f}",
                    exc,
                )
            child.status = OrderStatus.REJECTED
            if self._submit_guard is not None:
                self._submit_guard.record_status(child.symbol, OrderStatus.REJECTED)
            await self._publish_order(child)
            raise

        # Update with venue-supplied fields.
        async with self._lock:
            self._children[child.id] = placed

        if self._submit_guard is not None:
            self._submit_guard.record_status(placed.symbol, placed.status)
        await self._publish_order(placed)
        return placed

    # --- Updates from the user-data stream ---

    async def on_order_update(self, update: ChildOrder) -> None:
        """Merge a venue-side order update into our state."""
        self.touch_user_data_activity()
        async with self._lock:
            existing = self._children.get(update.id)
            if existing is None:
                # Order we didn't originate (e.g. manual UI cancel). Track
                # it so cancel/flatten still works, but don't link to a parent.
                self._children[update.id] = update
                merged = update
            else:
                existing.status = merge_order_status(
                    existing.status,
                    update.status,
                    order_id=existing.id,
                )
                existing.filled_qty = update.filled_qty
                existing.avg_fill_price = update.avg_fill_price
                if update.venue_order_id and not existing.venue_order_id:
                    existing.venue_order_id = update.venue_order_id
                merged = existing

        if self._submit_guard is not None:
            self._submit_guard.record_status(merged.symbol, merged.status)
        await self._publish_order(merged)

    async def on_fill(self, fill: Fill) -> bool:
        """Apply fill; return False if duplicate trade_id was ignored."""
        self.touch_user_data_activity()
        async with self._lock:
            if fill.trade_id and fill.trade_id in self._seen_trade_ids:
                logger.info("ignoring duplicate fill trade_id=%s", fill.trade_id)
                return False
            if fill.trade_id:
                self._seen_trade_ids.add(fill.trade_id)
                if len(self._seen_trade_ids) > self._max_seen_trades:
                    self._seen_trade_ids = set(
                        list(self._seen_trade_ids)[-self._max_seen_trades // 2 :]
                    )
            fill.parent_id = self._child_to_parent.get(fill.child_id)

        return True

    # --- Cancel / flatten ---

    async def cancel(self, child_id: str) -> None:
        child = self._children.get(child_id)
        if child is None:
            return
        if child.status in TERMINAL_ORDER_STATUSES:
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
            if c.status in WORKING_ORDER_STATUSES
        ]

    def parent(self, parent_id: str) -> ParentOrder | None:
        return self._parents.get(parent_id)

    def children_of(self, parent_id: str) -> list[ChildOrder]:
        return [c for c in self._children.values() if c.parent_id == parent_id]

    def child(self, child_id: str) -> ChildOrder | None:
        """Return the latest OMS view of `child_id` (exchange-updated)."""
        return self._children.get(child_id)

    # --- WAL recovery ---

    def restore_fill_seen(self, trade_id: str | None) -> None:
        if trade_id:
            self._seen_trade_ids.add(trade_id)

    def restore_state(
        self,
        *,
        parents: dict[str, ParentOrder],
        children: dict[str, ChildOrder],
    ) -> None:
        """Merge WAL-restored parents/children without hitting the venue."""
        self._parents.update(parents)
        for cid, child in children.items():
            self._children[cid] = child
            if child.parent_id:
                self._child_to_parent[cid] = child.parent_id

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
    # Surface venue price explicitly for API payloads (matches effective fill price).
    d["venue_price"] = fill.venue_price or fill.price
    return d
