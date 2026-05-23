"""Own resting MM quotes and entry ledger."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import time

from common.enums import OrderStatus, Side
from common.types import ChildOrder, Fill

MM_QUOTE_NOTE_PREFIX = "mm-quote"


@dataclass(slots=True)
class OwnLevel:
    price: float
    qty: float
    child_id: str
    posted_ts: float = 0.0


@dataclass(slots=True)
class EntryLedger:
    entry_mid: float = 0.0
    entry_qty: float = 0.0
    open_side: int = 0
    opened_ts: float = 0.0


@dataclass(slots=True)
class OwnBookState:
    symbol: str
    own_bid: OwnLevel | None = None
    own_ask: OwnLevel | None = None
    ledger: EntryLedger = field(default_factory=EntryLedger)
    last_fill_side: str = ""
    last_fill_adverse_bps: float = 0.0
    markout_cooldown_until: float = 0.0
    consecutive_buy_fills: int = 0
    consecutive_sell_fills: int = 0
    halt_bid_until: float = 0.0
    halt_ask_until: float = 0.0
    exit_limit_since: float = 0.0
    vol_regime_halt_until: float = 0.0

    @property
    def own_bid_qty(self) -> float:
        return self.own_bid.qty if self.own_bid else 0.0

    @property
    def own_ask_qty(self) -> float:
        return self.own_ask.qty if self.own_ask else 0.0


def is_mm_quote_child(child: ChildOrder) -> bool:
    return child.parent_id.startswith("Q-") or MM_QUOTE_NOTE_PREFIX in (child.parent_id or "")


def is_mm_quote_parent(parent_id: str, notes: str = "") -> bool:
    return parent_id.startswith("Q-") or notes.startswith(MM_QUOTE_NOTE_PREFIX)


class OwnQuoteBook:
    def __init__(self, markout_cooldown_sec: float = 15.0) -> None:
        self._states: dict[str, OwnBookState] = {}
        self._markout_cooldown = max(0.0, markout_cooldown_sec)

    def set_markout_cooldown_sec(self, sec: float) -> None:
        self._markout_cooldown = max(0.0, sec)

    def state(self, symbol: str) -> OwnBookState:
        sym = symbol.upper()
        st = self._states.get(sym)
        if st is None:
            st = OwnBookState(symbol=sym)
            self._states[sym] = st
        return st

    def sync_working(
        self,
        symbol: str,
        children: list[ChildOrder],
    ) -> OwnBookState:
        st = self.state(symbol)
        st.own_bid = None
        st.own_ask = None
        for c in children:
            if c.status not in (
                OrderStatus.NEW,
                OrderStatus.ACK,
                OrderStatus.PARTIAL,
            ):
                continue
            if c.price is None or c.qty <= 0:
                continue
            lvl = OwnLevel(
                price=c.price,
                qty=c.qty - c.filled_qty,
                child_id=c.id,
                posted_ts=c.created_at,
            )
            if c.side is Side.BUY:
                st.own_bid = lvl
            else:
                st.own_ask = lvl
        return st

    def set_session_levels(
        self,
        symbol: str,
        *,
        bid: OwnLevel | None,
        ask: OwnLevel | None,
    ) -> None:
        st = self.state(symbol)
        st.own_bid = bid
        st.own_ask = ask

    def on_level_fill(
        self,
        symbol: str,
        fill: Fill,
        *,
        position_qty: float,
        adverse_bps: float = 0.0,
    ) -> OwnBookState:
        st = self.state(symbol)
        st.last_fill_side = fill.side.value
        st.last_fill_adverse_bps = adverse_bps
        if adverse_bps > 0 and self._markout_cooldown > 0:
            st.markout_cooldown_until = time() + self._markout_cooldown
        if fill.side.value == "buy":
            st.consecutive_buy_fills += 1
            st.consecutive_sell_fills = 0
        elif fill.side.value == "sell":
            st.consecutive_sell_fills += 1
            st.consecutive_buy_fills = 0

        side = 1 if position_qty > 1e-12 else (-1 if position_qty < -1e-12 else 0)
        if side == 0:
            st.halt_bid_until = 0.0
            st.halt_ask_until = 0.0
            st.exit_limit_since = 0.0
            st.consecutive_buy_fills = 0
            st.consecutive_sell_fills = 0
        st.ledger.open_side = side
        if side == 0:
            st.ledger.entry_mid = 0.0
            st.ledger.entry_qty = 0.0
            st.ledger.opened_ts = 0.0
        elif st.ledger.opened_ts <= 0:
            st.ledger.opened_ts = time()
            st.ledger.entry_mid = fill.price
            st.ledger.entry_qty = abs(position_qty)
        else:
            prev_q = st.ledger.entry_qty
            new_q = abs(position_qty)
            if new_q > prev_q + 1e-12:
                st.ledger.entry_mid = (
                    st.ledger.entry_mid * prev_q + fill.price * (new_q - prev_q)
                ) / new_q
                if fill.side.value == "buy":
                    st.halt_bid_until = time() + 3600.0
                elif fill.side.value == "sell":
                    st.halt_ask_until = time() + 3600.0
            st.ledger.entry_qty = new_q
        return st

    def unrealized_pnl_bps(self, symbol: str, mid: float, position_qty: float) -> float:
        st = self.state(symbol)
        entry = st.ledger.entry_mid
        if entry <= 0 or mid <= 0 or abs(position_qty) < 1e-12:
            return 0.0
        if position_qty > 0:
            return (mid - entry) / entry * 10_000.0
        return (entry - mid) / entry * 10_000.0
