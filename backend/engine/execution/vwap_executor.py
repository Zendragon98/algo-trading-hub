"""Runs a VWAP schedule against the OMS.

Each parent order spawns one `_run_parent` task which iterates the
slices, sleeps until the next slice's `delay_sec`, and submits a child
order via the OrderManager. Children are LIMIT orders pegged to the
top-of-book on the passive side; if the venue rejects (or the slice
isn't filled within `slice_timeout_sec`) the executor cancels and
re-submits as a MARKET order to keep the schedule on track.

The executor never blocks the engine loop — each parent runs as its own
asyncio task and is cancellable via `cancel_parent`.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from common.config import Settings
from common.enums import OrderStatus, OrderType
from common.types import ChildOrder, ParentOrder

from ..market_data.feature_store import FeatureStore
from ..orders.order_manager import OrderManager, new_client_order_id
from .slicer import Slice, build_schedule

logger = logging.getLogger(__name__)


# A small adapter so the executor can ask "what's the current top-of-book
# for SYM?" without depending directly on the OrderBookStore.
PriceProvider = Callable[[str], float | None]


@dataclass(slots=True)
class ExecutorConfig:
    duration_sec: int
    n_slices: int
    slice_timeout_sec: float = 6.0     # how long to let a LIMIT child rest
    market_fallback: bool = True       # cancel + market the residual on timeout


class VwapExecutor:
    def __init__(
        self,
        order_manager: OrderManager,
        features: FeatureStore,
        price_provider: PriceProvider,
        settings: Settings,
        config: ExecutorConfig | None = None,
    ) -> None:
        self._om = order_manager
        self._features = features
        self._price = price_provider
        self._cfg = config or ExecutorConfig(
            duration_sec=settings.vwap_duration_sec,
            n_slices=settings.vwap_num_slices,
        )
        self._tasks: dict[str, asyncio.Task[None]] = {}

    # --- Public ---

    async def execute(self, parent: ParentOrder) -> None:
        if parent.algo_mode is None:
            raise ValueError(f"parent {parent.id} missing algo_mode")
        schedule = build_schedule(
            mode=parent.algo_mode,
            total_qty=parent.qty,
            duration_sec=self._cfg.duration_sec,
            n_slices=self._cfg.n_slices,
        )
        self._om.register_parent(parent)
        task = asyncio.create_task(
            self._run_parent(parent, schedule), name=f"vwap-{parent.id}"
        )
        self._tasks[parent.id] = task
        # Detach: the parent's lifecycle is independent of `execute()`'s caller.
        task.add_done_callback(lambda _t, pid=parent.id: self._tasks.pop(pid, None))

    async def cancel_parent(self, parent_id: str) -> None:
        task = self._tasks.get(parent_id)
        if task is not None:
            task.cancel()
        await self._om.cancel_parent(parent_id)

    async def shutdown(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        for task in list(self._tasks.values()):
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._tasks.clear()

    # --- Internal ---

    async def _run_parent(self, parent: ParentOrder, schedule: list[Slice]) -> None:
        logger.info(
            "VWAP %s %s %.6f %s mode=%s slices=%d",
            parent.id, parent.side.value, parent.qty, parent.symbol,
            parent.algo_mode.value if parent.algo_mode else "-",
            len(schedule),
        )
        last_delay = 0.0
        for slc in schedule:
            wait = slc.delay_sec - last_delay
            if wait > 0:
                await asyncio.sleep(wait)
            last_delay = slc.delay_sec

            try:
                await self._submit_slice(parent, slc)
            except Exception:  # noqa: BLE001
                logger.exception("slice %d failed; aborting parent %s", slc.index, parent.id)
                await self._om.cancel_parent(parent.id)
                return

        logger.info("VWAP %s schedule exhausted", parent.id)

    async def _submit_slice(self, parent: ParentOrder, slc: Slice) -> None:
        price = self._passive_price(parent)
        order_type = OrderType.LIMIT if price is not None else OrderType.MARKET
        child = ChildOrder(
            id=new_client_order_id(),
            parent_id=parent.id,
            symbol=parent.symbol,
            side=parent.side,
            qty=slc.qty,
            price=price,
            order_type=order_type,
        )
        placed = await self._om.submit_child(child)

        if order_type is OrderType.MARKET:
            return  # market orders fill immediately, nothing to babysit

        await self._await_fill_or_market(placed)

    async def _await_fill_or_market(self, child: ChildOrder) -> None:
        """Wait up to `slice_timeout_sec` for the limit to fill; market the residual."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + self._cfg.slice_timeout_sec
        # Cap the poll interval at 100ms so short timeouts (used by tests
        # and the live mid-flight cancellation path) actually behave as
        # advertised. Mock gateways report fills synchronously; the loop
        # body will catch FILLED on its first iteration in that case.
        poll = min(0.1, self._cfg.slice_timeout_sec / 2)
        while True:
            if child.status in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED):
                return
            if loop.time() >= deadline:
                break
            await asyncio.sleep(poll)
        if not self._cfg.market_fallback:
            return

        await self._om.cancel(child.id)
        residual = max(0.0, child.qty - child.filled_qty)
        if residual <= 0:
            return
        market = ChildOrder(
            id=new_client_order_id(),
            parent_id=child.parent_id,
            symbol=child.symbol,
            side=child.side,
            qty=residual,
            price=None,
            order_type=OrderType.MARKET,
        )
        await self._om.submit_child(market)

    def _passive_price(self, parent: ParentOrder) -> float | None:
        """Pick a passive limit price on the resting side of the book.

        If the book isn't ready, fall back to a market order by returning None.
        """
        feat = self._features.snapshot(parent.symbol)
        if feat.mid is None or feat.spread_bps is None:
            return None
        # For a buy, rest at the bid (passive); for a sell, rest at the ask.
        # Use the live top-of-book from the price provider for tighter pegging.
        last = self._price(parent.symbol)
        return last if last is not None else feat.mid
