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
from collections.abc import Iterable
from dataclasses import asdict

from common.enums import EventType, Side
from common.events import Event, EventBus
from common.types import Fill, Position, Tick

logger = logging.getLogger(__name__)


class PositionTracker:
    """Per-symbol net position with a weighted-average entry price."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._positions: dict[str, Position] = {}
        # Avg entry retained when ACCOUNT_UPDATE pops a flat row before the fill WS.
        self._entry_before_flat: dict[str, float] = {}
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
        # If we're in exchange-driven PnL mode (ACCOUNT_UPDATE), our local
        # mark ticks should not reintroduce derived PnL; keep unrealized
        # PnL as last reported by the venue.
        await self._publish(position)

    async def apply_exchange_positions(self, positions: Iterable[Position]) -> None:
        """Replace tracked positions with exchange-reported state.

        Used by venue adapters that provide ACCOUNT_UPDATE / position
        snapshots. Zero-qty rows pop the symbol so a venue-side close
        (ADL, manual flatten, opposite fill) propagates to the dashboard
        instead of leaving a stale row in the OPEN POSITIONS panel.
        """
        # Materialise once so the publish pass below sees the same data
        # the lock-guarded apply pass mutated.
        snapshot = list(positions)
        async with self._lock:
            for p in snapshot:
                if p.qty == 0:
                    existing = self._positions.get(p.symbol)
                    if existing is not None and abs(existing.avg_entry_price) > 1e-12:
                        self._entry_before_flat[p.symbol] = existing.avg_entry_price
                    self._positions.pop(p.symbol, None)
                    continue
                self._positions[p.symbol] = p
                self._entry_before_flat.pop(p.symbol, None)
        for p in snapshot:
            # Always publish, including the qty==0 close, so the WS hook
            # in the frontend can drop the symbol from the position panel.
            await self._publish(p)

    async def sync_from_venue(self, positions: Iterable[Position]) -> None:
        """Replace the local book with the venue open-position snapshot.

        Unlike ``apply_exchange_positions``, symbols missing from the REST
        response are removed locally (venue flat). Used by operator flatten.
        """
        open_positions = [p for p in positions if abs(p.qty) > 1e-12]
        async with self._lock:
            removed = set(self._positions) - {p.symbol for p in open_positions}
            for sym in removed:
                existing = self._positions[sym]
                if abs(existing.avg_entry_price) > 1e-12:
                    self._entry_before_flat[sym] = existing.avg_entry_price
            self._positions = {p.symbol: p for p in open_positions}
            for p in open_positions:
                self._entry_before_flat.pop(p.symbol, None)
        for sym in removed:
            await self._publish(Position(symbol=sym, qty=0.0))
        for p in open_positions:
            await self._publish(p)

    # --- Read-only ---

    def all(self) -> list[Position]:
        return [p for p in self._positions.values() if p.qty != 0]

    def get(self, symbol: str) -> Position | None:
        return self._positions.get(symbol)

    def entry_before_flat(self, symbol: str) -> float | None:
        """Avg entry cached when the venue row went flat before the fill event."""

        entry = self._entry_before_flat.get(symbol)
        return entry if entry is not None and entry > 0 else None

    def clear_entry_before_flat(self, symbol: str) -> None:
        self._entry_before_flat.pop(symbol, None)

    # --- Internals ---

    def _apply_fill(self, position: Position, fill: Fill) -> None:
        # Convert the fill into a signed delta: buys add, sells subtract.
        signed = fill.qty * fill.side.sign
        prev_qty = position.qty
        new_qty = prev_qty + signed

        if prev_qty == 0 or _same_sign(prev_qty, signed):
            self._entry_before_flat.pop(fill.symbol, None)
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
                if position.avg_entry_price > 0:
                    self._entry_before_flat[fill.symbol] = position.avg_entry_price
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
