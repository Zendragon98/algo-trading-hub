"""Market making (MM 2.0): lightweight exports only (avoids import cycles).

Import ``MarketMakingV2Strategy`` from ``engine.strategies.market_making.strategy``
or use lazy attribute access below.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .ids import MM_STRATEGY_NAMES, is_mm_strategy
from .universe import auto_universe, engine_symbol_universe, resolve_mm2_symbols

if TYPE_CHECKING:
    from .strategy import MarketMakingV2Strategy

__all__ = [
    "MM_STRATEGY_NAMES",
    "MarketMakingV2Strategy",
    "auto_universe",
    "engine_symbol_universe",
    "is_mm_strategy",
    "resolve_mm2_symbols",
]


def __getattr__(name: str) -> object:
    if name == "MarketMakingV2Strategy":
        from .strategy import MarketMakingV2Strategy

        return MarketMakingV2Strategy
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
