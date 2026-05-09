"""Square-root impact estimator (optional).

The production `Engine` records venue fill prices as-is. This module remains
for experiments and unit tests; construct `ImpactModel` with an explicit
`ImpactConfig(enabled=True)` if you want the textbook calibration offline.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from common.enums import Side

from ..market_data.orderbook import OrderBook


@dataclass(frozen=True, slots=True)
class ImpactConfig:
    """Calibration knobs for the impact model."""

    enabled: bool = True
    k: float = 0.5
    min_depth: float = 1e-9
    top_n: int = 10        # depth levels considered consumable

    @classmethod
    def from_settings(cls, settings) -> "ImpactConfig":
        """The production engine does not apply this model; tests may opt in explicitly."""
        _ = settings
        return cls(enabled=False)


class ImpactModel:
    """Stateless square-root impact model.

    Holds only the calibration; all per-fill state is passed in. That
    keeps the model trivially testable and lets us swap in a different
    functional form later without rewiring callers.
    """

    def __init__(self, config: ImpactConfig) -> None:
        self._config = config

    @property
    def config(self) -> ImpactConfig:
        return self._config

    # --- Estimation ---

    def estimate_bps(self, side: Side, qty: float, book: OrderBook | None) -> float:
        """Return the estimated price-impact cost of `qty`, in bps.

        Always non-negative. The caller is responsible for applying the
        sign to a price (BUY -> add, SELL -> subtract).
        """
        if not self._config.enabled or qty <= 0 or book is None or not book.ready():
            return 0.0

        # Buys consume ask-side liquidity, sells consume bid-side.
        levels = book.asks if side is Side.BUY else book.bids
        depth = max(
            sum(lvl.qty for lvl in levels[: self._config.top_n]),
            self._config.min_depth,
        )
        return self._config.k * math.sqrt(qty / depth) * 10_000.0

    def apply(self, side: Side, qty: float, raw_price: float, book: OrderBook | None) -> tuple[float, float]:
        """Return ``(simulated_fill_price, impact_bps)``.

        ``simulated_fill_price`` is the venue fill price adjusted in the
        direction that hurts the trade. ``impact_bps`` is the magnitude
        of the adjustment so callers can attribute it in metrics.
        """
        bps = self.estimate_bps(side, qty, book)
        if bps == 0.0 or raw_price <= 0:
            return raw_price, 0.0
        # +bps for a buy makes us pay more; -bps for a sell means we
        # receive less. Either way the position is worse off than
        # the testnet fill report alone would suggest.
        adjustment = raw_price * (bps / 10_000.0) * side.sign
        return raw_price + adjustment, bps
