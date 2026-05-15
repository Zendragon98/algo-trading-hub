"""Position-aware signal planning for single-leg strategies.

Futures one-way mode cannot flip long→short in a single order at entry
size; this module emits reduce-only closes first, then openings once flat.
"""

from __future__ import annotations

from collections.abc import Callable

from common.enums import Side
from common.types import Signal

PositionProvider = Callable[[str], float]

_QTY_EPS = 1e-12


def side_from_qty(qty: float) -> int:
    """Return +1 long, -1 short, 0 flat."""
    if qty > _QTY_EPS:
        return 1
    if qty < -_QTY_EPS:
        return -1
    return 0


def closing_side(position_qty: float) -> Side | None:
    """Side that reduces ``position_qty`` toward flat."""
    if position_qty > _QTY_EPS:
        return Side.SELL
    if position_qty < -_QTY_EPS:
        return Side.BUY
    return None


def closes_position(signal_side: Side, position_qty: float) -> bool:
    """True when ``signal_side`` reduces an open position."""
    if position_qty > _QTY_EPS:
        return signal_side is Side.SELL
    if position_qty < -_QTY_EPS:
        return signal_side is Side.BUY
    return False


def plan_directional_signal(
    *,
    symbol: str,
    target_side: int,
    entry_qty: float,
    position_qty: float,
    reason_open: str,
    reason_close: str,
    score: float = 1.0,
) -> Signal | None:
    """Return one signal: close-first when needed, else open when flat.

    ``target_side`` is +1 (want long), -1 (want short), or 0 (want flat).
    """
    if target_side not in (-1, 0, 1):
        raise ValueError("target_side must be -1, 0, or +1")
    if entry_qty <= 0 and target_side != 0:
        return None

    actual = side_from_qty(position_qty)

    if target_side == 0:
        if actual == 0:
            return None
        side = closing_side(position_qty)
        assert side is not None
        return Signal(
            symbol=symbol,
            side=side,
            qty=abs(position_qty),
            reason=reason_close,
            score=score,
            reduce_only=True,
        )

    if actual != 0 and actual != target_side:
        side = closing_side(position_qty)
        assert side is not None
        return Signal(
            symbol=symbol,
            side=side,
            qty=abs(position_qty),
            reason=reason_close,
            score=score,
            reduce_only=True,
        )

    if actual == target_side:
        return None

    side = Side.BUY if target_side > 0 else Side.SELL
    return Signal(
        symbol=symbol,
        side=side,
        qty=entry_qty,
        reason=reason_open,
        score=score,
        reduce_only=False,
    )
