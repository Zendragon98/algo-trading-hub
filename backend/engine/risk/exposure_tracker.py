"""Per-symbol exposure cap + free-margin floor.

The portfolio-wide `max_gross_notional` ceiling already exists, but it
lets a single symbol consume the entire budget. ``ExposureTracker``
enforces a separate per-symbol cap (`max_symbol_notional_pct` of
equity) and refuses to open new exposure once free-margin headroom drops
below `min_free_margin_pct`.

Both checks are pre-trade — they cannot affect a position the engine has
already entered (those are governed by the SL / drawdown breakers).
"""

from __future__ import annotations

from common.config import Settings

from ..portfolio.portfolio import Portfolio


class ExposureTracker:
    def __init__(
        self,
        portfolio: Portfolio,
        max_symbol_notional_pct: float,
        min_free_margin_pct: float,
    ) -> None:
        self._portfolio = portfolio
        self._symbol_pct = max(0.0, min(max_symbol_notional_pct, 1.0))
        self._free_margin_pct = max(0.0, min(min_free_margin_pct, 1.0))

    @classmethod
    def from_settings(cls, settings: Settings, portfolio: Portfolio) -> "ExposureTracker":
        return cls(
            portfolio=portfolio,
            max_symbol_notional_pct=settings.max_symbol_notional_pct,
            min_free_margin_pct=settings.min_free_margin_pct,
        )

    def symbol_ok(self, symbol: str, additional_notional: float) -> bool:
        """True iff `symbol` can carry an additional `additional_notional`.

        Existing per-symbol notional is taken from the position tracker;
        we add `additional_notional` and compare against
        `equity * max_symbol_notional_pct`.
        """
        if self._symbol_pct <= 0:
            return True
        snap = self._portfolio.snapshot()
        equity = snap.equity
        if equity <= 0:
            return False
        cap = equity * self._symbol_pct
        existing = next(
            (p.notional for p in snap.positions if p.symbol == symbol),
            0.0,
        )
        return (existing + additional_notional) <= cap + 1e-9

    def margin_ok(self, additional_notional: float) -> bool:
        """True iff the post-trade free-margin ratio is above the floor.

        Approximation (no per-asset margin model): `free_margin_pct ~=
        1 - gross_notional / (equity * leverage)`. We cap with equity
        directly so the check works on spot venues / mocks where
        leverage is None.
        """
        if self._free_margin_pct <= 0:
            return True
        snap = self._portfolio.snapshot()
        equity = snap.equity
        if equity <= 0:
            return False
        used = snap.gross_notional + additional_notional
        # Headroom expressed as a fraction of equity (clamped to >= 0
        # for venues with notional > equity, e.g. futures with leverage).
        free_pct = max(0.0, 1.0 - used / max(equity, 1e-9))
        return free_pct >= self._free_margin_pct
