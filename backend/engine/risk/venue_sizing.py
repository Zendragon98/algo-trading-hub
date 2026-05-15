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
