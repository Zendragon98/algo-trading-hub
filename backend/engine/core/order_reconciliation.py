"""Reconcile local working orders against the venue open-order book."""

from __future__ import annotations

import asyncio
import logging
import time as _time
from typing import Any

from common.config import Settings
from common.enums import EventType, OrderStatus
from common.events import Event, EventBus

from gateways.gateway_interface import GatewayInterface

from ..orders.order_manager import OrderManager
from ..risk.circuit_breaker import (
    Breach,
    BreakerScope,
    BreakerSeverity,
    CircuitBreaker,
)

logger = logging.getLogger(__name__)


class OrderReconciler:
    def __init__(
        self,
        gateway: GatewayInterface,
        oms: OrderManager,
        breaker: CircuitBreaker,
        *,
        cancel_orphans: bool = False,
        on_mismatch: Any | None = None,
        bus: EventBus | None = None,
    ) -> None:
        self._gateway = gateway
        self._oms = oms
        self._breaker = breaker
        self._cancel_orphans = cancel_orphans
        self._on_mismatch = on_mismatch
        self._bus = bus
        self._interval_sec = 60.0
        self._task: asyncio.Task[None] | None = None
        self.last_result: dict[str, object] = {"ok": True, "venue_only": 0, "local_only": 0, "ts": 0.0}

    def apply_settings(self, settings: Settings) -> None:
        self._cancel_orphans = bool(settings.reconcile_cancel_orphans)
        self._interval_sec = max(5.0, float(settings.reconcile_interval_sec))

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="engine-order-reconcile")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._interval_sec)
                await self.reconcile_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("order reconcile loop failed")

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        gateway: GatewayInterface,
        oms: OrderManager,
        breaker: CircuitBreaker,
    ) -> "OrderReconciler":
        inst = cls(
            gateway=gateway,
            oms=oms,
            breaker=breaker,
            cancel_orphans=settings.reconcile_cancel_orphans,
        )
        inst._interval_sec = max(5.0, float(settings.reconcile_interval_sec))
        return inst

    async def sync_startup(self) -> None:
        """Align OMS with venue open orders after connect."""
        await self.reconcile_once(trip_on_mismatch=False)

    async def reconcile_once(self, *, trip_on_mismatch: bool = True) -> None:
        try:
            venue_orders = await self._gateway.fetch_open_orders()
        except Exception:  # noqa: BLE001
            logger.exception("fetch_open_orders failed during order reconcile")
            return

        venue_ids = {o.id for o in venue_orders}
        local_working = {
            c.id: c for c in self._oms.working_children()
        }
        local_ids = set(local_working)

        orphans_venue = venue_ids - local_ids
        orphans_local = local_ids - venue_ids

        if orphans_venue:
            logger.warning(
                "order reconcile: %d venue open orders unknown locally: %s",
                len(orphans_venue),
                ", ".join(sorted(orphans_venue)[:5]),
            )
            if self._cancel_orphans:
                await asyncio.gather(
                    *(
                        self._gateway.cancel_order(o.symbol, oid)
                        for o in venue_orders
                        if o.id in orphans_venue
                    ),
                    return_exceptions=True,
                )

        if orphans_local:
            logger.warning(
                "order reconcile: %d local working orders missing on venue",
                len(orphans_local),
            )
            for cid in orphans_local:
                child = local_working[cid]
                child.status = OrderStatus.REJECTED
                await self._oms.on_order_update(child)

        ok = not (orphans_venue or orphans_local)
        self.last_result = {
            "ok": ok,
            "venue_only": len(orphans_venue),
            "local_only": len(orphans_local),
            "ts": _time.time(),
        }
        if self._bus is not None:
            await self._bus.publish(
                Event(
                    type=EventType.STATUS,
                    payload={"kind": "order_reconcile", **self.last_result},
                    source="order_reconciler",
                )
            )
        if not ok and self._on_mismatch is not None:
            await self._on_mismatch(self.last_result)
        if trip_on_mismatch and (orphans_venue or orphans_local):
            self._breaker.trip(
                Breach(
                    code="order_reconcile_mismatch",
                    scope=BreakerScope.ENGINE,
                    severity=BreakerSeverity.MINOR,
                    detail=f"venue_only={len(orphans_venue)} local_only={len(orphans_local)}",
                    cooldown_sec=60.0,
                )
            )
