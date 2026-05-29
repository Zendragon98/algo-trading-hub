from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

class MmInstitutionalMixin(BaseModel):
    # --- Institutional MM (quote-only execution, microstructure) ---
    mm_institutional_risk_enabled: bool = True
    mm_quote_enabled: bool = True
    mm_urgent_exit_market: bool = True
    mm_tape_pressure_mode: str = "volume"  # volume | count
    mm_max_inventory_notional: float = 0.0  # 0 = use max_symbol_notional_pct * equity
    mm_inventory_skew_scale: float = 4.0
    # Shift MM reservation mid away from inventory at |ratio|=1 (bps; long -> lower mid).
    mm_reservation_inventory_bps: float = 12.0
    # Extra half-spread on the side that would add exposure at |ratio|=1 (bps).
    mm_inventory_spread_skew_bps: float = 5.0
    # Weight on microstructure bias when building reservation mid (0–1 scale on bias units).
    mm_reservation_micro_weight: float = 0.12
    mm_inventory_hard_ratio: float = 0.85
    mm_inventory_exit_ratio: float = 0.7
    mm_inventory_size_damp: float = 0.5
    mm_inventory_include_working: bool = False
    mm_jump_return_bps: float = 25.0
    mm_jump_vol_mult: float = 3.0
    mm_jump_vol_ewma_alpha: float = 0.08
    mm_jump_pause_sec: float = 30.0
    mm_jump_flatten: bool = False
    mm_max_adverse_markout_bps: float = 8.0
    mm_markout_cooldown_sec: float = 15.0
    mm_markout_ewma_alpha: float = 0.15
    mm_markout_horizons_sec: Annotated[list[float], NoDecode] = Field(
        default_factory=lambda: [1.0, 5.0, 30.0],
    )
    mm_scratch_loss_bps: float = 15.0
    mm_exit_scratch_bps: float = 5.0
    # Exit when unrealized <= -this (before max-hold); limit peg ramps toward touch.
    mm_loss_exit_bps: float = 10.0
    # Max scratch distance when deeply underwater (pegs toward best bid/ask).
    mm_exit_aggressive_bps: float = 35.0
    # Loss bps span over which exit limit ramps from scratch to aggressive.
    mm_exit_loss_ramp_bps: float = 20.0
    # At full ramp, peg sell at best_ask / buy at best_bid (aggressive limit, taker).
    mm_exit_cross_touch: bool = True
    # After this fraction of max_hold, flatten if still losing (0 = off).
    mm_early_loss_hold_frac: float = 0.0
    mm_market_exit_loss_bps: float = 10.0
    mm_aggressive_exit_loss_bps: float = 5.0
    mm_exit_inside_touch_bps: float = 1.0
    mm_exit_stale_sec: float = 20.0
    mm_max_concurrent_positions: int = 3
    mm_max_consecutive_same_side_fills: int = 2
    mm_side_halt_sec: float = 120.0
    mm_vol_regime_spike_mult: float = 2.0
    mm_vol_regime_pause_sec: float = 600.0
    mm_min_exit_profit_bps: float = 5.0
    mm_max_hold_sec: float = 45.0
    mm_catastrophe_stop_pct: float = 0.0
    mm_depletion_top_n: int = 10
    mm_depletion_baseline_alpha: float = 0.06
    mm_depletion_drop_pct: float = 0.25
    mm_depletion_window_sec: float = 5.0
    mm_depletion_widen_bps: float = 4.0
    mm_depletion_shift_bps: float = 3.0
    mm_depletion_size_damp: float = 0.4
    mm_depletion_pull_ratio: float = 0.35
    mm_depletion_breaker_ratio: float = 0.25
    mm_depletion_scale: float = 6.0
    mm_large_trade_mult: float = 3.0
    mm_toxicity_threshold: float = 0.65
    mm_toxicity_vpin_weight: float = 0.2
    mm_toxicity_large_weight: float = 0.15
    mm_toxicity_depletion_weight: float = 0.2
    mm_toxicity_markout_weight: float = 0.2
    mm_toxicity_jump_weight: float = 0.15
    mm_toxicity_tape_vel_weight: float = 0.05
    mm_toxicity_informed_weight: float = 0.25
    mm_toxicity_markout_norm_bps: float = 20.0
    mm_toxicity_tape_vel_norm: float = 50.0
    mm_toxicity_vpin_informed_high: float = 0.55
    mm_toxicity_vpin_informed_low: float = 0.45
    mm_toxicity_depletion_informed_min: float = 0.5
    mm_quote_half_spread_bps: float = 3.0
    # Per-symbol half-spread (bps). Env: JSON or ``BTCUSDT:2,ETHUSDT:3,DOGEUSDT:12``.
    mm_symbol_half_spread_bps: Annotated[dict[str, float], NoDecode] = Field(
        default_factory=dict,
    )
    # Per-symbol full overrides, e.g. ``{"DOGEUSDT":{"half_spread_bps":15,"min_spread_bps":8}}``.
    mm_symbol_quote_overrides: Annotated[dict[str, dict[str, float]], NoDecode] = Field(
        default_factory=dict,
    )
    # When true, half-spread is at least ``mm_quote_venue_spread_mult * venue_spread_bps / 2``.
    mm_quote_use_venue_spread_floor: bool = True
    mm_quote_venue_spread_mult: float = 1.0
    # L2 calibration artefact from analytics.spread_calibrator (after l2_loader ingest).
    mm_spread_calibration_path: str = "mm_spread_calibration.json"
    mm_spread_calib_percentile: float = 50.0
    mm_spread_calib_half_mult: float = 0.55
    mm_spread_calib_buffer_bps: float = 0.5
    mm_spread_calib_min_half_bps: float = 1.0
    mm_spread_calib_max_half_bps: float = 50.0
    mm_spread_calib_min_samples: int = 30
    mm_quote_refresh_bps: float = 1.0
    mm_quote_min_rest_sec: float = 0.5
    mm_quote_size_pct: float = 0.002
    mm_quote_max_refresh_per_tick: int = 8
    mm_quote_toxic_widen_bps: float = 6.0
    # Execution zones (slide: place/cancel ranges in bps; 0 = disabled).
    mm_place_range_bps: float = 0.0
    mm_cancel_range_bps: float = 0.0
    mm_sweep_edge_bps: float = 0.0
    # Execution mode: make | chase | climb | ladder | climb_multi | take
    mm_execution_mode: str = "make"
    mm_execution_mode_bid: str = ""
    mm_execution_mode_ask: str = ""
    mm_ladder_levels: int = 3
    mm_ladder_spacing_ticks: int = 1
    mm_ladder_qty_weights: str = "equal"
    mm_climb_ticks_per_refresh: int = 1
    mm_climb_max_ticks_from_touch: int = 0
    mm_max_working_orders_per_symbol: int = 8
    # Protect guardrails (trade bursts); 0 thresh = off.
    mm_protect_enabled: bool = False
    mm_protect_burst_thresh: float = 0.55
    mm_protect_widen_bps: float = 8.0
    mm_protect_decay_sec: float = 3.0
    # Per-side position limits (notional); 0 = use inventory hard ratio only.
    mm_buy_position_limit_notional: float = 0.0
    mm_sell_position_limit_notional: float = 0.0
    mm_reject_halt_sec: float = 30.0
    # Funding / carry (perp); requires mm_funding_enabled.
    mm_funding_enabled: bool = False
    mm_funding_shift_scale: float = 1.0
    mm_funding_poll_sec: float = 60.0
    # Stablecoin basis tilt; needs USDT/USD index when enabled.
    mm_stablecoin_basis_enabled: bool = False
    usdt_usd_index: float = 1.0
    usdc_usd_index: float = 1.0
    # Manual reservation mid offset (bps).
    mm_manual_adj_bps: float = 0.0
    # Readiness gate before quoting.
    mm_require_ready: bool = False
    # Post-fill IOC hedge when adverse maker fill exceeds threshold.
    mm_fill_hedge_ioc_enabled: bool = False
    mm_fill_hedge_vol_bps: float = 50.0
    mm_fill_hedge_adverse_bps: float = 5.0

