"""Venue filter helpers shared by pre-trade validation and the engine."""

from __future__ import annotations

from gateways.gateway_interface import SymbolFilters


def venue_min_qty(
    *,
    mid: float,
    filters: SymbolFilters | None,
) -> float | None:
    """Return the venue-minimum tradable qty, or None to veto.

    Uses ``mid`` as a conservative proxy for MIN_NOTIONAL checks.
    """
    if mid <= 0:
        return None
    if filters is None:
        return 0.0

    required = 0.0

    if filters.min_qty is not None and required + 1e-12 < filters.min_qty:
        required = filters.min_qty

    if filters.min_notional is not None:
        min_qty_for_notional = filters.min_notional / mid
        if required + 1e-12 < min_qty_for_notional:
            required = min_qty_for_notional

    if filters.step_size is not None and filters.step_size > 0:
        step = filters.step_size
        n = int((required + step - 1e-15) / step)
        required = n * step

    if required <= 0:
        return None
    return required


def _floor_to_step(qty: float, step: float) -> float:
    if step <= 0:
        return qty
    n = int((qty + 1e-15) / step)
    return n * step


def venue_cap_qty(qty: float, filters: SymbolFilters | None) -> float:
    """Clamp ``qty`` down to the venue per-order maximum, floored to step."""
    if filters is None or filters.max_qty is None:
        return qty
    capped = min(qty, filters.max_qty)
    if filters.step_size is not None and filters.step_size > 0:
        capped = _floor_to_step(capped, filters.step_size)
    return capped


def venue_qty_in_bounds(
    qty: float,
    filters: SymbolFilters | None,
    ref_price: float | None,
    *,
    reduce_only: bool = False,
) -> bool:
    """True when ``qty`` satisfies venue min/max/step/notional rules."""
    if filters is None:
        return True
    if filters.max_qty is not None and qty > filters.max_qty + 1e-12:
        return False
    if filters.step_size is not None and filters.step_size > 0:
        if qty + 1e-12 < filters.step_size:
            return False
    if filters.min_qty is not None and qty + 1e-12 < filters.min_qty:
        return False
    if (
        not reduce_only
        and filters.min_notional is not None
        and ref_price is not None
        and qty * ref_price + 1e-9 < filters.min_notional
    ):
        return False
    return True
