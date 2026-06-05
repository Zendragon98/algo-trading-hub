from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

class ExecutionCoreMixin(BaseModel):
    # --- Futures leverage ---
    # Applied per symbol via the venue's `set_leverage` hook lazily before
    # the first entry order for that symbol (not at engine start). Leverage
    # doesn't change the dollar-loss-at-stop (that's
    # bounded by `risk_per_trade_pct`); it only relaxes the margin
    # requirement so the stop-loss-sized notional fits in the wallet.
    leverage: int = 10
    # Binance: caps per symbol come from GET /fapi/v1/leverageBracket.
    # Cached under backend/data/cache/ so later starts skip the REST call.
    # `0` = no time-based refresh (only refetch if file missing or
    # BINANCE_REST_BASE changes). Set >0 (seconds) to periodically refresh.
    leverage_bracket_cache_path: str = "data/cache/binance_leverage_brackets.json"
    leverage_bracket_cache_ttl_sec: int = 0

    # --- Execution ---
    vwap_duration_sec: int = 60
    vwap_num_slices: int = 6
    # AGGRESSIVE alpha entries cross the touch when True (per-strategy overrides below).
    urgent_cross_touch: bool = False
    urgent_exit_cross_touch: bool = True
    imbalance_top_n: int = 10
    trade_tape_window_sec: int = 300

