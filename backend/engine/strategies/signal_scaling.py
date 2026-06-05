"""Signal-based position scaling for alpha strategies.

Cubic scaling maps conviction in [0, 1] between a risk floor and ceiling:

    qty = P_floor + (P_ceil - P_floor) × s³

``s = 0`` at the entry threshold (minimum risk-sized position);
``s = 1`` at full conviction (maximum scaled size).
"""

from __future__ import annotations


def clamp_unit_signal(signal: float) -> float:
    """Clamp a normalized signal to [-1, +1]."""
    return max(-1.0, min(1.0, signal))


def normalized_unit_signal(value: float, *, full_scale: float) -> float:
    """Map a signed raw value to [-1, +1] using ``full_scale`` as |s| = 1."""
    if full_scale <= 0:
        return clamp_unit_signal(value)
    return clamp_unit_signal(value / full_scale)


def conviction_above_entry(
    value: float,
    *,
    entry: float,
    full: float,
) -> float:
    """Map a raw magnitude to [0, 1] with 0 at ``entry`` and 1 at ``full``."""
    if full <= entry or entry <= 0:
        return 1.0 if abs(value) + 1e-12 >= entry else 0.0
    span = full - entry
    return max(0.0, min(1.0, (abs(value) - entry) / span))


def cubic_scaled_qty(
    p_floor: float,
    signal: float,
    *,
    p_ceil: float | None = None,
) -> float:
    """Interpolate between *p_floor* (signal=0) and *p_ceil* (signal=1) via s³."""
    if p_floor <= 0:
        return 0.0
    ceiling = max(p_floor, p_ceil) if p_ceil is not None else p_floor
    s = max(0.0, min(1.0, abs(signal)))
    return p_floor + (ceiling - p_floor) * (s ** 3)


def cubic_position_size(p_max: float, signal: float) -> float:
    """Legacy helper: scale from zero to *p_max* via |s|³ (no risk floor)."""
    if p_max <= 0:
        return 0.0
    s = clamp_unit_signal(signal)
    return p_max * (abs(s) ** 3)
