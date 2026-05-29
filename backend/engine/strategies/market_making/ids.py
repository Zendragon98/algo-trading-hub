"""Strategy identifiers (no heavy imports — safe for package __init__)."""

from __future__ import annotations

MM_STRATEGY_NAMES = frozenset({"market_making_v2"})


def is_mm_strategy(name: str) -> bool:
    return name in MM_STRATEGY_NAMES
