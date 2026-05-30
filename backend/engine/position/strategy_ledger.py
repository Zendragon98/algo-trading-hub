"""Per-strategy virtual position ledger for multi-strategy mode.

The venue still holds one net position per symbol; this ledger tracks how
much of that exposure each strategy *intends* to own after internal netting,
plus each strategy's fill-VWAP entry for PnL (never signal mid).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from common.enums import Side

from .venue_pnl import apply_attributed_fill_vwap

_QTY_EPS = 1e-12


@dataclass(slots=True)
class _SymbolLeg:
    qty: float = 0.0
    fill_vwap: float = 0.0
    fill_qty_abs: float = 0.0


@dataclass(slots=True)
class StrategyPositionLedger:
    """Signed qty and fill-VWAP per (strategy, symbol)."""

    _legs: dict[str, dict[str, _SymbolLeg]] = field(default_factory=dict)

    def qty(self, strategy: str, symbol: str) -> float:
        leg = self._leg(strategy, symbol)
        return leg.qty if leg is not None else 0.0

    def fill_vwap(self, strategy: str, symbol: str) -> float:
        leg = self._leg(strategy, symbol)
        if leg is None or abs(leg.qty) < _QTY_EPS:
            return 0.0
        return leg.fill_vwap if leg.fill_vwap > 0 else 0.0

    def apply_delta(
        self,
        strategy: str,
        symbol: str,
        delta: float,
        *,
        price: float | None = None,
    ) -> None:
        if abs(delta) < _QTY_EPS:
            return
        sym = symbol.upper()
        book = self._legs.setdefault(strategy, {})
        leg = book.setdefault(sym, _SymbolLeg())
        prev_qty = leg.qty
        new_qty = prev_qty + delta

        if abs(new_qty) < _QTY_EPS:
            leg.qty = 0.0
            leg.fill_vwap = 0.0
            leg.fill_qty_abs = 0.0
            return

        prev_side = _sign(prev_qty)
        new_side = _sign(new_qty)
        delta_side = _sign(delta)

        if price is not None and price > 0 and abs(delta) > _QTY_EPS:
            if prev_side == 0:
                leg.fill_vwap = price
                leg.fill_qty_abs = abs(delta)
            elif prev_side != new_side:
                leg.fill_vwap = price
                leg.fill_qty_abs = abs(new_qty)
            elif delta_side == prev_side:
                leg.fill_vwap, leg.fill_qty_abs = apply_attributed_fill_vwap(
                    fill_vwap=leg.fill_vwap,
                    fill_qty_abs=leg.fill_qty_abs,
                    fill_price=price,
                    fill_qty=abs(delta),
                )
            # Partial close on same side: keep fill_vwap / fill_qty_abs.

        leg.qty = new_qty

    def apply_fill(
        self,
        strategy: str,
        symbol: str,
        side: Side,
        qty: float,
        *,
        price: float | None = None,
    ) -> None:
        delta = qty if side is Side.BUY else -qty
        self.apply_delta(strategy, symbol, delta, price=price)

    def snapshot(self) -> dict[str, dict[str, float]]:
        return {
            strat: {sym: leg.qty for sym, leg in syms.items()}
            for strat, syms in self._legs.items()
        }

    def _leg(self, strategy: str, symbol: str) -> _SymbolLeg | None:
        return self._legs.get(strategy, {}).get(symbol.upper())


def _sign(x: float) -> int:
    if x > _QTY_EPS:
        return 1
    if x < -_QTY_EPS:
        return -1
    return 0
