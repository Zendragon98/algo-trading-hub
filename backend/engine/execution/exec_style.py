"""Resolve execution style (passive VWAP vs cross-touch) per strategy."""

from __future__ import annotations

from common.config import Settings
from common.enums import Urgency

MM_STRATEGY = "market_making_v2"
FLOW_STRATEGY = "flow_momentum"


def resolve_cross_touch(
    settings: Settings,
    *,
    strategy_name: str,
    urgency: Urgency,
    reduce_only: bool,
    notes: str,
) -> bool:
    """True when the parent should peg at the aggressive touch (hit bid/ask)."""
    if reduce_only:
        if notes == "flow_exit_market":
            return bool(getattr(settings, "flow_exit_cross_touch", True))
        if urgency is Urgency.AGGRESSIVE:
            return bool(getattr(settings, "urgent_exit_cross_touch", True))
        return False
    if strategy_name == FLOW_STRATEGY and urgency is Urgency.AGGRESSIVE:
        return bool(getattr(settings, "flow_entry_cross_touch", True))
    if urgency is Urgency.AGGRESSIVE:
        return bool(getattr(settings, "urgent_cross_touch", False))
    return False
