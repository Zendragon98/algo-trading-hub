"""Resolve per-symbol MM knobs from Settings + calibration file."""

from __future__ import annotations

from common.config import Settings

from ..market_data.symbol_calibration import SymbolCalibration, load_symbol_calibration, pick


def calibration_path(settings: Settings) -> str:
    path = (getattr(settings, "symbol_calibration_path", "") or "").strip()
    if path:
        return path
    return (getattr(settings, "mm_spread_calibration_path", "") or "").strip()


def get_symbol_calibration(symbol: str, settings: Settings) -> SymbolCalibration | None:
    path = calibration_path(settings)
    if not path:
        return None
    return load_symbol_calibration(path).get(symbol.upper())


def mm_float(
    symbol: str,
    settings: Settings,
    attr: str,
    *,
    cal_attr: str | None = None,
) -> float:
    default = float(getattr(settings, attr))
    cal = get_symbol_calibration(symbol, settings)
    if cal is None:
        return default
    val = getattr(cal, cal_attr or attr, None)
    return pick(cal, val, default)


def mm2_fee_round_trip_bps(symbol: str, settings: Settings) -> float:
    """Per-symbol fee RT from calibration fees section, else Settings.

    Positive = cost to trade; negative = maker rebate (income) per round trip.
    ``mm2_fee_round_trip_bps`` when non-zero overrides calibration and per-leg fees.
    """
    explicit = float(settings.mm2_fee_round_trip_bps or 0.0)
    if explicit != 0.0:
        return explicit
    cal = get_symbol_calibration(symbol, settings)
    if cal is not None and cal.maker_fee_bps is not None and cal.taker_fee_bps is not None:
        per_leg = (
            float(cal.maker_fee_bps)
            if settings.post_only_enabled
            else float(cal.taker_fee_bps)
        )
        return 2.0 * per_leg
    per_leg = (
        float(settings.mm2_maker_fee_bps)
        if settings.post_only_enabled
        else float(settings.mm2_taker_fee_bps)
    )
    return 2.0 * per_leg


def mm2_spread_gate_fee_rt_bps(symbol: str, settings: Settings) -> float:
    """Fee drag used only for spread / edge gates (0 when assuming maker rebate)."""
    if bool(getattr(settings, "mm2_assume_maker_rebate", False)):
        return 0.0
    return mm2_fee_round_trip_bps(symbol, settings)


def mm2_spread_buffer_bps(symbol: str, settings: Settings) -> float:
    return mm_float(
        symbol,
        settings,
        "mm2_spread_buffer_bps",
        cal_attr="spread_buffer_bps",
    )


def mm2_fee_edge_floor_bps(symbol: str, settings: Settings) -> float:
    """Minimum spread (bps) from fee economics + buffer for spread gates."""
    fee_rt = mm2_spread_gate_fee_rt_bps(symbol, settings)
    buf = mm2_spread_buffer_bps(symbol, settings)
    return max(0.0, fee_rt + buf)
