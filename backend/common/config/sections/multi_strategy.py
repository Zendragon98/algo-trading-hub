from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field
from pydantic_settings import NoDecode


class MultiStrategyMixin(BaseModel):
    # --- STRATEGY=all partition (non-overlapping single-leg universes) ---
    multi_strategy_partition: bool = True
    # Bases for pairs when strategy=all (USDT + USDC legs each).
    multi_strategy_pair_bases: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["BTC", "ETH", "SOL", "BNB", "XRP"],
    )
    flow_max_symbols: int = 10
