from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field
from pydantic_settings import NoDecode

class FlowMixin(BaseModel):
    # --- Flow momentum (follow sustained one-sided tape across multiple symbols) ---
    # FLOW_SYMBOLS: CSV list, or ``AUTO`` (empty = AUTO) — full MM scan universe.
    flow_symbols: Annotated[list[str], NoDecode] = Field(default_factory=list)
    flow_universe_auto: bool = False
    flow_tape_mode: str = "volume"  # volume | count
    flow_min_tape_trades: int = 5
    flow_tape_threshold: float = 0.20
    flow_exit_tape_threshold: float = 0.06
    # Exit long when tape falls below entry_thr * frac (momentum faded, not reversed).
    flow_exit_tape_frac: float = 0.45
    flow_exit_confirm_ticks: int = 1
    flow_imbalance_min: float = 0.05
    # Consecutive 1 Hz ticks with aligned tape+imbalance before entry (reduces late chase).
    flow_confirm_ticks: int = 5
    flow_require_depletion: bool = True
    # Require |tape| non-decreasing over the confirm window (skip exhausted moves).
    flow_require_rising_tape: bool = True
    # Min trades/sec in the rolling tape window; skips stale 5m tape with no active prints.
    flow_min_tape_velocity: float = 1.0
    flow_skip_toxic: bool = False
    # Soft toxicity / informed-flow confirm (size + score boost; selective skips).
    flow_micro_boost_enabled: bool = True
    flow_jump_skip_entry: bool = True
    flow_toxic_align_min: float = 0.12
    flow_toxic_misalign_skip: bool = True
    flow_toxic_misalign_min_score: float = 0.55
    flow_toxic_exhaust_score: float = 0.92
    flow_toxic_size_boost_max: float = 1.30
    flow_toxic_score_boost_max: float = 0.10
    flow_large_trade_boost_min: float = 0.15
    flow_exit_toxic_flip: bool = True
    flow_exit_toxic_flip_min: float = 0.20
    flow_exit_toxic_flip_score_min: float = 0.40
    # Book depth ratio vs EWMA baseline (same signal as MM book_depleted, flow-native use).
    flow_depth_ratio_enabled: bool = True
    # Long: ask_depth_ratio below this means offers are being lifted; short: bid side.
    flow_depth_depleted_max: float = 0.35
    flow_depth_exhaust_skip: bool = True
    # Aggressor side refilled — skip late chase / exit momentum fade.
    flow_depth_exhaust_min: float = 0.85
    flow_depth_size_boost: float = 1.10
    flow_depth_score_boost: float = 0.04
    flow_exit_depth_replenish: bool = True
    # Skip entry when spread consumes too much of the stop budget (0 = use frac × stop).
    flow_max_spread_entry_bps: float = 0.0
    flow_max_spread_entry_frac: float = 0.4
    flow_taker_fee_bps: float = 4.5
    flow_min_edge_bps: float = 6.0
    flow_take_profit_bps: float = 12.0
    # Trailing profit capture: arm once peak >= max(take_profit, trail_arm); exit
    # when pnl pulls back trail_stop bps from peak. Set trail_stop=0 for fixed TP only.
    flow_trail_stop_bps: float = 6.0
    flow_trail_arm_bps: float = 8.0
    flow_stop_loss_bps: float = 14.0
    flow_max_hold_sec: float = 120.0
    # When True, time-stop only fires if underwater or tape momentum has faded.
    flow_max_hold_loss_only: bool = True
    flow_pnl_verify_max_drift_bps: float = 3.0
    flow_pnl_verify_log_interval_sec: float = 30.0
    flow_cooldown_sec: float = 45.0
    flow_risk_per_trade_pct: float = 0.08
    flow_qty: float = 0.001
    flow_min_mid_price: float = 0.01
    flow_entry_score: float = 0.85
    flow_max_entries_per_tick: int = 3
    flow_size_tape_scale: bool = True
    flow_scan_log_interval_sec: float = 60.0
    # Entry: AGGRESSIVE flow parents peg at ask (buy) / bid (sell) when True.
    flow_entry_cross_touch: bool = True
    # Exit execution: market-first schedule (like flatten) when True.
    flow_exit_market: bool = True
    flow_exit_cross_touch: bool = True
    flow_exit_urgent_score: float = 1.0
