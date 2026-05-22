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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from common.config import Settings
from common.enums import AlgoMode, OrderType, Side, Urgency
from common.types import ChildOrder, ParentOrder
from engine.orders.order_state_machine import TERMINAL_ORDER_STATUSES
from gateways.gateway_interface import GatewayInterface, SymbolFilters

from ..market_data.feature_store import FeatureStore
from ..orders.order_manager import OrderManager, new_client_order_id
from ..risk.venue_sizing import venue_cap_qty, venue_qty_in_bounds
from .slicer import Slice, build_schedule

logger = logging.getLogger(__name__)


class _FlattenRecoverComplete(Exception):
    """Internal: flatten parent finished early after a -2022 recovery path."""


def _is_reduce_only_reject(exc: BaseException) -> bool:
    """Binance futures code when there is no open position to reduce."""
    return getattr(exc, "code", None) == -2022


# A small adapter so the executor can ask "what's the current top-of-book
# for SYM?" without depending directly on the OrderBookStore.
PriceProvider = Callable[[str], float | None]
LimitCollarCheck = Callable[[str, float, float], tuple[bool, str]]
# Notified once per parent when its run task ends — for any reason (full
# fill, partial fill, slice rejection, operator cancel). Lets the
# ExecutionTracker close out the report so the OMS panel doesn't pile
# up parents that the venue refused on the first slice.
ParentDoneCallback = Callable[[str], Awaitable[None]]
VenueFlatCallback = Callable[[str], Awaitable[None]]


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
        gateway: GatewayInterface,
        features: FeatureStore,
        price_provider: PriceProvider,
        settings: Settings,
        config: ExecutorConfig | None = None,
        on_parent_done: ParentDoneCallback | None = None,
        on_venue_flat_after_reduce_only: VenueFlatCallback | None = None,
    ) -> None:
        self._om = order_manager
        self._gateway = gateway
        self._features = features
        self._price = price_provider
        self._settings = settings
        self._cfg = config or ExecutorConfig(
            duration_sec=settings.vwap_duration_sec,
            n_slices=settings.vwap_num_slices,
        )
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._on_parent_done = on_parent_done
        self._on_venue_flat = on_venue_flat_after_reduce_only
        self._limit_collar_check: LimitCollarCheck | None = None

    def set_limit_collar_check(self, fn: LimitCollarCheck | None) -> None:
        """Optional pre-submit guard for passive LIMIT pegs vs mid."""
        self._limit_collar_check = fn

    def apply_settings(self, settings: Settings) -> None:
        """Refresh VWAP schedule defaults for *new* parents (in-flight unchanged)."""
        self._settings = settings
        self._cfg = ExecutorConfig(
            duration_sec=settings.vwap_duration_sec,
            n_slices=settings.vwap_num_slices,
            slice_timeout_sec=self._cfg.slice_timeout_sec,
            market_fallback=self._cfg.market_fallback,
        )

    # --- Public ---

    def _cfg_for_parent(self, parent: ParentOrder) -> ExecutorConfig:
        if parent.notes == "flatten_passive":
            return ExecutorConfig(
                duration_sec=int(
                    getattr(self._settings, "flatten_vwap_duration_sec", 18) or 18
                ),
                n_slices=int(getattr(self._settings, "flatten_vwap_slices", 4) or 4),
                slice_timeout_sec=max(6.0, self._cfg.slice_timeout_sec),
                market_fallback=True,
            )
        if parent.notes == "flatten":
            return ExecutorConfig(
                duration_sec=int(self._settings.urgent_duration_sec),
                n_slices=int(self._settings.urgent_num_slices),
                slice_timeout_sec=min(6.0, self._cfg.slice_timeout_sec),
                market_fallback=True,
            )
        if parent.urgency is Urgency.AGGRESSIVE:
            return ExecutorConfig(
                duration_sec=int(self._settings.urgent_duration_sec),
                n_slices=int(self._settings.urgent_num_slices),
                slice_timeout_sec=self._cfg.slice_timeout_sec,
                market_fallback=self._cfg.market_fallback,
            )
        return self._cfg

    async def execute(self, parent: ParentOrder) -> None:
        if parent.algo_mode is None:
            raise ValueError(f"parent {parent.id} missing algo_mode")
        schedule = self._build_viable_schedule(parent, self._cfg_for_parent(parent))
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
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                logger.exception("vwap slice task shutdown raised")
        self._tasks.clear()

    # --- Internal ---

    def _build_viable_schedule(
        self, parent: ParentOrder, cfg: ExecutorConfig | None = None,
    ) -> list[Slice]:
        cfg = cfg or self._cfg
        """Shrink the slice count until every child satisfies venue filters.

        Uses `GatewayInterface.get_symbol_filters` (cached at connect()
        time) to enforce step size, min qty, and min notional. Falls back
        to a single-slice parent when even that won't satisfy the venue;
        the caller can still abort cleanly when `place_order` rejects.
        """
        algo_mode = parent.algo_mode
        if algo_mode is None:
            raise ValueError(f"parent {parent.id} missing algo_mode")
        filters = self._gateway.get_symbol_filters(parent.symbol)
        ref_price = self._price(parent.symbol)
        requested = cfg.n_slices
        schedule: list[Slice] | None = None
        for n in range(requested, 0, -1):
            candidate = build_schedule(
                mode=algo_mode,
                total_qty=parent.qty,
                duration_sec=cfg.duration_sec,
                n_slices=n,
            )
            if all(
                _slice_satisfies(s.qty, filters, ref_price, reduce_only=parent.reduce_only)
                for s in candidate
            ):
                schedule = candidate
                break
        if schedule is None:
            schedule = build_schedule(
                mode=algo_mode,
                total_qty=parent.qty,
                duration_sec=cfg.duration_sec,
                n_slices=1,
            )
        # When venue constraints force us down to a single slice, still use
        # the same orderbook-driven mode (front/backload) to time *when* we
        # place the parent-sized order so we can compare vs arrival/VWAP.
        if len(schedule) == 1 and requested > 1:
            schedule = [
                Slice(index=0, qty=schedule[0].qty, delay_sec=_single_shot_delay(algo_mode, cfg.duration_sec))
            ]
        if len(schedule) < requested:
            logger.info(
                "VWAP %s slice count reduced %d -> %d (%s qty=%.8f; venue filters)",
                parent.id,
                requested,
                len(schedule),
                parent.symbol,
                parent.qty,
            )
        return schedule

    async def _run_parent(self, parent: ParentOrder, schedule: list[Slice]) -> None:
        note_suffix = f" | {parent.notes}" if parent.notes else ""
        logger.info(
            "VWAP %s %s %.6f %s mode=%s slices=%d%s",
            parent.id, parent.side.value, parent.qty, parent.symbol,
            parent.algo_mode.value if parent.algo_mode else "-",
            len(schedule),
            note_suffix,
        )
        try:
            last_delay = 0.0
            for slc in schedule:
                wait = slc.delay_sec - last_delay
                if wait > 0:
                    await asyncio.sleep(wait)
                last_delay = slc.delay_sec

                try:
                    await self._submit_slice(parent, slc)
                except _FlattenRecoverComplete:
                    logger.info(
                        "VWAP %s flatten parent finished early (-2022 recover path)",
                        parent.id,
                    )
                    break
                except ValueError as exc:
                    logger.warning(
                        "slice %d skipped (venue bounds) parent=%s: %s",
                        slc.index,
                        parent.id,
                        exc,
                    )
                    await self._om.cancel_parent(parent.id)
                    return
                except Exception as exc:  # noqa: BLE001
                    if _is_reduce_only_reject(exc):
                        logger.warning(
                            "slice %d aborted (reduce_only) parent=%s: %s",
                            slc.index,
                            parent.id,
                            exc,
                        )
                    elif getattr(exc, "code", None) == -4164:
                        logger.warning(
                            "slice %d aborted (min notional) parent=%s: %s",
                            slc.index,
                            parent.id,
                            exc,
                        )
                    else:
                        logger.exception(
                            "slice %d failed; aborting parent %s: %s",
                            slc.index,
                            parent.id,
                            exc,
                        )
                    await self._om.cancel_parent(parent.id)
                    return

            logger.info("VWAP %s schedule exhausted", parent.id)
        finally:
            # Always close the parent on the tracker, regardless of how
            # the run ended: full fill (no-op, already complete), partial
            # fill, slice rejection, or operator cancel. Without this the
            # ExecutionTracker's open set grows unboundedly and the OMS
            # panel keeps showing parents whose first slice was rejected.
            if self._on_parent_done is not None:
                try:
                    await self._on_parent_done(parent.id)
                except Exception:  # noqa: BLE001
                    logger.exception("on_parent_done failed for %s", parent.id)

    async def _notify_venue_flat(self, symbol: str, parent_id: str, *, context: str) -> None:
        if self._on_venue_flat is not None:
            try:
                await self._on_venue_flat(symbol)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "on_venue_flat_after_reduce_only failed symbol=%s parent=%s",
                    symbol,
                    parent_id,
                )
        logger.info(
            "reduceOnly recovered: venue flat (%s) symbol=%s parent=%s",
            context,
            symbol,
            parent_id,
        )

    async def _try_reduce_only_recovery_after_reject(
        self,
        parent: ParentOrder,
        *,
        slice_index: int,
        ref_price: float | None,
    ) -> bool:
        """Handle Binance -2022 on reduce-only slices.

        Refresh ``positionRisk`` once. If the venue is flat, end the parent
        early and let the engine heal local state. Flatten parents may still
        market-claw a residual when the venue shows open size.
        """
        if not parent.reduce_only:
            return False
        try:
            rows = await self._gateway.fetch_positions()
        except Exception:  # noqa: BLE001
            logger.warning(
                "reduceOnly recovery skipped: fetch_positions failed parent=%s",
                parent.id,
                exc_info=True,
            )
            return False

        tol = float(getattr(self._settings, "reconcile_qty_tolerance", 1e-6))
        sym_u = parent.symbol.upper()
        venue_qty = 0.0
        for p in rows:
            if str(p.symbol).upper() == sym_u:
                venue_qty = float(p.qty)
                break

        if abs(venue_qty) <= tol:
            await self._notify_venue_flat(parent.symbol, parent.id, context="already_flat")
            return True

        is_flatten = parent.notes in ("flatten", "flatten_passive")
        if not is_flatten:
            return False

        filters = self._gateway.get_symbol_filters(parent.symbol)
        mq = venue_cap_qty(abs(venue_qty), filters)
        mref = ref_price if ref_price is not None and ref_price > 0 else (
            self._price(parent.symbol)
        )
        if mq <= tol or not venue_qty_in_bounds(
            mq, filters, mref, reduce_only=True,
        ):
            logger.warning(
                "flatten reduceOnly recovery: residual not sendable "
                "(symbol=%s venue_qty=%.8f mq=%s parent=%s)",
                parent.symbol,
                venue_qty,
                mq,
                parent.id,
            )
            return False

        close_side = Side.SELL if venue_qty > 0 else Side.BUY
        recover_ix = max(80, min(98, 80 + slice_index))
        claw = ChildOrder(
            id=new_client_order_id(parent.id, recover_ix),
            parent_id=parent.id,
            symbol=parent.symbol,
            side=close_side,
            qty=mq,
            price=None,
            order_type=OrderType.MARKET,
            reduce_only=True,
        )
        try:
            placed = await self._om.submit_child(claw)
        except Exception as exc2:  # noqa: BLE001
            if _is_reduce_only_reject(exc2):
                await self._notify_venue_flat(
                    parent.symbol, parent.id, context="flat_after_claw_race",
                )
                return True
            raise
        await self._await_terminal(placed.id)
        logger.info(
            "flatten reduceOnly recovered via market clawback %s qty=%.8f parent=%s",
            parent.symbol,
            mq,
            parent.id,
        )
        return True

    async def _submit_slice(self, parent: ParentOrder, slc: Slice) -> None:
        price = self._passive_price(parent)
        order_type = OrderType.LIMIT if price is not None else OrderType.MARKET
        if price is not None and self._limit_collar_check is not None:
            mid = self._features.snapshot(parent.symbol).mid
            ref_mid = mid if mid and mid > 0 else self._price(parent.symbol)
            if ref_mid and ref_mid > 0:
                ok, reason = self._limit_collar_check(parent.symbol, price, ref_mid)
                if not ok:
                    logger.warning(
                        "limit collar veto parent=%s slice=%d: %s",
                        parent.id,
                        slc.index,
                        reason,
                    )
                    return
        # Re-validate the child against venue constraints right before submit.
        # This prevents hard REST rejections (e.g. MIN_NOTIONAL) from bubbling
        # out of the gateway and spamming logs.
        filters = self._gateway.get_symbol_filters(parent.symbol)
        ref_price = price if price is not None else self._price(parent.symbol)
        is_market = order_type is OrderType.MARKET
        slice_qty = venue_cap_qty(slc.qty, filters, market_order=is_market)
        if not _slice_satisfies(slice_qty, filters, ref_price, reduce_only=parent.reduce_only):
            raise ValueError(
                f"slice qty violates venue filters (symbol={parent.symbol} qty={slice_qty:.10f} "
                f"ref_price={'-' if ref_price is None else f'{ref_price:.8f}'} filters={filters})"
            )
        child = ChildOrder(
            id=new_client_order_id(parent.id, slc.index),
            parent_id=parent.id,
            symbol=parent.symbol,
            side=parent.side,
            qty=slice_qty,
            price=price,
            order_type=order_type,
            reduce_only=parent.reduce_only,
        )
        try:
            placed = await self._om.submit_child(child)
        except Exception as exc:
            if (
                _is_reduce_only_reject(exc)
                and await self._try_reduce_only_recovery_after_reject(
                    parent,
                    slice_index=slc.index,
                    ref_price=ref_price,
                )
            ):
                raise _FlattenRecoverComplete
            if parent.reduce_only or _is_reduce_only_reject(exc):
                logger.warning(
                    "slice aborted (reduce_only) parent=%s slice=%d: %s",
                    parent.id,
                    slc.index,
                    exc,
                )
                raise
            # If the passive LIMIT is rejected (or REST submit fails), fall back to
            # a MARKET order for the slice so the strategy continues to function
            # under exchange realities (partial/no fill/reject).
            if order_type is OrderType.LIMIT and self._cfg.market_fallback:
                market = ChildOrder(
                    id=new_client_order_id(parent.id, min(slc.index + 50, 99)),
                    parent_id=parent.id,
                    symbol=parent.symbol,
                    side=parent.side,
                    qty=slice_qty,
                    price=None,
                    order_type=OrderType.MARKET,
                    reduce_only=parent.reduce_only,
                )
                placed = await self._om.submit_child(market)
                await self._await_terminal(placed.id)
                return
            raise

        if order_type is OrderType.MARKET:
            await self._await_terminal(placed.id)
            return

        await self._await_fill_or_market(placed.id)

    async def _await_terminal(self, child_id: str) -> None:
        """Wait briefly for the exchange to terminalise the order."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + self._cfg.slice_timeout_sec
        poll = min(0.1, self._cfg.slice_timeout_sec / 2)
        while True:
            child = self._om.child(child_id)
            if child is None:
                return
            if child.status in TERMINAL_ORDER_STATUSES:
                return
            if loop.time() >= deadline:
                return
            await asyncio.sleep(poll)

    async def _await_fill_or_market(self, child_id: str) -> None:
        """Wait up to `slice_timeout_sec` for the limit to fill; market the residual."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + self._cfg.slice_timeout_sec
        # Cap the poll interval at 100ms so short timeouts (used by tests
        # and the live mid-flight cancellation path) actually behave as
        # advertised. Mock gateways report fills synchronously; the loop
        # body will catch FILLED on its first iteration in that case.
        poll = min(0.1, self._cfg.slice_timeout_sec / 2)
        while True:
            child = self._om.child(child_id)
            if child is None:
                return
            if child.status in TERMINAL_ORDER_STATUSES:
                return
            if loop.time() >= deadline:
                break
            await asyncio.sleep(poll)
        if not self._cfg.market_fallback:
            return
        if child.reduce_only:
            return

        await self._om.cancel(child_id)
        # Re-read after cancel request so residual is based on the latest
        # exchange-reported filled_qty (partial fills can race the cancel).
        child_after = self._om.child(child_id) or child
        residual = max(0.0, child_after.qty - child_after.filled_qty)
        if residual <= 0:
            return
        filt = self._gateway.get_symbol_filters(child_after.symbol)
        residual = venue_cap_qty(residual, filt)
        if residual <= 0:
            return
        market = ChildOrder(
            id=new_client_order_id(child_after.parent_id, 99),
            parent_id=child_after.parent_id,
            symbol=child_after.symbol,
            side=child_after.side,
            qty=residual,
            price=None,
            order_type=OrderType.MARKET,
            reduce_only=child_after.reduce_only,
        )
        placed = await self._om.submit_child(market)
        await self._await_terminal(placed.id)

    def _passive_price(self, parent: ParentOrder) -> float | None:
        """Pick a passive limit price on the resting side of the book.

        If the book isn't ready, fall back to a market order by returning None.
        """
        feat = self._features.snapshot(parent.symbol)
        if feat.mid is None or feat.spread_bps is None:
            return None
        if parent.side.value == "buy":
            price = feat.best_bid
        else:
            price = feat.best_ask
        if price is None:
            price = self._price(parent.symbol) or feat.mid
        if price is None or feat.mid is None:
            return None
        cap_bps = float(self._settings.max_limit_deviation_bps)
        if cap_bps > 0:
            dev_bps = abs(price - feat.mid) / feat.mid * 10_000.0
            if dev_bps > cap_bps:
                logger.warning(
                    "limit collar veto %s: dev=%.1fbps > cap=%.1fbps",
                    parent.symbol, dev_bps, cap_bps,
                )
                return None
            gateway_px = self._price(parent.symbol)
            if gateway_px and gateway_px > 0:
                gw_dev = abs(price - gateway_px) / gateway_px * 10_000.0
                if gw_dev > cap_bps:
                    logger.warning(
                        "passive price stale %s: book=%.4f mark=%.4f dev=%.1fbps",
                        parent.symbol,
                        price,
                        gateway_px,
                        gw_dev,
                    )
                    return None
        return price


def _slice_satisfies(
    qty: float,
    filters: SymbolFilters | None,
    ref_price: float | None,
    *,
    reduce_only: bool = False,
) -> bool:
    """Return True if `qty` clears the venue's per-order constraints."""
    return venue_qty_in_bounds(
        qty, filters, ref_price, reduce_only=reduce_only,
    )


def _single_shot_delay(mode: AlgoMode, duration_sec: float) -> float:
    """Delay used when we can't slice and must place one parent-sized child.

    FRONTLOAD => as early as possible
    BACKLOAD  => as late as possible (but before the schedule ends)
    NORMAL    => midpoint
    """
    if duration_sec <= 0:
        return 0.0
    if mode is AlgoMode.FRONTLOAD:
        return 0.0
    if mode is AlgoMode.BACKLOAD:
        return max(0.0, duration_sec * 0.9)
    return max(0.0, duration_sec * 0.5)
