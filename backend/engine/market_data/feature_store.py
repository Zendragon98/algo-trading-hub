"""Snapshot of per-symbol features consumed by strategies + algo wheel."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import time

from common.config import Settings

from ..position.venue_pnl import inventory_pnl_bps
from ..strategies.position_sync import VenuePosition
from .funding_store import FundingRateStore
from .microstructure_hub import MicrostructureHub
from .orderbook import OrderBookStore
from .own_quote_book import OwnBookState
from .trade_tape import TradeTape


@dataclass(slots=True)
class Features:
    """Microstructure features for a single symbol."""

    symbol: str
    ts: float = field(default_factory=time)
    mid: float | None = None
    spread_bps: float | None = None
    micro_price: float | None = None
    imbalance_topn: float = 0.0
    bid_hit_ratio: float = 0.0
    ask_hit_ratio: float = 0.0
    tape_bid_hit_count: int = 0
    tape_ask_hit_count: int = 0
    last_price: float | None = None
    best_bid: float | None = None
    best_ask: float | None = None
    mid_return_1s_bps: float = 0.0
    vol_ewma_bps: float = 0.0
    vol_5m_bps: float = 0.0
    vol_1h_bps: float = 0.0
    jump_active: bool = False
    vpin: float = 0.5
    tape_velocity: float = 0.0
    large_trade_share: float = 0.0
    markout_adverse_ewma_bps: float = 0.0
    toxicity_score: float = 0.0
    is_toxic: bool = False
    toxicity_flow_direction: float = 0.0
    bid_depth_ratio: float = 1.0
    ask_depth_ratio: float = 1.0
    bid_depletion_score: float = 0.0
    ask_depletion_score: float = 0.0
    depth_depletion_asym: float = 0.0
    inventory_ratio: float = 0.0
    own_bid_price: float | None = None
    own_ask_price: float | None = None
    own_bid_qty: float = 0.0
    own_ask_qty: float = 0.0
    entry_mid: float = 0.0
    unrealized_pnl_bps: float = 0.0
    funding_rate_bps: float = 0.0
    funding_carry_bps: float = 0.0
    md_ready: bool = False


class FeatureStore:
    """Read-through view onto order book, tape, and microstructure hub."""

    def __init__(
        self,
        books: OrderBookStore,
        tape: TradeTape,
        settings: Settings,
        hub: MicrostructureHub | None = None,
        funding: FundingRateStore | None = None,
    ) -> None:
        self._books = books
        self._tape = tape
        self._top_n = settings.imbalance_top_n
        self._hub = hub or MicrostructureHub(books, tape, settings)
        self._funding = funding
        self._settings = settings

    def apply_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._top_n = settings.imbalance_top_n
        self._hub.apply_settings(settings)

    @property
    def hub(self) -> MicrostructureHub:
        return self._hub

    def snapshot(
        self,
        symbol: str,
        *,
        own: OwnBookState | None = None,
        position_qty: float = 0.0,
        equity: float = 0.0,
        venue: VenuePosition | None = None,
        fill_vwap: float = 0.0,
    ) -> Features:
        own_bid_q = own.own_bid_qty if own else 0.0
        own_ask_q = own.own_ask_qty if own else 0.0
        ms = self._hub.snapshot(symbol, own_bid_qty=own_bid_q, own_ask_qty=own_ask_q)
        book = self._books.get(symbol)
        mid = book.mid() if book.ready() else None

        feat = Features(
            symbol=symbol,
            bid_hit_ratio=ms.tape.bid_hit_ratio,
            ask_hit_ratio=ms.tape.ask_hit_ratio,
            tape_bid_hit_count=ms.tape.bid_hit_count,
            tape_ask_hit_count=ms.tape.ask_hit_count,
            last_price=ms.tape.last_price,
            mid_return_1s_bps=ms.mid.return_1s_bps,
            vol_ewma_bps=ms.mid.vol_ewma_bps,
            vol_5m_bps=ms.mid.vol_5m_bps,
            vol_1h_bps=ms.mid.vol_1h_bps,
            jump_active=ms.mid.jump_active,
            vpin=ms.tape.vpin,
            tape_velocity=ms.tape.trades_per_sec,
            large_trade_share=ms.tape.large_trade_share,
            markout_adverse_ewma_bps=ms.markout.adverse_ewma_bps,
            toxicity_score=ms.toxicity.toxicity_score,
            is_toxic=ms.toxicity.is_toxic,
            toxicity_flow_direction=ms.toxicity.flow_direction,
            bid_depth_ratio=ms.depletion.bid_depth_ratio,
            ask_depth_ratio=ms.depletion.ask_depth_ratio,
            bid_depletion_score=ms.depletion.bid_depletion_score,
            ask_depletion_score=ms.depletion.ask_depletion_score,
            depth_depletion_asym=ms.depletion.depth_depletion_asym,
        )

        feat.md_ready = book.ready()
        if not book.ready():
            return feat

        feat.mid = mid
        feat.spread_bps = book.spread_bps()
        feat.micro_price = book.micro_price(top_n=1)
        feat.imbalance_topn = book.imbalance(self._top_n)
        feat.best_bid = book.best_bid()
        feat.best_ask = book.best_ask()

        if self._funding is not None and self._settings.mm_funding_enabled:
            snap = self._funding.get(symbol)
            if snap is not None:
                feat.funding_rate_bps = snap.rate_bps
                feat.funding_carry_bps = snap.carry_bps

        if own is not None and mid and mid > 0:
            feat.own_bid_price = own.own_bid.price if own.own_bid else None
            feat.own_ask_price = own.own_ask.price if own.own_ask else None
            feat.own_bid_qty = own_bid_q
            feat.own_ask_qty = own_ask_q
            entry = fill_vwap if fill_vwap > 0 else own.ledger.entry_mid
            feat.entry_mid = entry
            pnl_bps, _ = inventory_pnl_bps(
                fill_entry=entry,
                book_mid=float(mid),
                position_qty=position_qty,
                venue=venue,
            )
            feat.unrealized_pnl_bps = pnl_bps
            feat.inventory_ratio = _inventory_ratio(
                position_qty,
                mid,
                equity,
                self._settings,
                own_bid_q,
                own_ask_q,
            )

        return feat


def unrealized_pnl_bps(entry_mid: float, mid: float, position_qty: float) -> float:
    """Legacy price-based bps — prefer ``inventory_pnl_bps`` for live paths."""
    if entry_mid <= 0 or mid <= 0 or abs(position_qty) < 1e-12:
        return 0.0
    if position_qty > 0:
        return (mid - entry_mid) / entry_mid * 10_000.0
    return (entry_mid - mid) / entry_mid * 10_000.0


def _inventory_ratio(
    position_qty: float,
    mid: float,
    equity: float,
    settings: Settings,
    own_bid_qty: float,
    own_ask_qty: float,
) -> float:
    notional_cap = float(settings.mm_max_inventory_notional)
    if notional_cap <= 0 and equity > 0:
        notional_cap = equity * float(settings.max_symbol_notional_pct)
    if notional_cap <= 0 or mid <= 0:
        return 0.0
    qty = position_qty
    if settings.mm_inventory_include_working:
        qty += own_bid_qty - own_ask_qty
    return (qty * mid) / notional_cap
