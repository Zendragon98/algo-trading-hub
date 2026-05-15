"""Per-strategy virtual position ledger for multi-strategy mode.

The venue still holds one net position per symbol; this ledger tracks how
much of that exposure each strategy *intends* to own after internal netting.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from common.enums import Side


@dataclass(slots=True)
class StrategyPositionLedger:
    """Signed qty per (strategy, symbol)."""

    _qty: dict[str, dict[str, float]] = field(default_factory=dict)

    def qty(self, strategy: str, symbol: str) -> float:
        return self._qty.get(strategy, {}).get(symbol.upper(), 0.0)

    def apply_delta(self, strategy: str, symbol: str, delta: float) -> None:
        if abs(delta) < 1e-15:
            return
        sym = symbol.upper()
        book = self._qty.setdefault(strategy, {})
        book[sym] = book.get(sym, 0.0) + delta

    def apply_fill(self, strategy: str, symbol: str, side: Side, qty: float) -> None:
        delta = qty if side is Side.BUY else -qty
        self.apply_delta(strategy, symbol, delta)

    def snapshot(self) -> dict[str, dict[str, float]]:
        return {s: dict(syms) for s, syms in self._qty.items()}
