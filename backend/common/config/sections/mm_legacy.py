from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

class MmLegacyMixin(BaseModel):
    # --- Market-making tilt strategy (skew + imbalance + tape) ---
    # MM_SYMBOLS: CSV list, or ``AUTO`` (empty = AUTO) to run mm_universe_scanner at boot.
    mm_symbols: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # Rolling mean of (micro_price - mid)/mid in bps over this many seconds.
    mm_skew_window_sec: float = 300.0
    mm_skew_scale: float = 1.0
    mm_imbalance_scale: float = 8.0
    # Count-based tape pressure uses TRADE_TAPE_WINDOW_SEC (default 300s): scale on
    # (ask_hit_count - bid_hit_count) / total_trades when total >= mm_min_tape_trades.
    mm_tape_scale: float = 12.0
    mm_min_tape_trades: int = 3
    mm_min_samples: int = 5
    mm_risk_per_trade_pct: float = 0.002
    mm_qty: float = 0.001
    mm_cooldown_sec: float = 20.0
    # Cap new MM entries per engine tick (exits are not capped). 0 = unlimited.
    mm_max_entries_per_tick: int = 1
    # MM_SYMBOLS=AUTO: analytics scan for liquid, stable-spread markets (see mm_universe_scanner).
    mm_auto_max_symbols: int = 12
    mm_auto_prefilter_top_volume: int = 60
    mm_auto_sample_rounds: int = 20
    mm_auto_sample_interval_sec: float = 1.0
    mm_auto_min_quote_volume: float = 5_000_000.0
    mm_auto_min_mid_price: float = 0.05
    mm_auto_min_spread_bps: float = 0.8
    mm_auto_max_spread_bps: float = 20.0
    # Stability caps: 0 = derive from scan percentiles + 24h range vol (see mm_universe_scanner).
    mm_auto_max_spread_cv: float = 0.0
    mm_auto_max_mid_vol_bps: float = 0.0
    mm_auto_stability_percentile: float = 75.0
    mm_auto_spread_cv_floor: float = 0.12
    mm_auto_spread_cv_cap: float = 0.70
    mm_auto_mid_vol_floor_bps: float = 2.0
    mm_auto_mid_vol_cap_bps: float = 35.0
    mm_auto_vol_regime_mult: float = 1.25
    mm_auto_min_edge_bps: float = 0.0  # 0 = 2× maker fee + spread buffer
    mm_auto_scan_ttl_sec: float = 3600.0
    # Tiered AUTO universe: always include maincaps, fill remainder from midcap scan.
    mm_auto_pin_symbols: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "BTCUSDT",
            "ETHUSDT",
            "BNBUSDT",
            "SOLUSDT",
            "XRPUSDT",
        ],
    )
    mm_auto_pin_min_quote_volume: float = 30_000_000.0
    mm_auto_midcap_min_quote_volume: float = 8_000_000.0
    mm_auto_pin_min_edge_bps: float = 2.0
    mm_auto_pin_min_spread_bps: float = 0.4
    # Set at boot when MM_SYMBOLS/MM2_SYMBOLS were AUTO; enables live universe refresh.
    mm_universe_auto: bool = False
    mm2_universe_auto: bool = False
    mm_universe_refresh_sec: float = 3600.0
    mm_universe_adverse_refresh_cooldown_sec: float = 600.0
    mm_universe_adverse_check_sec: float = 30.0
    mm_universe_adverse_markout_bps: float = 0.0
    mm_universe_adverse_min_symbols: int = 2
    mm_universe_adverse_spread_widen_mult: float = 1.75
    mm_universe_adverse_regime_vol_bps: float = 25.0
    mm_universe_regime_symbols: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["BTCUSDT", "ETHUSDT"],
    )

