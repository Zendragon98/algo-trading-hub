from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

class BlendMixin(BaseModel):
    # --- Blended multi-indicator strategy (ADX-gated, closed-bar only) ---
    blend_symbols: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["BTCUSDT", "ETHUSDT"]
    )
    blend_max_symbols: int = 10
    blend_symbol: str = "BTCUSDT"
    blend_bar_interval_sec: float = 900.0
    blend_adx_period: int = 14
    blend_adx_trend_threshold: float = 20.0
    blend_adx_strong_threshold: float = 30.0
    blend_ema_fast: int = 12
    blend_ema_slow: int = 26
    blend_ema_min_gap_bps: float = 5.0
    blend_macd_fast: int = 12
    blend_macd_slow: int = 26
    blend_macd_signal: int = 9
    blend_rsi_period: int = 14
    blend_rsi_oversold: float = 30.0
    blend_rsi_overbought: float = 70.0
    blend_rsi_extreme_oversold: float = 25.0
    blend_rsi_extreme_overbought: float = 75.0
    blend_bb_period: int = 20
    blend_bb_std: float = 2.0
    blend_bb_lower_threshold: float = 0.05
    blend_bb_upper_threshold: float = 0.95
    blend_micro_threshold: float = 0.15
    blend_micro_window_sec: float = 120.0
    blend_entry_threshold: float = 0.50
    blend_exit_threshold: float = 0.15
    blend_min_confirming_votes: int = 2
    blend_regime_flip_exit: bool = True
    blend_regime_flip_adx_buffer: float = 2.0
    blend_min_mid_price: float = 0.01
    blend_risk_per_trade_pct: float = 0.12
    blend_qty: float = 0.001
    blend_cooldown_sec: float = 60.0
    blend_max_entries_per_tick: int = 2
    blend_scan_log_interval_sec: float = 60.0

