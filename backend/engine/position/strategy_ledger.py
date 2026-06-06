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

    def reconcile_symbol_to_venue(
        self,
        symbol: str,
        venue_qty: float,
        *,
        tol: float = 1e-9,
    ) -> dict[str, float]:
        """Align per-strategy legs for ``symbol`` to the venue net qty.

        The venue holds one net position per symbol; this ledger can drift when
        positions close outside the attributed-fill path (manual/liquidation/
        flatten/risk exits). Two safe corrections:

        - **Venue flat:** zero every strategy leg for ``symbol``.
        - **Venue same-sign but smaller than ledger net:** scale each leg down
          proportionally so the ledger net matches the venue.

        Opposite-sign or larger-than-ledger venue qty is left untouched (that is
        a different drift the reconcile breaker surfaces). Returns the applied
        per-strategy delta for legs that changed.
        """
        sym = symbol.upper()
        legs = [
            (strat, book[sym].qty)
            for strat, book in self._legs.items()
            if sym in book and abs(book[sym].qty) > _QTY_EPS
        ]
        if not legs:
            return {}
        ledger_net = sum(qty for _, qty in legs)
        flat = abs(venue_qty) <= tol
        scale = (
            not flat
            and _sign(venue_qty) == _sign(ledger_net)
            and abs(venue_qty) < abs(ledger_net) - tol
        )
        if not (flat or scale):
            return {}
        factor = 0.0 if flat else abs(venue_qty) / abs(ledger_net)
        changed: dict[str, float] = {}
        for strat, qty in legs:
            delta = qty * factor - qty
            if abs(delta) > _QTY_EPS:
                self.apply_delta(strat, sym, delta)
                changed[strat] = delta
        return changed

    def symbols(self) -> set[str]:
        return {sym for syms in self._legs.values() for sym in syms}

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
