"""Shared institutional market-making logic."""

from __future__ import annotations

from dataclasses import dataclass

from common.config import Settings
from common.types import QuoteIntent

from ..market_data.feature_store import Features, unrealized_pnl_bps
from ..market_data.own_quote_book import EntryLedger, OwnBookState
from .mm_calibrated import mm_float
from .mm_symbol_params import MmSymbolQuoteParams, resolve_mm_params

MM_STRATEGY_NAMES = frozenset({"market_making", "market_making_v2"})


def is_mm_strategy(name: str) -> bool:
    return name in MM_STRATEGY_NAMES


def exit_pegged_price(mid: float, *, scratch_bps: float, reduce_long: bool) -> float:
    """Limit peg for inventory-reducing exit quotes (scratch inside the spread)."""
    if mid <= 0:
        return 0.0
    bps = max(0.0, scratch_bps) / 10_000.0
    return mid * (1.0 - bps) if reduce_long else mid * (1.0 + bps)


@dataclass(slots=True)
class SkewState:
    skew_avg: float | None = None


@dataclass(slots=True)
class MmQuotePricing:
    """How MM fair mid and spreads were derived from venue mid + inventory."""

    venue_mid: float
    reservation_mid: float
    inventory_ratio: float
    micro_shift_bps: float
    inventory_shift_bps: float
    bid_half_bps: float
    ask_half_bps: float
    bid_price: float | None
    ask_price: float | None


def inventory_ratio(
    position_qty: float,
    mid: float,
    settings: Settings,
    equity: float,
    *,
    own_bid_qty: float = 0.0,
    own_ask_qty: float = 0.0,
) -> float:
    if mid <= 0:
        return 0.0
    notional_cap = float(settings.mm_max_inventory_notional)
    if notional_cap <= 0 and equity > 0:
        notional_cap = equity * float(settings.max_symbol_notional_pct)
    if notional_cap <= 0:
        return 0.0
    qty = position_qty
    if settings.mm_inventory_include_working:
        qty += own_bid_qty - own_ask_qty
    return (qty * mid) / notional_cap


def tape_pressure(feat: Features, settings: Settings) -> float:
    mode = (settings.mm_tape_pressure_mode or "volume").strip().lower()
    if mode == "volume":
        total = feat.bid_hit_ratio + feat.ask_hit_ratio
        if total <= 1e-12:
            return 0.0
        return feat.ask_hit_ratio - feat.bid_hit_ratio
    total = feat.tape_bid_hit_count + feat.tape_ask_hit_count
    min_tr = max(1, int(settings.mm_min_tape_trades))
    if total < min_tr:
        return 0.0
    return (feat.tape_ask_hit_count - feat.tape_bid_hit_count) / float(total)


def build_microstructure_bias(
    feat: Features,
    settings: Settings,
    skew_avg: float | None,
) -> float:
    """Microstructure tilt only (skew, imbalance, tape, depletion) — no inventory."""
    sym = feat.symbol
    skew = float(skew_avg or 0.0)
    return (
        mm_float(sym, settings, "mm_skew_scale", cal_attr="skew_scale") * skew
        + mm_float(sym, settings, "mm_imbalance_scale", cal_attr="imbalance_scale")
        * float(feat.imbalance_topn)
        + mm_float(sym, settings, "mm_tape_scale", cal_attr="tape_scale")
        * tape_pressure(feat, settings)
        + mm_float(sym, settings, "mm_depletion_scale", cal_attr="depletion_scale")
        * float(feat.depth_depletion_asym)
    )


def compute_reservation_mid(
    venue_mid: float,
    *,
    feat: Features,
    settings: Settings,
    skew_avg: float | None,
    inv_ratio: float,
    params: MmSymbolQuoteParams | None = None,
) -> tuple[float, float, float]:
    """MM fair mid from venue mid + micro shift + inventory skew.

    Long inventory (inv_ratio > 0) lowers reservation mid so asks are more
    competitive and bids sit further away — encouraging inventory reduction.
    """
    if venue_mid <= 0:
        return 0.0, 0.0, 0.0
    sym = feat.symbol
    micro = build_microstructure_bias(feat, settings, skew_avg)
    micro_w = mm_float(sym, settings, "mm_reservation_micro_weight", cal_attr="reservation_micro_weight")
    micro_bps = (
        micro * micro_w
        + float(settings.mm_depletion_shift_bps) * float(feat.depth_depletion_asym)
    )
    p = params or resolve_mm_params(feat.symbol, settings, feat)
    inv_bps = -inv_ratio * p.reservation_inventory_bps
    total_bps = micro_bps + inv_bps
    reservation = venue_mid * (1.0 + total_bps / 10_000.0)
    return reservation, micro_bps, inv_bps


def compute_half_spreads_bps(
    feat: Features,
    settings: Settings,
    inv_ratio: float,
    params: MmSymbolQuoteParams | None = None,
) -> tuple[float, float]:
    """Half-spreads around reservation mid; widen the side that adds exposure."""
    p = params or resolve_mm_params(feat.symbol, settings, feat)
    base = p.half_spread_bps
    toxic_w = p.toxic_widen_bps * float(feat.toxicity_score)
    bid_half = base + toxic_w + p.depletion_widen_bps * feat.bid_depletion_score
    ask_half = base + toxic_w + p.depletion_widen_bps * feat.ask_depletion_score
    skew = p.inventory_spread_skew_bps
    min_half = max(0.5, base * 0.25)
    if inv_ratio > 0:
        bid_half += skew * inv_ratio
        ask_half = max(min_half, ask_half - skew * inv_ratio)
    elif inv_ratio < 0:
        ask_half += skew * (-inv_ratio)
        bid_half = max(min_half, bid_half - skew * (-inv_ratio))
    return bid_half, ask_half


def quote_prices_from_reservation(
    reservation_mid: float,
    bid_half_bps: float,
    ask_half_bps: float,
) -> tuple[float | None, float | None]:
    if reservation_mid <= 0:
        return None, None
    bid = reservation_mid * (1.0 - bid_half_bps / 10_000.0)
    ask = reservation_mid * (1.0 + ask_half_bps / 10_000.0)
    return bid, ask


def compute_quote_pricing(
    *,
    feat: Features,
    settings: Settings,
    skew_avg: float | None,
    inv_ratio: float,
    params: MmSymbolQuoteParams | None = None,
) -> MmQuotePricing:
    p = params or resolve_mm_params(feat.symbol, settings, feat)
    venue_mid = float(feat.mid or 0.0)
    reservation, micro_bps, inv_bps = compute_reservation_mid(
        venue_mid,
        feat=feat,
        settings=settings,
        skew_avg=skew_avg,
        inv_ratio=inv_ratio,
        params=p,
    )
    bid_half, ask_half = compute_half_spreads_bps(feat, settings, inv_ratio, params=p)
    bid_price, ask_price = quote_prices_from_reservation(reservation, bid_half, ask_half)
    return MmQuotePricing(
        venue_mid=venue_mid,
        reservation_mid=reservation,
        inventory_ratio=inv_ratio,
        micro_shift_bps=micro_bps,
        inventory_shift_bps=inv_bps,
        bid_half_bps=bid_half,
        ask_half_bps=ask_half,
        bid_price=bid_price,
        ask_price=ask_price,
    )


def entry_blocked(feat: Features, settings: Settings, *, want_long: bool) -> str | None:
    if feat.jump_active:
        return "jump"
    if feat.is_toxic:
        flow = feat.toxicity_flow_direction
        if want_long and flow > 0.2:
            return "toxic"
        if not want_long and flow < -0.2:
            return "toxic"
    markout_cap = mm_float(
        feat.symbol, settings, "mm_max_adverse_markout_bps", cal_attr="max_adverse_markout_bps"
    )
    if feat.markout_adverse_ewma_bps > markout_cap:
        return "markout"
    hard = float(settings.mm_inventory_hard_ratio)
    if hard > 0:
        if want_long and feat.inventory_ratio >= hard:
            return "inventory"
        if not want_long and feat.inventory_ratio <= -hard:
            return "inventory"
    return None


def plan_exit_reason(
    *,
    feat: Features,
    settings: Settings,
    own: OwnBookState,
    position_qty: float,
    mid: float,
) -> str | None:
    if abs(position_qty) < 1e-12:
        return None
    pnl_bps = _pnl_bps(own.ledger, mid, position_qty)

    max_hold = float(settings.mm_max_hold_sec)
    if max_hold > 0 and own.ledger.opened_ts > 0:
        import time as _time

        if _time.time() - own.ledger.opened_ts >= max_hold:
            return f"mm_time_exit pnl_bps={pnl_bps:.2f}"

    min_profit = mm_float(
        feat.symbol, settings, "mm_min_exit_profit_bps", cal_attr="min_exit_profit_bps"
    )
    if min_profit > 0 and pnl_bps >= min_profit:
        return f"mm_profit_exit pnl_bps={pnl_bps:.2f}"

    exit_ratio = float(settings.mm_inventory_exit_ratio)
    if exit_ratio > 0 and abs(feat.inventory_ratio) >= exit_ratio:
        return f"mm_inventory_exit inv={feat.inventory_ratio:.3f} pnl_bps={pnl_bps:.2f}"

    if feat.jump_active and settings.mm_jump_flatten:
        return "mm_jump_flatten"

    scratch = mm_float(feat.symbol, settings, "mm_scratch_loss_bps", cal_attr="scratch_loss_bps")
    if own.last_fill_adverse_bps >= scratch:
        return f"mm_adverse_level_fill bps={own.last_fill_adverse_bps:.2f}"

    return None


def _pnl_bps(ledger: EntryLedger, mid: float, position_qty: float) -> float:
    return unrealized_pnl_bps(ledger.entry_mid, mid, position_qty)


def compute_quote_intent(
    *,
    feat: Features,
    settings: Settings,
    own: OwnBookState,
    position_qty: float,
    equity: float,
    skew_avg: float | None,
    strategy_name: str,
    fee_round_trip_bps: float = 0.0,
) -> QuoteIntent:
    venue_mid = float(feat.mid or 0.0)
    inv = inventory_ratio(
        position_qty,
        venue_mid,
        settings,
        equity,
        own_bid_qty=own.own_bid_qty,
        own_ask_qty=own.own_ask_qty,
    )
    params = resolve_mm_params(feat.symbol, settings, feat)
    pricing = compute_quote_pricing(
        feat=feat,
        settings=settings,
        skew_avg=skew_avg,
        inv_ratio=inv,
        params=params,
    )
    bid_price = pricing.bid_price
    ask_price = pricing.ask_price

    pull = mm_float(
        feat.symbol, settings, "mm_depletion_pull_ratio", cal_attr="depletion_pull_ratio"
    )
    if feat.bid_depth_ratio < pull or feat.jump_active:
        bid_price = None
    if feat.ask_depth_ratio < pull or feat.jump_active:
        ask_price = None

    hard = float(settings.mm_inventory_hard_ratio)
    if hard > 0 and inv >= hard:
        bid_price = None
    if hard > 0 and inv <= -hard:
        ask_price = None

    import time as _time

    if _time.time() < own.markout_cooldown_until:
        if own.last_fill_side == "buy":
            ask_price = None
        elif own.last_fill_side == "sell":
            bid_price = None

    if entry_blocked(feat, settings, want_long=True):
        bid_price = None
    if entry_blocked(feat, settings, want_long=False):
        ask_price = None

    size_pct = params.size_pct if params.size_pct is not None else float(settings.mm_quote_size_pct)
    size_notional = equity * size_pct if equity > 0 else 0.0
    if size_notional <= 0:
        size_notional = float(settings.mm_qty) * venue_mid
    base_qty = size_notional / venue_mid if venue_mid > 0 else float(settings.mm_qty)
    damp = float(settings.mm_inventory_size_damp)
    bid_qty = base_qty * max(0.0, 1.0 - damp * max(0.0, inv)) * (
        1.0 - float(settings.mm_depletion_size_damp) * feat.bid_depletion_score
    )
    ask_qty = base_qty * max(0.0, 1.0 - damp * max(0.0, -inv)) * (
        1.0 - float(settings.mm_depletion_size_damp) * feat.ask_depletion_score
    )

    reduce_bid = position_qty < -1e-12
    reduce_ask = position_qty > 1e-12
    pnl_bps = unrealized_pnl_bps(own.ledger.entry_mid, venue_mid, position_qty)

    reason = (
        f"mm_quote {params.symbol} venue_mid={pricing.venue_mid:.4f} "
        f"res_mid={pricing.reservation_mid:.4f} inv={inv:.3f} "
        f"half_spread={params.half_spread_bps:.2f} "
        f"venue_floor={params.venue_half_floor_bps:.2f} "
        f"micro_bps={pricing.micro_shift_bps:.2f} inv_bps={pricing.inventory_shift_bps:.2f} "
        f"bid_half={pricing.bid_half_bps:.2f} ask_half={pricing.ask_half_bps:.2f} "
        f"pnl_bps={pnl_bps:.2f} fee_rt={fee_round_trip_bps:.1f}"
    )
    return QuoteIntent(
        symbol=feat.symbol,
        bid_price=bid_price,
        ask_price=ask_price,
        bid_qty=max(0.0, bid_qty),
        ask_qty=max(0.0, ask_qty),
        reason=reason,
        strategy_name=strategy_name,
        reduce_only_bid=reduce_bid,
        reduce_only_ask=reduce_ask,
        venue_mid=pricing.venue_mid,
        reservation_mid=pricing.reservation_mid,
        inventory_ratio=inv,
        bid_half_bps=pricing.bid_half_bps,
        ask_half_bps=pricing.ask_half_bps,
        unrealized_pnl_bps=pnl_bps,
    )
