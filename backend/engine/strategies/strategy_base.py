"""Strategy plugin interface.

Strategies are stateful; they receive a feature snapshot per tick and
return zero or more `Signal`s. The engine handles risk + execution, so
strategies should be free of any I/O concerns.

Implementations must be cheap (called from the hot path). Move heavy
calibration into `analytics/` and have the strategy read the resulting
artefact at startup.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from common.types import Signal

from ..market_data.feature_store import Features


class StrategyBase(ABC):
    """Base class for every strategy plugged into the engine."""

    # Machine-readable identifier surfaced on the wire and in logs.
    name: str = "unnamed-strategy"
    # Human-readable label rendered by the dashboard. Falls back to `name`
    # so a brand-new strategy still shows something useful before it sets
    # its own label.
    display_label: str = ""
    # One-line subtitle describing the strategy's edge for the operator.
    description: str = ""

    @abstractmethod
    def symbols(self) -> list[str]:
        """Return the symbols the strategy needs subscribed.

        The engine subscribes the union of `symbols()` across all strategies.
        """

    @abstractmethod
    def on_tick(self, features: dict[str, Features]) -> Iterable[Signal]:
        """React to a fresh feature snapshot.

        Args:
            features: per-symbol latest `Features`. The dict only contains
                symbols requested by `symbols()`.

        Returns:
            Zero or more `Signal`s for the engine to risk-check and execute.
        """

    def on_fill(self, symbol: str, qty: float, side: str) -> None:
        """Optional hook for strategies that need to react to fills.

        Default no-op so simple strategies don't need to implement it.
        """

    def manages_own_risk(self) -> bool:
        """Return True if this strategy emits its own SL/TP exit signals.

        When True, the engine's per-leg `StopLossMonitor` skips every
        symbol returned by `symbols()` — no fixed-% bracket arming, no
        SL/TP triggers. The strategy is then solely responsible for
        deciding when to exit a position.

        Default: False. Single-leg strategies (trend, mean reversion on
        one instrument) want the per-leg bracket as a safety net.

        Override to True for strategies whose risk lives in a different
        space than each leg's absolute price move — e.g. pairs trading,
        where the pair's risk is basis divergence, not a single leg
        moving 0.5%. Without this, a normal correlated tick on both
        legs trips both legs' brackets and unwinds a healthy trade.

        Portfolio-level safeguards (max drawdown kill-switch, per-trade
        notional cap, gross notional cap) remain active for these
        symbols regardless.
        """
        return False
