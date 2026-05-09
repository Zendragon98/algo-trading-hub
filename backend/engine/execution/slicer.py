"""VWAP child-order schedule generation.

Each parent order is broken into `n_slices` evenly-spaced child orders
of qty `total * w_i` where the weights `w_i` depend on the algo mode:

    NORMAL    -> uniform        : w_i = 1 / n
    FRONTLOAD -> exponential decay: w_i ∝ exp(-i / tau), tau ~ n/2
    BACKLOAD  -> reverse exponential: w_i ∝ exp((i - n) / tau)

Weights always sum to 1.0; rounding error is folded into the last slice
so that the realised qty matches the parent exactly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from common.enums import AlgoMode


@dataclass(frozen=True, slots=True)
class Slice:
    """One scheduled child within a parent order."""

    index: int
    qty: float
    delay_sec: float    # delay relative to the parent submit time


def build_schedule(
    *,
    mode: AlgoMode,
    total_qty: float,
    duration_sec: float,
    n_slices: int,
) -> list[Slice]:
    if n_slices <= 0:
        raise ValueError("n_slices must be > 0")
    if total_qty <= 0:
        raise ValueError("total_qty must be > 0")
    if duration_sec <= 0:
        raise ValueError("duration_sec must be > 0")

    weights = _weights(mode, n_slices)

    # Convert weights into actual quantities, then patch any FP drift into
    # the final slice so totals match the parent to the cent.
    qtys = [total_qty * w for w in weights]
    drift = total_qty - sum(qtys)
    qtys[-1] += drift

    interval = duration_sec / n_slices
    return [
        Slice(index=i, qty=qtys[i], delay_sec=i * interval)
        for i in range(n_slices)
    ]


def _weights(mode: AlgoMode, n: int) -> list[float]:
    if mode is AlgoMode.NORMAL or n == 1:
        return [1.0 / n] * n

    # tau ~ n/2 keeps the curve gentle: the first slice gets ~2x the
    # last slice for n=6, FRONTLOAD. Aggressive enough to matter without
    # collapsing the schedule into a single slice.
    tau = max(1.0, n / 2.0)

    if mode is AlgoMode.FRONTLOAD:
        raw = [math.exp(-i / tau) for i in range(n)]
    elif mode is AlgoMode.BACKLOAD:
        raw = [math.exp((i - (n - 1)) / tau) for i in range(n)]
    else:
        raw = [1.0] * n

    s = sum(raw)
    return [w / s for w in raw]
