"""Reconcile local working orders against the venue open-order book.

When ``skip_rest_poll`` is set (engine wires user-data freshness), periodic
passes trust ``ORDER_TRADE_UPDATE`` on the user-data WebSocket instead of
``GET /fapi/v1/openOrders``. Startup and ``force_rest=True`` always pull REST.
"""

from __future__ import annotations

import asyncio
import logging
import time as _time
from collections.abc import Callable
from typing import Any

from common.config import Settings
from common.enums import EventType, OrderStatus
from common.events import Event, EventBus
from common.types import ChildOrder
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
        skip_rest_poll: Callable[[], bool] | None = None,
    ) -> None:
        self._gateway = gateway
        self._oms = oms
        self._breaker = breaker
        self._cancel_orphans = cancel_orphans
        self._on_mismatch = on_mismatch
        self._bus = bus
        self._skip_rest_poll = skip_rest_poll
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
    ) -> OrderReconciler:
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
        await self.reconcile_once(trip_on_mismatch=False, force_rest=True)

    async def _refresh_local_orphans_from_venue(
        self,
        orphan_ids: set[str],
        local_working: dict[str, ChildOrder],
    ) -> None:
        """Merge venue REST truth for children absent from ``openOrders`` (WS lag)."""
        for cid in sorted(orphan_ids):
            child = local_working.get(cid)
            if child is None:
                continue
            try:
                refreshed = await self._gateway.fetch_order_by_client_id(
                    child.symbol,
                    cid,
                )
            except Exception:
                logger.exception(
                    "order reconcile: fetch_order_by_client_id failed (%s %s)",
                    child.symbol,
                    cid,
                )
                continue
            if refreshed is None:
                continue
            logger.info(
                "order reconcile: refreshed stale local order %s status=%s (REST)",
                cid,
                refreshed.status.value,
            )
            await self._oms.on_order_update(refreshed)

    async def reconcile_once(
        self,
        *,
        trip_on_mismatch: bool = True,
        force_rest: bool = False,
    ) -> None:
        if (
            not force_rest
            and self._skip_rest_poll is not None
            and self._skip_rest_poll()
        ):
            logger.debug(
                "order reconcile skipped (user-data WebSocket recently active; no REST openOrders)",
            )
            return

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
                n_cancel = len(orphans_venue)
                await asyncio.gather(
                    *(
                        self._gateway.cancel_order(o.symbol, o.id)
                        for o in venue_orders
                        if o.id in orphans_venue
                    ),
                    return_exceptions=True,
                )
                try:
                    venue_orders = await self._gateway.fetch_open_orders()
                except Exception:  # noqa: BLE001
                    logger.exception("fetch_open_orders failed after orphan cancel")
                else:
                    venue_ids = {o.id for o in venue_orders}
                    orphans_venue = venue_ids - local_ids
                    if not orphans_venue:
                        logger.info(
                            "order reconcile: cleared %d venue orphan(s)",
                            n_cancel,
                        )

        if orphans_local:
            logger.warning(
                "order reconcile: %d local working orders missing from openOrders",
                len(orphans_local),
            )
            await self._refresh_local_orphans_from_venue(orphans_local, local_working)
            local_working = {c.id: c for c in self._oms.working_children()}
            local_ids = set(local_working)
            orphans_local = local_ids - venue_ids

        if orphans_local:
            logger.warning(
                "order reconcile: %d local working orders still unmatched after REST refresh",
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
        if ok:
            logger.debug(
                "order reconcile ok (venue_only=0 local_only=0)",
            )
            self._breaker.rearm(code="order_reconcile_mismatch")
        elif self._on_mismatch is not None:
            await self._on_mismatch(self.last_result)
        # Transient local-only drift (VWAP in flight) is common; venue orphans are riskier.
        if trip_on_mismatch and (orphans_venue or len(orphans_local) >= 3):
            self._breaker.trip(
                Breach(
                    code="order_reconcile_mismatch",
                    scope=BreakerScope.ENGINE,
                    severity=BreakerSeverity.MINOR,
                    detail=f"venue_only={len(orphans_venue)} local_only={len(orphans_local)}",
                    cooldown_sec=60.0,
                )
            )
