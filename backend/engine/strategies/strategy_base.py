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

    name: str = "unnamed-strategy"

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
