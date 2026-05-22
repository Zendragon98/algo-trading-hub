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
