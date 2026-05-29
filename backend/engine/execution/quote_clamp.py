"""Shared MM quote price clamp helpers (no strategy imports)."""

from __future__ import annotations


def clamp_targets_no_cross(
    bid: float | None,
    ask: float | None,
    *,
    best_bid: float | None,
    best_ask: float | None,
    tick: float,
) -> tuple[float | None, float | None]:
    if tick <= 0:
        return bid, ask
    if bid is not None and best_ask is not None and best_ask > 0 and bid >= best_ask:
        bid = best_ask - tick
    if ask is not None and best_bid is not None and best_bid > 0 and ask <= best_bid:
        ask = best_bid + tick
    return bid, ask
