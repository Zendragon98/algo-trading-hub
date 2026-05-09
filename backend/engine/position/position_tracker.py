"""Position tracking + mark-to-market.

Subscribes to the EventBus for FILL events and folds them into a
per-symbol `Position`. Mark prices are refreshed from TICK events; the
PositionTracker emits a `POSITION` event whenever a position's qty,
entry, or mark changes materially.

The class deliberately does *not* call into the gateway. Initial
position seeding (after a restart) is the engine's job — it asks the
gateway once on startup and feeds those into `seed()`.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from typing import Iterable

from common.enums import EventType, Side
from common.events import Event, EventBus
from common.types import Fill, Position, Tick

logger = logging.getLogger(__name__)


class PositionTracker:
    """Per-symbol net position with a weighted-average entry price."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._positions: dict[str, Position] = {}
        self._lock = asyncio.Lock()

    # --- Seeding ---

    def seed(self, positions: Iterable[Position]) -> None:
        for p in positions:
            self._positions[p.symbol] = p

    # --- Mutators ---

    async def on_fill(self, fill: Fill) -> None:
        """Apply a fill to the position book."""
        async with self._lock:
            position = self._positions.setdefault(
                fill.symbol, Position(symbol=fill.symbol)
            )
            self._apply_fill(position, fill)

        await self._publish(position)

    async def on_tick(self, tick: Tick) -> None:
        """Refresh mark price; cheap, called per tick."""
        position = self._positions.get(tick.symbol)
        if position is None or position.qty == 0:
            return
        if position.mark_price == tick.mid:
            return
        position.mark_price = tick.mid
        await self._publish(position)

    # --- Read-only ---

    def all(self) -> list[Position]:
        return [p for p in self._positions.values() if p.qty != 0]

    def get(self, symbol: str) -> Position | None:
        return self._positions.get(symbol)

    # --- Internals ---

    def _apply_fill(self, position: Position, fill: Fill) -> None:
        # Convert the fill into a signed delta: buys add, sells subtract.
        signed = fill.qty * fill.side.sign
        prev_qty = position.qty
        new_qty = prev_qty + signed

        if prev_qty == 0 or _same_sign(prev_qty, signed):
            # Opening or adding to the same direction: weighted-average entry.
            total_cost = position.avg_entry_price * abs(prev_qty) + fill.price * fill.qty
            total_qty = abs(prev_qty) + fill.qty
            position.avg_entry_price = total_cost / total_qty if total_qty > 0 else 0.0
        else:
            # Reducing or flipping the position: realise PnL on the
            # closed portion before adjusting entry.
            closing_qty = min(abs(prev_qty), fill.qty)
            pnl_per_unit = (fill.price - position.avg_entry_price) * (1 if prev_qty > 0 else -1)
            position.realized_pnl += pnl_per_unit * closing_qty

            if abs(signed) > abs(prev_qty):
                # Flip: residual opens a new position in the opposite direction
                # at the fill price.
                position.avg_entry_price = fill.price
            elif new_qty == 0:
                # Fully closed; reset entry so the next fill seeds it cleanly.
                position.avg_entry_price = 0.0
            # else: partial close keeps the existing weighted entry intact.

        position.qty = new_qty
        position.mark_price = fill.price if position.mark_price == 0 else position.mark_price

    async def _publish(self, position: Position) -> None:
        payload = _position_to_dict(position)
        await self._bus.publish(Event(type=EventType.POSITION, payload=payload))


def _same_sign(a: float, b: float) -> bool:
    return (a > 0 and b > 0) or (a < 0 and b < 0)


def _position_to_dict(position: Position) -> dict:
    d = asdict(position)
    d["side"] = position.side.value
    d["size"] = position.size
    d["unrealized_pnl"] = position.unrealized_pnl
    d["notional"] = position.notional
    return d


# Re-exported so tests can construct a fill with the right Side enum.
__all__ = ["PositionTracker", "Side"]
