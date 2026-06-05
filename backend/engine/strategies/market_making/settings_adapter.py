"""Map mm2_* config fields onto mm_* names for mm_core."""

from __future__ import annotations

from common.config import Settings

_MM2_FIELD_MAP = {
    "mm_min_skew_bps": "mm2_min_skew_bps",
    "mm_tape_confirm": "mm2_tape_confirm",
    "mm_skew_scale": "mm2_skew_scale",
    "mm_imbalance_scale": "mm2_imbalance_scale",
    "mm_tape_scale": "mm2_tape_scale",
    "mm_min_tape_trades": "mm2_min_tape_trades",
    "mm_min_exit_profit_bps": "mm2_min_exit_profit_bps",
    "mm_max_hold_sec": "mm2_max_hold_sec",
    "mm_qty": "mm2_qty",
    "mm_risk_per_trade_pct": "mm2_risk_per_trade_pct",
    "mm_market_exit_loss_bps": "mm2_market_exit_loss_bps",
    "mm_aggressive_exit_loss_bps": "mm2_aggressive_exit_loss_bps",
    "mm_exit_inside_touch_bps": "mm2_exit_inside_touch_bps",
    "mm_exit_stale_sec": "mm2_exit_stale_sec",
    "mm_exit_scratch_bps": "mm2_exit_scratch_bps",
    "mm_max_inventory_notional": "mm2_max_inventory_notional",
    "mm_max_inventory_notional_total": "mm2_max_inventory_notional_total",
    "mm_max_concurrent_positions": "mm2_max_concurrent_positions",
    "mm_risk_widen_multiplier": "mm2_risk_widen_multiplier",
    "mm_risk_size_damp": "mm2_risk_size_damp",
    "mm_max_consecutive_same_side_fills": "mm2_max_consecutive_same_side_fills",
    "mm_side_halt_sec": "mm2_side_halt_sec",
    "mm_vol_regime_spike_mult": "mm2_vol_regime_spike_mult",
    "mm_vol_regime_pause_sec": "mm2_vol_regime_pause_sec",
    "mm_exit_aggressive_bps": "mm2_exit_aggressive_bps",
    "mm_exit_loss_ramp_bps": "mm2_exit_loss_ramp_bps",
    "mm_exit_cross_touch": "mm2_exit_cross_touch",
    "mm_early_loss_hold_frac": "mm2_early_loss_hold_frac",
}


class Mm2SettingsAdapter:
    """Read mm_* names from mm2_* (and institutional mm_*) fields."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def __getattr__(self, name: str) -> object:
        alt = _MM2_FIELD_MAP.get(name)
        if alt is not None:
            return getattr(self._settings, alt)
        return getattr(self._settings, name)


def mm_settings_for(settings: Settings) -> Mm2SettingsAdapter:
    return Mm2SettingsAdapter(settings)
