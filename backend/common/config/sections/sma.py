from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

class SmaMixin(BaseModel):
    # --- SMA crossover strategy (multi-symbol scanner) ---
    # SMA_SYMBOLS supports a CSV list ("BTCUSDT,ETHUSDT") or the literal
    # "AUTO" to discover every USDT perpetual on the venue at boot.
    # SMA_SYMBOL is kept as a backwards-compat shim — when sma_symbols is
    # empty, main.py falls back to a single-symbol list of [sma_symbol].
    sma_symbols: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["BTCUSDT", "ETHUSDT"],
    )
    sma_symbol: str = "BTCUSDT"
    sma_fast_window: int = 10
    sma_slow_window: int = 30
    # Closed-bar sampling (seconds). 900 = 15m bars; windows count bars, not ticks.
    sma_bar_interval_sec: float = Field(default=900.0)
    # Skip symbols below this mid (stops cannot resolve on sub-tick alts).
    sma_min_mid_price: float = 0.01
    # Portfolio risk budget per round-trip (split evenly across ``sma_symbols``).
    # Falls back to ``sma_qty`` when equity is unavailable (e.g. boot
    # before the first ``fetch_balance`` lands).
    sma_risk_per_trade_pct: float = 0.12
    sma_qty: float = 0.001
    sma_cooldown_sec: int = 45
    sma_max_entries_per_tick: int = 2
    # INFO heartbeat while the SMA scanner is active (0 = off).
    sma_scan_log_interval_sec: float = 60.0
    # Cap SMA_SYMBOLS=AUTO to the top-N USDT perps by 24h quote volume (0 = full universe).
    sma_max_symbols: int = 10

