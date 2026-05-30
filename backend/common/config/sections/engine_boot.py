from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

class EngineBootMixin(BaseModel):
    # --- Engine ---
    # Annotated[..., NoDecode] tells pydantic-settings to skip JSON parsing
    # so the env loader hands us the raw string for `_split_csv` to split.
    symbols: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["BTCUSDT", "BTCUSDC"]
    )
    base_currency: str = "USDT"
    engine_autostart: bool = False
    # Which strategy set to run: "pairs" | "sma" | "blend" | "flow" | "mm2" | "all".
    # STRATEGY=all: periodic LIVE LOG summary of every loaded strategy (seconds; 0 = off).
    multi_strategy_log_interval_sec: float = 60.0
    # Minimum seconds between runtime strategy hot-swaps (control API / settings patch).
    # 0 = no limit (dev). Use 5+ on deployed VMs to block soak-script / UI spam.
    strategy_swap_min_interval_sec: float = 0.0
    # "all" runs every registered strategy with internal position netting.
    strategy: str = "pairs"

