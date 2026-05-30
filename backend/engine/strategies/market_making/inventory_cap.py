"""Shared MM inventory notional cap resolution."""

from __future__ import annotations

from common.config import Settings


def resolve_mm_inventory_notional(settings: Settings, equity: float) -> float:
    """Per-symbol inventory cap: mm2 override, then mm, then equity pct."""
    cap = float(getattr(settings, "mm2_max_inventory_notional", 0.0) or 0.0)
    if cap <= 0:
        cap = float(settings.mm_max_inventory_notional)
    if cap <= 0 and equity > 0:
        cap = equity * float(settings.max_symbol_notional_pct)
    return max(0.0, cap)
