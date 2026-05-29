from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

class FlowMixin(BaseModel):
    # --- Flow momentum (follow sustained one-sided tape across multiple symbols) ---
    # FLOW_SYMBOLS: CSV list, or ``AUTO`` (empty = AUTO) — full MM scan universe.
    flow_symbols: Annotated[list[str], NoDecode] = Field(default_factory=list)
    flow_universe_auto: bool = False
    flow_tape_mode: str = "volume"  # volume | count
    flow_min_tape_trades: int = 5
    flow_tape_threshold: float = 0.12
    flow_exit_tape_threshold: float = 0.06
    flow_imbalance_min: float = 0.05
    flow_confirm_ticks: int = 3
    flow_require_depletion: bool = False
    flow_min_tape_velocity: float = 0.0
    flow_skip_toxic: bool = True
    flow_take_profit_bps: float = 15.0
    flow_stop_loss_bps: float = 10.0
    flow_max_hold_sec: float = 90.0
    flow_cooldown_sec: float = 30.0
    flow_risk_per_trade_pct: float = 0.08
    flow_qty: float = 0.001
    flow_min_mid_price: float = 0.01
    flow_entry_score: float = 0.85
    flow_max_entries_per_tick: int = 2
    flow_scan_log_interval_sec: float = 60.0

