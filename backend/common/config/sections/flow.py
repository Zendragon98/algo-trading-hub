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
    flow_skip_toxic: bool = True
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
    # Exit execution: market-first schedule (like flatten) when True.
    flow_exit_market: bool = True
    flow_exit_cross_touch: bool = True
    flow_exit_urgent_score: float = 1.0
