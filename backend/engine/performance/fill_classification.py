"""Classify each fill as opening or closing a position for the trades table."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from common.types import Fill, Position


@dataclass(slots=True)
class FillClassification:
    action: Literal["open", "close"]
    entry_price: float | None
    exit_price: float | None
    pnl: float | None


def position_before_fill(
    post_fill: Position | None,
    fill: Fill,
    *,
    fallback_entry: float | None = None,
) -> Position | None:
    """Infer book state immediately before ``fill`` when qty is already post-fill.

    Binance ``ACCOUNT_UPDATE`` often applies the new ``pa`` before
    ``ORDER_TRADE_UPDATE`` arrives. Classifying against the post-fill book
    would label reducing fills as opens. When the venue row was already popped
    (qty flat), ``fallback_entry`` supplies avg entry cached at close time.
    """
    signed = fill.qty * fill.side.sign
    post_qty = post_fill.qty if post_fill else 0.0
    pre_qty = post_qty - signed
    if abs(pre_qty) < 1e-12:
        return None
    entry = (
        post_fill.avg_entry_price
        if post_fill is not None
        else (fallback_entry or 0.0)
    )
    return Position(
        symbol=post_fill.symbol if post_fill is not None else fill.symbol,
        qty=pre_qty,
        avg_entry_price=entry,
        mark_price=post_fill.mark_price if post_fill is not None else fill.price,
        realized_pnl=post_fill.realized_pnl if post_fill is not None else 0.0,
        exchange_unrealized_pnl=(
            post_fill.exchange_unrealized_pnl if post_fill is not None else 0.0
        ),
        updated_at=post_fill.updated_at if post_fill is not None else fill.ts,
    )


def classify_fill(position: Position | None, fill: Fill) -> FillClassification:
    """Mirror PositionTracker._apply_fill open/close logic before the fill is applied."""
    prev_qty = position.qty if position else 0.0
    signed = fill.qty * fill.side.sign
    prev_entry = position.avg_entry_price if position else 0.0

    if prev_qty == 0 or _same_sign(prev_qty, signed):
        return FillClassification(
            action="open",
            entry_price=fill.price,
            exit_price=None,
            pnl=None,
        )

    closing_qty = min(abs(prev_qty), fill.qty)
    pnl_per_unit = (fill.price - prev_entry) * (1 if prev_qty > 0 else -1)
    computed_pnl = pnl_per_unit * closing_qty
    pnl = _pick_close_pnl(venue_rp=fill.realized_pnl, computed_pnl=computed_pnl)

    return FillClassification(
        action="close",
        entry_price=prev_entry if prev_entry > 0 else None,
        exit_price=fill.price,
        pnl=pnl,
    )


def _same_sign(a: float, b: float) -> bool:
    return (a > 0 and b > 0) or (a < 0 and b < 0)


def _pick_close_pnl(*, venue_rp: float | None, computed_pnl: float) -> float:
    """Prefer Binance ``rp`` when it looks authoritative; otherwise use local economics.

    The venue sometimes sends ``rp`` that is exactly zero (use computed — prior
    behaviour), or a *tiny* non-zero figure vs a much larger mark-to-close
    estimate for the same slice — in that case trust ``computed_pnl``.
    """

    if venue_rp is None:
        return computed_pnl
    av = abs(float(venue_rp))
    ac = abs(float(computed_pnl))
    if av < 1e-12:
        return computed_pnl
    # e.g. rp=0.002 (rounds to $0.00 in UI) while entry/exit economics are ~$2
    if av < 0.01 and ac >= 0.05 and ac > 10.0 * av:
        return computed_pnl
    return float(venue_rp)
