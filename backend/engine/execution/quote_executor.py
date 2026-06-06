"""Post-only / multi-mode two-sided quote executor for market-making strategies."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from common.config import Settings
from common.enums import MmExecutionMode, OrderType, Side
from common.types import ChildOrder, ParentOrder, QuoteIntent
from gateways.gateway_interface import SymbolFilters

from ..market_data.own_quote_book import MM_QUOTE_NOTE_PREFIX, OwnLevel, OwnQuoteBook, is_mm_quote_child
from ..orders.order_manager import OrderManager, new_client_order_id
from ..orders.order_state_machine import WORKING_ORDER_STATUSES
from ..risk.venue_sizing import venue_cap_qty, venue_min_qty, venue_qty_in_bounds
from .mm_execution import (
    cancel_reason,
    chase_should_replace,
    climb_next_price,
    ladder_level_targets,
    parse_ladder_weights,
    resolve_execution_mode,
    tick_from_feat,
    within_place_zone,
)
from .quote_clamp import clamp_targets_no_cross

logger = logging.getLogger(__name__)

SymbolFiltersFor = Callable[[str], SymbolFilters | None]
RejectCallback = Callable[[str, Side, int | None], None]


def _new_quote_parent_id(symbol: str) -> str:
    return f"Q-{symbol[:6]}-{uuid.uuid4().hex[:8]}"


@dataclass(slots=True)
class _WorkingQuote:
    parent_id: str
    child_id: str
    side: Side
    price: float
    qty: float
    level_index: int = 0
    posted_ts: float = 0.0


@dataclass(slots=True)
class _SymbolSession:
    bid_levels: list[_WorkingQuote] = field(default_factory=list)
    ask_levels: list[_WorkingQuote] = field(default_factory=list)


class QuoteExecutor:
    def __init__(
        self,
        order_manager: OrderManager,
        own_book: OwnQuoteBook,
        settings: Settings,
        *,
        symbol_filters: SymbolFiltersFor | None = None,
        on_venue_reject: RejectCallback | None = None,
    ) -> None:
        self._om = order_manager
        self._own = own_book
        self._settings = settings
        self._symbol_filters = symbol_filters
        self._on_reject = on_venue_reject
        self._sessions: dict[str, _SymbolSession] = {}

    def apply_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._own.set_markout_cooldown_sec(settings.mm_markout_cooldown_sec)

    async def refresh(self, intents: list[QuoteIntent]) -> None:
        if not self._settings.mm_quote_enabled:
            return
        n = 0
        max_n = max(0, int(self._settings.mm_quote_max_refresh_per_tick))
        for intent in intents:
            if max_n > 0 and n >= max_n:
                break
            if await self._refresh_symbol(intent):
                n += 1

    async def cancel_all(self, symbols: list[str] | None = None) -> None:
        targets = symbols or list(self._sessions.keys())
        for sym in targets:
            sess = self._sessions.pop(sym.upper(), None)
            if sess is None:
                continue
            for w in (*sess.bid_levels, *sess.ask_levels):
                await self._om.cancel(w.child_id)

    def clear_sessions(self, symbols: list[str] | None = None) -> None:
        if symbols is None:
            self._sessions.clear()
            return
        for sym in symbols:
            self._sessions.pop(sym.upper(), None)

    def prune_symbol(self, symbol: str) -> None:
        """Drop session entries whose child orders are no longer working."""
        sym = symbol.upper()
        sess = self._sessions.get(sym)
        if sess is None:
            return
        self._prune_stale_levels(sess.bid_levels)
        self._prune_stale_levels(sess.ask_levels)

    def seed_sessions_from_oms(self, symbols: Iterable[str] | None = None) -> None:
        """Adopt OMS working MM orders into empty executor sessions (startup)."""
        if symbols is None:
            seen: set[str] = set()
            for child in self._om.working_children():
                if is_mm_quote_child(child):
                    seen.add(child.symbol.upper())
            targets = sorted(seen)
        else:
            targets = [s.upper() for s in symbols]
        for sym in targets:
            self._seed_symbol_from_oms(sym)

    def _is_child_working(self, child_id: str) -> bool:
        child = self._om.child(child_id)
        return child is not None and child.status in WORKING_ORDER_STATUSES

    def _prune_stale_levels(self, levels: list[_WorkingQuote]) -> None:
        levels[:] = [w for w in levels if self._is_child_working(w.child_id)]

    def _seed_symbol_from_oms(self, symbol: str) -> None:
        sym = symbol.upper()
        sess = self._sessions.get(sym)
        if sess is not None and (sess.bid_levels or sess.ask_levels):
            self._prune_stale_levels(sess.bid_levels)
            self._prune_stale_levels(sess.ask_levels)
            if sess.bid_levels or sess.ask_levels:
                return
        children = [
            c
            for c in self._om.working_children()
            if c.symbol.upper() == sym and is_mm_quote_child(c) and c.price is not None and c.qty > 0
        ]
        if not children:
            return
        sess = self._sessions.setdefault(sym, _SymbolSession())
        for child in children:
            remaining = child.qty - child.filled_qty
            if remaining <= 0:
                continue
            wq = _WorkingQuote(
                child.parent_id,
                child.id,
                child.side,
                child.price,
                remaining,
                level_index=0,
                posted_ts=child.created_at or time.time(),
            )
            if child.side is Side.BUY:
                sess.bid_levels.append(wq)
            else:
                sess.ask_levels.append(wq)

    async def _refresh_symbol(self, intent: QuoteIntent) -> bool:
        sym = intent.symbol.upper()
        sess = self._sessions.setdefault(sym, _SymbolSession())
        self.prune_symbol(sym)
        tick = tick_from_feat(
            intent.best_bid,
            intent.best_ask,
            intent.venue_mid,
            intent.spread_bps,
        )
        bid_p, ask_p = clamp_targets_no_cross(
            intent.bid_price,
            intent.ask_price,
            best_bid=intent.best_bid,
            best_ask=intent.best_ask,
            tick=tick,
        )
        changed = False
        if await self._sync_side(
            sym, sess, Side.BUY, bid_p, intent.bid_qty, intent, tick_hint=tick
        ):
            changed = True
        if await self._sync_side(
            sym, sess, Side.SELL, ask_p, intent.ask_qty, intent, tick_hint=tick
        ):
            changed = True
        self._own.set_session_levels(
            sym,
            bid=_aggregate_level(sess.bid_levels),
            ask=_aggregate_level(sess.ask_levels),
        )
        return changed

    async def _sync_side(
        self,
        symbol: str,
        sess: _SymbolSession,
        side: Side,
        price: float | None,
        qty: float,
        intent: QuoteIntent,
        *,
        tick_hint: float,
    ) -> bool:
        levels = sess.bid_levels if side is Side.BUY else sess.ask_levels
        reduce_only = intent.reduce_only_bid if side is Side.BUY else intent.reduce_only_ask
        take_flag = intent.take_bid if side is Side.BUY else intent.take_ask
        intent_mode = intent.bid_execution_mode if side is Side.BUY else intent.ask_execution_mode
        mode = resolve_execution_mode(intent_mode, self._settings, side=side, take_flag=take_flag)

        if price is None or qty <= 0:
            if levels:
                for w in levels:
                    await self._om.cancel(w.child_id)
                levels.clear()
                return True
            return False

        best_bid = intent.best_bid
        best_ask = intent.best_ask
        tick = tick_hint
        if best_bid and best_ask:
            clamped_b, clamped_a = clamp_targets_no_cross(
                price if side is Side.BUY else None,
                price if side is Side.SELL else None,
                best_bid=best_bid,
                best_ask=best_ask,
                tick=tick,
            )
            price = clamped_b if side is Side.BUY else clamped_a
            if price is None:
                return False

        if mode is MmExecutionMode.TAKE:
            take_px = intent.take_bid_price if side is Side.BUY else intent.take_ask_price
            return await self._sync_take(
                symbol, sess, side, take_px or price, qty, intent, reduce_only=reduce_only
            )

        if mode in (MmExecutionMode.LADDER, MmExecutionMode.CLIMB_MULTI):
            return await self._sync_ladder(
                symbol,
                sess,
                side,
                price,
                qty,
                intent,
                reduce_only=reduce_only,
                climb=(mode is MmExecutionMode.CLIMB_MULTI),
                best_bid=best_bid,
                best_ask=best_ask,
                tick=tick,
            )

        if mode is MmExecutionMode.CLIMB:
            return await self._sync_climb(
                symbol,
                sess,
                side,
                price,
                qty,
                intent,
                reduce_only=reduce_only,
                best_bid=best_bid,
                best_ask=best_ask,
                tick=tick,
            )

        return await self._sync_single(
            symbol,
            sess,
            side,
            price,
            qty,
            intent,
            reduce_only=reduce_only,
            chase=(mode is MmExecutionMode.CHASE),
            best_bid=best_bid,
            best_ask=best_ask,
            tick=tick,
        )

    async def _sync_single(
        self,
        symbol: str,
        sess: _SymbolSession,
        side: Side,
        price: float,
        qty: float,
        intent: QuoteIntent,
        *,
        reduce_only: bool,
        chase: bool,
        best_bid: float | None,
        best_ask: float | None,
        tick: float,
    ) -> bool:
        levels = sess.bid_levels if side is Side.BUY else sess.ask_levels
        self._prune_stale_levels(levels)

        while len(levels) > 1:
            extra = levels.pop()
            await self._om.cancel(extra.child_id)

        place_bps = float(self._settings.mm_place_range_bps)
        cancel_bps = float(self._settings.mm_cancel_range_bps)
        now = time.time()
        working = levels[0] if levels else None

        if working is not None:
            child = self._om.child(working.child_id)
            if child is not None:
                working.qty = max(0.0, child.qty - child.filled_qty)

        if working is not None:
            reason = cancel_reason(
                side,
                working.price,
                price,
                best_bid=best_bid,
                best_ask=best_ask,
                cancel_range_bps=cancel_bps,
                posted_ts=working.posted_ts,
                min_rest_sec=float(self._settings.mm_quote_min_rest_sec),
                now=now,
            )
            refresh_bps = float(self._settings.mm_quote_refresh_bps)
            if reason is not None:
                await self._om.cancel(working.child_id)
                levels.clear()
                working = None
            elif chase and not chase_should_replace(
                working.price, working.qty, price, qty, refresh_bps
            ):
                return False
            elif not chase and _quote_unchanged(working, price, qty, tick):
                return False
            else:
                if chase and chase_should_replace(working.price, working.qty, price, qty, refresh_bps):
                    await self._om.cancel(working.child_id)
                    levels.clear()
                    working = None
                elif not chase:
                    return False

        if not within_place_zone(
            side, price, best_bid=best_bid, best_ask=best_ask, place_range_bps=place_bps
        ):
            return False

        placed = await self._place_level(
            symbol, side, price, qty, intent, reduce_only=reduce_only, level_index=0
        )
        if placed is None:
            return False
        if side is Side.BUY:
            sess.bid_levels = [placed]
        else:
            sess.ask_levels = [placed]
        return True

    async def _sync_climb(
        self,
        symbol: str,
        sess: _SymbolSession,
        side: Side,
        target: float,
        qty: float,
        intent: QuoteIntent,
        *,
        reduce_only: bool,
        best_bid: float | None,
        best_ask: float | None,
        tick: float,
    ) -> bool:
        levels = sess.bid_levels if side is Side.BUY else sess.ask_levels
        self._prune_stale_levels(levels)
        for extra in levels[1:]:
            await self._om.cancel(extra.child_id)
        del levels[1:]
        climb_ticks = max(1, int(self._settings.mm_climb_ticks_per_refresh))
        working = levels[0] if levels else None
        place_bps = float(self._settings.mm_place_range_bps)
        cancel_bps = float(self._settings.mm_cancel_range_bps)
        now = time.time()

        if working is not None:
            reason = cancel_reason(
                side,
                working.price,
                target,
                best_bid=best_bid,
                best_ask=best_ask,
                cancel_range_bps=cancel_bps,
                posted_ts=working.posted_ts,
                min_rest_sec=float(self._settings.mm_quote_min_rest_sec),
                now=now,
            )
            if reason:
                await self._om.cancel(working.child_id)
                levels.clear()
                working = None
            else:
                next_px = climb_next_price(
                    side, working.price, target, tick=tick, climb_ticks=climb_ticks
                )
                if next_px is None or abs(next_px - working.price) < tick * 0.25:
                    return False
                await self._om.cancel(working.child_id)
                levels.clear()
                target = next_px

        if not within_place_zone(
            side, target, best_bid=best_bid, best_ask=best_ask, place_range_bps=place_bps
        ):
            return False

        placed = await self._place_level(
            symbol, side, target, qty, intent, reduce_only=reduce_only, level_index=0
        )
        if placed is None:
            return False
        if side is Side.BUY:
            sess.bid_levels = [placed]
        else:
            sess.ask_levels = [placed]
        return True

    async def _sync_ladder(
        self,
        symbol: str,
        sess: _SymbolSession,
        side: Side,
        target: float,
        qty: float,
        intent: QuoteIntent,
        *,
        reduce_only: bool,
        climb: bool,
        best_bid: float | None,
        best_ask: float | None,
        tick: float,
    ) -> bool:
        levels = sess.bid_levels if side is Side.BUY else sess.ask_levels
        self._prune_stale_levels(levels)
        n_levels = max(1, int(self._settings.mm_ladder_levels))
        spacing = max(1, int(self._settings.mm_ladder_spacing_ticks))
        weights = parse_ladder_weights(self._settings.mm_ladder_qty_weights, n_levels)
        targets = ladder_level_targets(
            side, target, qty, tick=tick, levels=n_levels, spacing_ticks=spacing, weights=weights
        )
        max_orders = max(2, int(self._settings.mm_max_working_orders_per_symbol))
        if len(targets) * 2 > max_orders:
            targets = targets[: max(1, max_orders // 2)]

        place_bps = float(self._settings.mm_place_range_bps)
        cancel_bps = float(self._settings.mm_cancel_range_bps)
        refresh_bps = float(self._settings.mm_quote_refresh_bps)
        climb_ticks = max(1, int(self._settings.mm_climb_ticks_per_refresh))
        now = time.time()
        working_by_idx = {w.level_index: w for w in levels}
        changed = False
        target_idxs = {t.level_index for t in targets}

        for w in list(levels):
            if w.level_index not in target_idxs:
                await self._om.cancel(w.child_id)
                levels.remove(w)
                changed = True

        for lt in targets:
            working = working_by_idx.get(lt.level_index)
            eff_price = lt.price
            if working is not None and climb:
                eff_price = climb_next_price(
                    side, working.price, lt.price, tick=tick, climb_ticks=climb_ticks
                ) or lt.price
            elif working is not None:
                eff_price = working.price

            if working is not None:
                reason = cancel_reason(
                    side,
                    working.price,
                    lt.price,
                    best_bid=best_bid,
                    best_ask=best_ask,
                    cancel_range_bps=cancel_bps,
                    posted_ts=working.posted_ts,
                    min_rest_sec=float(self._settings.mm_quote_min_rest_sec),
                    now=now,
                )
                if reason:
                    await self._om.cancel(working.child_id)
                    levels.remove(working)
                    working = None
                elif climb:
                    if abs(eff_price - working.price) < tick * 0.25:
                        continue
                    await self._om.cancel(working.child_id)
                    levels.remove(working)
                    working = None
                elif not chase_should_replace(
                    working.price, working.qty, lt.price, lt.qty, refresh_bps
                ):
                    continue
                else:
                    await self._om.cancel(working.child_id)
                    if working in levels:
                        levels.remove(working)
                    working = None

            px = eff_price if climb else lt.price
            if not within_place_zone(
                side, px, best_bid=best_bid, best_ask=best_ask, place_range_bps=place_bps
            ):
                continue

            placed = await self._place_level(
                symbol,
                side,
                px,
                lt.qty,
                intent,
                reduce_only=reduce_only,
                level_index=lt.level_index,
            )
            if placed is not None:
                levels.append(placed)
                changed = True

        if side is Side.BUY:
            sess.bid_levels = sorted(levels, key=lambda w: w.level_index)
        else:
            sess.ask_levels = sorted(levels, key=lambda w: w.level_index)
        return changed

    async def _sync_take(
        self,
        symbol: str,
        sess: _SymbolSession,
        side: Side,
        price: float,
        qty: float,
        intent: QuoteIntent,
        *,
        reduce_only: bool,
    ) -> bool:
        levels = sess.bid_levels if side is Side.BUY else sess.ask_levels
        for w in levels:
            await self._om.cancel(w.child_id)
        levels.clear()

        sized = self._size_quote_qty(symbol, qty, price, reduce_only)
        if sized is None:
            return False

        parent_id = _new_quote_parent_id(symbol)
        child_id = new_client_order_id(parent_id, 0 if side is Side.BUY else 1, prefix="MMQ")
        child = ChildOrder(
            id=child_id,
            parent_id=parent_id,
            symbol=symbol,
            side=side,
            qty=sized,
            price=price,
            order_type=OrderType.LIMIT,
            reduce_only=reduce_only,
            time_in_force="IOC",
            post_only=False,
        )
        parent = ParentOrder(
            id=parent_id,
            symbol=symbol,
            side=side,
            qty=sized,
            notes=f"{MM_QUOTE_NOTE_PREFIX} take {intent.reason[:180]}",
            reduce_only=reduce_only,
            strategy_name=intent.strategy_name,
        )
        self._om.register_parent(parent)
        try:
            placed = await self._om.submit_child(child)
        except Exception as exc:
            self._handle_reject(symbol, side, exc)
            return False
        wq = _WorkingQuote(
            parent_id, placed.id, side, price, sized, level_index=0, posted_ts=time.time()
        )
        levels.append(wq)
        logger.info("MM take %s %s @ %.6f qty=%.8f", symbol, side.value, price, sized)
        return True

    async def _place_level(
        self,
        symbol: str,
        side: Side,
        price: float,
        qty: float,
        intent: QuoteIntent,
        *,
        reduce_only: bool,
        level_index: int,
    ) -> _WorkingQuote | None:
        sized_qty = self._size_quote_qty(symbol, qty, price, reduce_only)
        if sized_qty is None:
            return None

        parent_id = _new_quote_parent_id(symbol)
        child_id = new_client_order_id(
            parent_id, level_index if side is Side.BUY else 10 + level_index, prefix="MMQ"
        )
        child = ChildOrder(
            id=child_id,
            parent_id=parent_id,
            symbol=symbol,
            side=side,
            qty=sized_qty,
            price=price,
            order_type=OrderType.LIMIT,
            reduce_only=reduce_only,
            post_only=True,
        )
        parent = ParentOrder(
            id=parent_id,
            symbol=symbol,
            side=side,
            qty=sized_qty,
            notes=f"{MM_QUOTE_NOTE_PREFIX} {intent.reason[:200]}",
            reduce_only=reduce_only,
            strategy_name=intent.strategy_name,
        )
        self._om.register_parent(parent)
        try:
            placed = await self._om.submit_child(child)
        except Exception as exc:
            self._handle_reject(symbol, side, exc)
            return None
        return _WorkingQuote(
            parent_id,
            placed.id,
            side,
            price,
            sized_qty,
            level_index=level_index,
            posted_ts=time.time(),
        )

    def _handle_reject(self, symbol: str, side: Side, exc: Exception) -> None:
        code = getattr(exc, "code", None)
        status = getattr(exc, "status", None)
        if code == -4164:
            logger.debug("MM quote skipped %s %s: below min notional", symbol, side.value)
        elif code == -2022:
            logger.warning("MM quote reduce_only rejected %s %s: %s", symbol, side.value, exc)
            if self._on_reject is not None:
                self._on_reject(symbol, side, code)
        elif code in (-4116, -1003) or status in (0, 418):
            logger.warning("MM quote place failed %s %s: %s", symbol, side.value, exc)
            if self._on_reject is not None:
                self._on_reject(symbol, side, code)
        else:
            raise

    def _size_quote_qty(
        self,
        symbol: str,
        qty: float,
        price: float,
        reduce_only: bool,
    ) -> float | None:
        if qty <= 0 or price <= 0:
            return None
        if self._symbol_filters is None:
            return qty
        filters = self._symbol_filters(symbol)
        if filters is None:
            return qty
        if not reduce_only:
            floor = venue_min_qty(mid=price, filters=filters)
            if floor is not None:
                qty = max(qty, floor)
        qty = venue_cap_qty(qty, filters)
        if qty <= 0:
            return None
        if not venue_qty_in_bounds(qty, filters, price, reduce_only=reduce_only):
            return None
        return qty


def _aggregate_level(levels: list[_WorkingQuote]) -> OwnLevel | None:
    if not levels:
        return None
    best = max(levels, key=lambda w: w.price) if levels[0].side is Side.BUY else min(
        levels, key=lambda w: w.price
    )
    total_qty = sum(w.qty for w in levels)
    return OwnLevel(best.price, total_qty, best.child_id, posted_ts=best.posted_ts)


def _quote_unchanged(
    working: _WorkingQuote,
    price: float,
    qty: float,
    tick: float,
) -> bool:
    tol = max(tick * 0.5, 1e-12)
    if abs(working.price - price) >= tol:
        return False
    if working.qty > 0 and abs(qty - working.qty) / working.qty >= 0.05:
        return False
    return True
