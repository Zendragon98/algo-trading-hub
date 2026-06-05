"""Canonical venue-aligned price and PnL resolution.

Source-of-truth hierarchy for **exit decisions**:
  1. Binance ``up`` (``exchange_unrealized_pnl``) — authoritative when the
     strategy's signed qty matches the net venue leg (within tolerance).
  2. **Fill VWAP** — strategy-attributed fill prices (``on_fill(price=…)``).
  3. **Venue avg entry** (``ep`` / ``entryPrice``) + venue mark — price-based
     bps when ``up`` is unavailable but the leg is fully owned.
  4. **Unknown** — never use book mid as entry; exit thresholds that require
     PnL must not fire on fabricated zero-bps reads.

``signal_mid`` (book mid at signal emit) is **not** an exit input — it is
observability-only until fills or ACCOUNT_UPDATE confirm economics.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Literal

from common.types import Position

from ..strategies.position_sync import VenuePosition, side_from_qty

logger = logging.getLogger(__name__)

EntrySource = Literal["venue_upnl", "fill_vwap", "venue_avg", "unknown"]

_QTY_EPS = 1e-12
_QTY_ALIGN_FRAC = 0.02


@dataclass(frozen=True, slots=True)
class VenuePnlSnapshot:
    """One PnL read for strategy exits and verification."""

    entry_price: float
    entry_source: EntrySource
    mark: float
    internal_bps: float
    venue_upnl_usd: float | None
    venue_bps: float | None
    drift_bps: float | None
    qty_aligned: bool
    verified: bool
    exit_bps: float
    executable_bps: float


def executable_price(
    *,
    pos_side: int,
    best_bid: float | None,
    best_ask: float | None,
    mark: float,
) -> float:
    """Price at which a reduce-only order would execute (bid for long, ask for short)."""
    if pos_side > 0 and best_bid is not None and best_bid > 0:
        return best_bid
    if pos_side < 0 and best_ask is not None and best_ask > 0:
        return best_ask
    return mark


def venue_position_from(position: Position | None) -> VenuePosition | None:
    if position is None or abs(position.qty) < _QTY_EPS:
        return None
    return VenuePosition(
        qty=position.qty,
        avg_entry_price=position.avg_entry_price,
        mark_price=position.mark_price,
        exchange_unrealized_pnl=position.exchange_unrealized_pnl,
    )


def price_pnl_bps(entry: float, mark: float, side: int) -> float:
    if entry <= 0 or mark <= 0 or side == 0:
        return 0.0
    if side > 0:
        return (mark - entry) / entry * 10_000.0
    return (entry - mark) / entry * 10_000.0


def qty_aligned_with_venue(*, pos_qty: float, venue_qty: float) -> bool:
    if abs(pos_qty) < _QTY_EPS or abs(venue_qty) < _QTY_EPS:
        return False
    if side_from_qty(pos_qty) != side_from_qty(venue_qty):
        return False
    diff = abs(abs(pos_qty) - abs(venue_qty))
    return diff <= _QTY_EPS or diff / abs(venue_qty) <= _QTY_ALIGN_FRAC


def resolve_entry_price(
    *,
    venue: VenuePosition | None,
    pos_side: int,
    pos_qty: float,
    fill_vwap: float,
) -> tuple[float, EntrySource]:
    """Return entry price for internal bps — never uses signal mid."""
    if fill_vwap > 0:
        return fill_vwap, "fill_vwap"
    if venue is not None and venue.avg_entry_price > 0:
        if qty_aligned_with_venue(pos_qty=pos_qty, venue_qty=venue.qty):
            if side_from_qty(venue.qty) == pos_side:
                return venue.avg_entry_price, "venue_avg"
    return 0.0, "unknown"


def venue_bps_from_exchange_upnl(
    *,
    venue: VenuePosition,
    pos_side: int,
    pos_qty: float,
) -> float | None:
    if venue.exchange_unrealized_pnl is None or abs(pos_qty) < _QTY_EPS:
        return None
    if not qty_aligned_with_venue(pos_qty=pos_qty, venue_qty=venue.qty):
        return None
    if side_from_qty(venue.qty) != pos_side:
        return None
    share = min(1.0, abs(pos_qty) / abs(venue.qty))
    scaled_upnl = venue.exchange_unrealized_pnl * share
    basis = abs(venue.avg_entry_price * pos_qty)
    if basis <= _QTY_EPS:
        return None
    return scaled_upnl / basis * 10_000.0


def compute_venue_pnl(
    *,
    pos_side: int,
    pos_qty: float,
    mid: float,
    fill_vwap: float,
    venue: VenuePosition | None,
    best_bid: float | None = None,
    best_ask: float | None = None,
) -> VenuePnlSnapshot:
    entry_price, entry_source = resolve_entry_price(
        venue=venue,
        pos_side=pos_side,
        pos_qty=pos_qty,
        fill_vwap=fill_vwap,
    )
    mark = mid
    if venue is not None and venue.mark_price > 0:
        mark = venue.mark_price

    internal_bps = price_pnl_bps(entry_price, mark, pos_side) if entry_price > 0 else 0.0
    exec_price = executable_price(
        pos_side=pos_side,
        best_bid=best_bid,
        best_ask=best_ask,
        mark=mark,
    )
    executable_bps = (
        price_pnl_bps(entry_price, exec_price, pos_side) if entry_price > 0 else 0.0
    )

    aligned = (
        venue is not None
        and qty_aligned_with_venue(pos_qty=pos_qty, venue_qty=venue.qty)
        and side_from_qty(venue.qty) == pos_side
    )
    venue_upnl = venue.exchange_unrealized_pnl if aligned and venue is not None else None
    venue_bps: float | None = None
    if aligned and venue is not None and venue.avg_entry_price > 0:
        venue_bps = venue_bps_from_exchange_upnl(
            venue=venue, pos_side=pos_side, pos_qty=pos_qty
        )

    drift: float | None = None
    if venue_bps is not None and entry_price > 0:
        drift = internal_bps - venue_bps

    verified = venue_bps is not None
    if verified:
        exit_bps = venue_bps
        effective_source: EntrySource = "venue_upnl"
    elif entry_price > 0:
        exit_bps = executable_bps
        effective_source = entry_source
    else:
        exit_bps = 0.0
        effective_source = "unknown"

    return VenuePnlSnapshot(
        entry_price=entry_price,
        entry_source=effective_source,
        mark=mark,
        internal_bps=internal_bps,
        venue_upnl_usd=venue_upnl,
        venue_bps=venue_bps,
        drift_bps=drift,
        qty_aligned=aligned,
        verified=verified,
        exit_bps=exit_bps,
        executable_bps=executable_bps,
    )


def apply_attributed_fill_vwap(
    *,
    fill_vwap: float,
    fill_qty_abs: float,
    fill_price: float,
    fill_qty: float,
) -> tuple[float, float]:
    if fill_price <= 0 or fill_qty <= 0:
        return fill_vwap, fill_qty_abs
    if fill_qty_abs <= _QTY_EPS:
        return fill_price, fill_qty
    total = fill_qty_abs + fill_qty
    vwap = (fill_vwap * fill_qty_abs + fill_price * fill_qty) / total
    return vwap, total


def inventory_pnl_bps(
    *,
    fill_entry: float,
    book_mid: float,
    position_qty: float,
    venue: VenuePosition | None,
) -> tuple[float, VenuePnlSnapshot | None]:
    """MM inventory PnL bps — prefers venue ``up`` when qty aligns."""
    side = side_from_qty(position_qty)
    if side == 0:
        return 0.0, None
    snap = compute_venue_pnl(
        pos_side=side,
        pos_qty=position_qty,
        mid=book_mid,
        fill_vwap=fill_entry if fill_entry > 0 else 0.0,
        venue=venue,
    )
    if snap.entry_source == "unknown":
        return 0.0, snap
    return snap.exit_bps, snap


def maybe_log_pnl_verification(
    *,
    tag: str,
    symbol: str,
    snap: VenuePnlSnapshot,
    pos_qty: float,
    pos_side: int,
    now: float,
    last_log_ts: float,
    log_interval_sec: float,
    max_drift_bps: float,
) -> float:
    if pos_side == 0 or abs(pos_qty) < _QTY_EPS:
        return last_log_ts
    if log_interval_sec <= 0:
        return last_log_ts
    if last_log_ts > 0 and now - last_log_ts < log_interval_sec:
        return last_log_ts

    side_label = "LONG" if pos_side > 0 else "SHORT"
    venue_part = ""
    if snap.venue_bps is not None and snap.venue_upnl_usd is not None:
        venue_part = (
            f" venue_bps={snap.venue_bps:.2f} venue_upnl=${snap.venue_upnl_usd:.4f}"
        )
    drift_part = f" drift={snap.drift_bps:+.2f}bps" if snap.drift_bps is not None else ""
    align_part = "aligned" if snap.qty_aligned else "qty_mismatch"
    if snap.verified and (snap.drift_bps is None or abs(snap.drift_bps) <= max_drift_bps):
        verified = "ok"
    elif not snap.qty_aligned and snap.entry_source == "fill_vwap":
        verified = "attributed_only"
    elif snap.entry_source == "unknown":
        verified = "NO_ENTRY"
    else:
        verified = "MISMATCH"

    msg = (
        f"{tag} pnl verify {symbol} {side_label} qty={abs(pos_qty):.4f} "
        f"entry={snap.entry_price:.6f}({snap.entry_source}) mark={snap.mark:.6f} "
        f"internal_bps={snap.internal_bps:.2f} exit_bps={snap.exit_bps:.2f} "
        f"{align_part}{venue_part}{drift_part} verified={verified}"
    )

    if verified in ("MISMATCH", "NO_ENTRY"):
        logger.warning(msg)
    elif snap.drift_bps is not None and abs(snap.drift_bps) > max_drift_bps:
        logger.warning(msg)
    else:
        logger.debug(msg)
    return now
