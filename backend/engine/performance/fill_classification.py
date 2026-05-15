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
    if fill.realized_pnl is not None and fill.realized_pnl != 0:
        pnl = fill.realized_pnl
    else:
        pnl = computed_pnl

    return FillClassification(
        action="close",
        entry_price=prev_entry if prev_entry > 0 else None,
        exit_price=fill.price,
        pnl=pnl,
    )


def _same_sign(a: float, b: float) -> bool:
    return (a > 0 and b > 0) or (a < 0 and b < 0)
