"""Post-only two-sided quote executor for market-making strategies."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from common.config import Settings
from common.enums import OrderType, Side
from common.types import ChildOrder, ParentOrder, QuoteIntent

from ..market_data.own_quote_book import MM_QUOTE_NOTE_PREFIX, OwnLevel, OwnQuoteBook
from ..orders.order_manager import OrderManager, new_client_order_id

logger = logging.getLogger(__name__)


def _new_quote_parent_id(symbol: str) -> str:
    return f"Q-{symbol[:6]}-{uuid.uuid4().hex[:8]}"


@dataclass(slots=True)
class _WorkingQuote:
    parent_id: str
    child_id: str
    side: Side
    price: float
    qty: float


@dataclass(slots=True)
class _SymbolSession:
    bid: _WorkingQuote | None = None
    ask: _WorkingQuote | None = None


class QuoteExecutor:
    def __init__(
        self,
        order_manager: OrderManager,
        own_book: OwnQuoteBook,
        settings: Settings,
    ) -> None:
        self._om = order_manager
        self._own = own_book
        self._settings = settings
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
            for w in (sess.bid, sess.ask):
                if w is not None:
                    await self._om.cancel(w.child_id)

    async def _refresh_symbol(self, intent: QuoteIntent) -> bool:
        sym = intent.symbol.upper()
        sess = self._sessions.setdefault(sym, _SymbolSession())
        changed = False
        if await self._sync_side(sym, sess, Side.BUY, intent.bid_price, intent.bid_qty, intent):
            changed = True
        if await self._sync_side(sym, sess, Side.SELL, intent.ask_price, intent.ask_qty, intent):
            changed = True
        self._own.set_session_levels(
            sym,
            bid=OwnLevel(sess.bid.price, sess.bid.qty, sess.bid.child_id) if sess.bid else None,
            ask=OwnLevel(sess.ask.price, sess.ask.qty, sess.ask.child_id) if sess.ask else None,
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
    ) -> bool:
        working = sess.bid if side is Side.BUY else sess.ask
        reduce_only = intent.reduce_only_bid if side is Side.BUY else intent.reduce_only_ask

        if price is None or qty <= 0:
            if working is not None:
                await self._om.cancel(working.child_id)
                if side is Side.BUY:
                    sess.bid = None
                else:
                    sess.ask = None
                return True
            return False

        refresh_bps = float(self._settings.mm_quote_refresh_bps)
        if working is not None:
            if working.qty > 0 and working.price > 0:
                move_bps = abs(price - working.price) / working.price * 10_000.0
                if move_bps < refresh_bps and abs(qty - working.qty) / working.qty < 0.05:
                    return False
            await self._om.cancel(working.child_id)

        parent_id = _new_quote_parent_id(symbol)
        child_id = new_client_order_id(parent_id, 0 if side is Side.BUY else 1, prefix="MMQ")
        child = ChildOrder(
            id=child_id,
            parent_id=parent_id,
            symbol=symbol,
            side=side,
            qty=qty,
            price=price,
            order_type=OrderType.LIMIT,
            reduce_only=reduce_only,
        )
        parent = ParentOrder(
            id=parent_id,
            symbol=symbol,
            side=side,
            qty=qty,
            notes=f"{MM_QUOTE_NOTE_PREFIX} {intent.reason[:200]}",
            reduce_only=reduce_only,
            strategy_name=intent.strategy_name,
        )
        self._om.register_parent(parent)
        placed = await self._om.submit_child(child)
        wq = _WorkingQuote(parent_id, placed.id, side, price, qty)
        if side is Side.BUY:
            sess.bid = wq
        else:
            sess.ask = wq
        logger.info(
            "MM quote %s %s @ %.6f qty=%.8f ro=%s %s",
            symbol,
            side.value,
            price,
            qty,
            reduce_only,
            intent.reason[:120],
        )
        return True
