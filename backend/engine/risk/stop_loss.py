"""Per-position stop-loss / take-profit monitor.

Holds a per-symbol pair of price thresholds. When a new tick crosses a
threshold the monitor emits an exit signal which the engine forwards to
the execution router as a market parent order.

Thresholds are armed automatically the first time a position appears
(seeded from `Limits.default_stop_loss_pct` / `default_take_profit_pct`)
but can be overridden per symbol at runtime.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from common.types import Position, Tick

from .limits import Limits

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class StopBracket:
    """Stop-loss + take-profit prices around an entry."""

    stop_price: float
    take_price: float


class StopLossMonitor:
    """Tracks SL/TP brackets keyed by symbol."""

    def __init__(self, limits: Limits) -> None:
        self._limits = limits
        self._brackets: dict[str, StopBracket] = {}

    def arm(self, position: Position) -> StopBracket:
        """Arm or refresh the bracket for `position`.

        Idempotent — re-arming a symbol that already has a bracket
        replaces it with the new entry-derived levels. Useful when a
        position is added to.
        """
        if position.qty == 0 or position.avg_entry_price == 0:
            self._brackets.pop(position.symbol, None)
            raise ValueError("cannot arm bracket on flat or unpriced position")

        if position.qty > 0:  # long
            stop = position.avg_entry_price * (1 - self._limits.default_stop_loss_pct)
            take = position.avg_entry_price * (1 + self._limits.default_take_profit_pct)
        else:  # short
            stop = position.avg_entry_price * (1 + self._limits.default_stop_loss_pct)
            take = position.avg_entry_price * (1 - self._limits.default_take_profit_pct)

        bracket = StopBracket(stop_price=stop, take_price=take)
        self._brackets[position.symbol] = bracket
        logger.info(
            "armed bracket %s entry=%.4f stop=%.4f take=%.4f",
            position.symbol, position.avg_entry_price, stop, take,
        )
        return bracket

    def disarm(self, symbol: str) -> None:
        self._brackets.pop(symbol, None)

    def evaluate(self, position: Position, tick: Tick) -> str | None:
        """Return a non-empty exit reason if the bracket triggers, else None.

        Reasons:
            "stop_loss" -> protective exit
            "take_profit" -> profit-taking exit
        """
        bracket = self._brackets.get(tick.symbol)
        if bracket is None or position.qty == 0:
            return None

        if position.qty > 0:  # long: stop below, take above
            if tick.bid <= bracket.stop_price:
                return "stop_loss"
            if tick.ask >= bracket.take_price:
                return "take_profit"
        else:  # short: stop above, take below
            if tick.ask >= bracket.stop_price:
                return "stop_loss"
            if tick.bid <= bracket.take_price:
                return "take_profit"
        return None
