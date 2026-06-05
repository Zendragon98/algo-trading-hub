from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

class Mm2Mixin(BaseModel):
    # --- Market-making 2.0 (fee-aware fade; skew + imbalance + tape) ---
    # MM2_SYMBOLS: CSV list, or ``AUTO`` (empty = AUTO); shares MM scanner when AUTO.
    mm2_symbols: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "BTCUSDT",
            "ETHUSDT",
            "SOLUSDT",
            "BNBUSDT",
            "XRPUSDT",
        ],
    )
    mm2_skew_window_sec: float = 180.0
    mm2_skew_scale: float = 1.0
    mm2_imbalance_scale: float = 8.0
    mm2_tape_scale: float = 12.0
    mm2_min_tape_trades: int = 5
    mm2_min_skew_bps: float = 1.0
    mm2_tape_confirm: float = 0.08  # require tape + imbalance to align before quoting a side
    # Classic MM: always rest bid+ask when flat; skew/tape tilt prices only (no entry gates).
    mm2_two_sided_always: bool = True
    mm2_taker_fee_bps: float = 4.5
    # Per-leg maker fee (bps); negative = rebate received per fill.
    mm2_maker_fee_bps: float = 2.0
    mm2_fee_round_trip_bps: float = 0.0
    # When True, spread gates do not require spread to cover fees (maker earns rebate).
    mm2_assume_maker_rebate: bool = False
    mm2_spread_buffer_bps: float = 2.0
    mm2_min_spread_bps: float = 0.0
    mm2_min_edge_bps: float = 0.0
    # calibrated = per-symbol min_spread_bps (+ fee floor); standard = max(cal, 2×half);
    # fee_floor = fees+buffer only; off = any positive spread (not for production)
    mm2_spread_gate_mode: str = "standard"
    mm2_min_exit_profit_bps: float = 4.0
    mm2_max_hold_sec: float = 60.0
    mm2_market_exit_loss_bps: float = 12.0
    mm2_aggressive_exit_loss_bps: float = 8.0
    mm2_exit_inside_touch_bps: float = 1.0
    mm2_exit_stale_sec: float = 20.0
    mm2_exit_scratch_bps: float = 0.0
    mm2_exit_aggressive_bps: float = 35.0
    mm2_exit_loss_ramp_bps: float = 20.0
    mm2_exit_cross_touch: bool = True
    mm2_early_loss_hold_frac: float = 0.0
    mm2_min_samples: int = 60
    mm2_quote_during_warmup: bool = True
    mm2_risk_per_trade_pct: float = 0.008
    mm2_max_inventory_notional: float = 300.0
    # Sum of |position| notionals across MM2 symbols; blocks new flat entries when exceeded.
    mm2_max_inventory_notional_total: float = 600.0
    mm2_max_concurrent_positions: int = 6
    # On moderate risk (elevated toxicity/markout/depletion): widen half-spread and damp size.
    mm2_risk_widen_multiplier: float = 2.0
    mm2_risk_size_damp: float = 0.5
    mm2_toxicity_moderate: float = 0.45
    mm2_toxicity_extreme: float = 0.85
    mm2_markout_moderate_frac: float = 0.75
    mm2_max_consecutive_same_side_fills: int = 2
    mm2_side_halt_sec: float = 120.0
    mm2_vol_regime_spike_mult: float = 1.8
    mm2_vol_regime_pause_sec: float = 90.0
    mm2_qty: float = 0.001
    mm2_cooldown_sec: float = 25.0
    mm2_max_entries_per_tick: int = 1
    # When >0, require |composite| >= entry + fee_rt×scale (composite-fee gate).
    # 0 = off (default); use MM2_MIN_SPREAD_BPS for a literal spread floor instead.
    mm2_composite_fee_scale: float = 0.0
    mm2_scan_log_interval_sec: float = 60.0

