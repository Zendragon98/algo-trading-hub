"""Backward-compatible spread calibration loader (delegates to symbol_calibration)."""

from __future__ import annotations

from dataclasses import dataclass

from .symbol_calibration import load_symbol_calibration


@dataclass(frozen=True, slots=True)
class CalibratedSpread:
    half_spread_bps: float
    min_spread_bps: float | None = None


def load_spread_calibration(path: str | None) -> dict[str, CalibratedSpread]:
    cal = load_symbol_calibration(path)
    return {
        sym: CalibratedSpread(
            half_spread_bps=entry.half_spread_bps or 0.0,
            min_spread_bps=entry.min_spread_bps,
        )
        for sym, entry in cal.items()
        if entry.half_spread_bps is not None
    }


def calibrated_half_spread(symbol: str, path: str | None) -> float | None:
    cal = load_symbol_calibration(path)
    entry = cal.get(symbol.upper())
    return entry.half_spread_bps if entry else None


def invalidate_cache() -> None:
    from .symbol_calibration import invalidate_cache as _inv

    _inv()
