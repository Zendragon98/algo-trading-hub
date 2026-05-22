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
import time as _time
from collections.abc import Iterable
from dataclasses import dataclass

from common.types import Position, Tick

from .limits import Limits

logger = logging.getLogger(__name__)

# How long to suppress repeat SL/TP firings on the same symbol after one
# has been emitted. The clock fires at 1 Hz; without a cooldown a
# position whose closing order is still in flight (or rejected) would
# emit a fresh exit on every tick, spamming the OMS and the venue.
_DEFAULT_COOLDOWN_SEC = 5.0
# After the venue rejects a reduce-only exit (-2022), back off so the
# 1 Hz clock does not keep spawning fresh parents while the book heals.
_VENUE_REJECT_BACKOFF_SEC = 60.0


@dataclass(slots=True)
class StopBracket:
    """Stop-loss + take-profit prices around an entry."""

    stop_price: float
    take_price: float


class StopLossMonitor:
    """Tracks SL/TP brackets keyed by symbol.

    Symbols listed in `externally_managed` are skipped entirely — the
    owning strategy is responsible for emitting its own SL/TP exits
    (e.g. pairs trading, where risk lives in basis-spread space, not
    in each leg's absolute price). For those symbols `arm()` and
    `evaluate()` are no-ops.
    """

    def __init__(
        self,
        limits: Limits,
        cooldown_sec: float = _DEFAULT_COOLDOWN_SEC,
        externally_managed: Iterable[str] | None = None,
    ) -> None:
        self._limits = limits
        self._brackets: dict[str, StopBracket] = {}
        self._cooldown_sec = max(0.0, cooldown_sec)
        # symbol -> monotonic timestamp of last emitted exit
        self._last_trigger_ts: dict[str, float] = {}
        # Position qty captured at the most recent arm() call. Used to
        # decide whether a re-arm represents a genuine scale-in/flip
        # (clear the cooldown) or a closing-fill from an in-flight exit
        # order (preserve the cooldown so the SL doesn't cascade fresh
        # exits every tick while the previous closer is still working).
        self._armed_qty: dict[str, float] = {}
        # symbol -> monotonic deadline; set when venue says flat on reduce-only
        self._venue_reject_backoff_until: dict[str, float] = {}
        self._externally_managed: frozenset[str] = frozenset(externally_managed or ())

    def arm(self, position: Position) -> StopBracket | None:
        """Arm or refresh the bracket for `position`.

        Idempotent — re-arming a symbol that already has a bracket
        replaces it with the new entry-derived levels. Useful when a
        position is added to.

        Returns None when `position.symbol` is externally managed — the
        per-leg fixed-% bracket is bypassed and the strategy's own
        risk logic is in charge.
        """
        if position.symbol in self._externally_managed:
            return None
        if position.qty == 0 or position.avg_entry_price == 0:
            self._brackets.pop(position.symbol, None)
            self._armed_qty.pop(position.symbol, None)
            raise ValueError("cannot arm bracket on flat or unpriced position")

        if position.qty > 0:  # long
            stop = position.avg_entry_price * (1 - self._limits.default_stop_loss_pct)
            take = position.avg_entry_price * (1 + self._limits.default_take_profit_pct)
        else:  # short
            stop = position.avg_entry_price * (1 + self._limits.default_stop_loss_pct)
            take = position.avg_entry_price * (1 - self._limits.default_take_profit_pct)

        bracket = StopBracket(stop_price=stop, take_price=take)
        prior_bracket = self._brackets.get(position.symbol)
        prior_qty = self._armed_qty.get(position.symbol)
        self._brackets[position.symbol] = bracket
        self._armed_qty[position.symbol] = position.qty

        # A scale-in (position grew in magnitude) or a flip (sign changed)
        # warrants clearing the previous cooldown — the new bracket should
        # be allowed to fire immediately on the next adverse tick. A
        # closing fill that shrinks the position does NOT; otherwise the
        # SL re-fires every tick while the in-flight closing parent is
        # still working, spawning duplicate exits and ReduceOnly errors.
        is_scale_in = (
            prior_qty is None
            or _sign(prior_qty) != _sign(position.qty)
            or abs(position.qty) > abs(prior_qty)
        )
        if is_scale_in:
            self._last_trigger_ts.pop(position.symbol, None)

        # Avoid log spam on closing-fill re-arms that don't change the
        # bracket levels at all (entry price is unchanged on partial
        # closes per PositionTracker._apply_fill).
        if prior_bracket is None or _bracket_changed(prior_bracket, bracket):
            logger.info(
                "armed bracket %s entry=%.4f stop=%.4f take=%.4f",
                position.symbol, position.avg_entry_price, stop, take,
            )
        return bracket

    def disarm(self, symbol: str) -> None:
        self._brackets.pop(symbol, None)
        self._last_trigger_ts.pop(symbol, None)
        self._armed_qty.pop(symbol, None)
        self._venue_reject_backoff_until.pop(symbol, None)

    def note_venue_rejected_exit(self, symbol: str, backoff_sec: float = _VENUE_REJECT_BACKOFF_SEC) -> None:
        """Back off SL/TP re-triggers after Binance -2022 (no position to reduce)."""
        until = _time.monotonic() + max(0.0, backoff_sec)
        self._venue_reject_backoff_until[symbol] = until
        self._last_trigger_ts[symbol] = _time.monotonic()

    def set_externally_managed(self, symbols: Iterable[str]) -> None:
        """Replace the externally-managed set (used on strategy hot-swap).

        Symbols newly entering the set get their per-leg bracket /
        cooldown disarmed so an in-flight SL/TP can't fire on a coin
        whose risk is now owned by the new active strategy. Symbols
        leaving the set will pick up a fresh bracket on the next tick
        via ``arm()``.
        """
        new_set = frozenset(symbols)
        added = new_set - self._externally_managed
        for sym in added:
            self.disarm(sym)
        self._externally_managed = new_set

    def evaluate(self, position: Position, tick: Tick) -> str | None:
        """Return a non-empty exit reason if the bracket triggers, else None.

        Reasons:
            "stop_loss" -> protective exit
            "take_profit" -> profit-taking exit

        A successful trigger starts a per-symbol cooldown so the engine's
        1 Hz clock doesn't keep emitting fresh exits while the previous
        closing order is still being placed (or recovering from a venue
        rejection).
        """
        if tick.symbol in self._externally_managed:
            return None
        bracket = self._brackets.get(tick.symbol)
        if bracket is None or position.qty == 0:
            return None

        backoff_until = self._venue_reject_backoff_until.get(tick.symbol)
        if backoff_until is not None and _time.monotonic() < backoff_until:
            return None

        last_ts = self._last_trigger_ts.get(tick.symbol)
        if last_ts is not None and _time.monotonic() - last_ts < self._cooldown_sec:
            return None

        reason: str | None = None
        if position.qty > 0:  # long: stop below, take above
            if tick.bid <= bracket.stop_price:
                reason = "stop_loss"
            elif tick.ask >= bracket.take_price:
                reason = "take_profit"
        else:  # short: stop above, take below
            if tick.ask >= bracket.stop_price:
                reason = "stop_loss"
            elif tick.bid <= bracket.take_price:
                reason = "take_profit"

        if reason is not None:
            self._last_trigger_ts[tick.symbol] = _time.monotonic()
        return reason

    def replace_limits(self, limits: Limits) -> None:
        """Apply updated SL/TP fractions after runtime ``Settings`` patch."""
        self._limits = limits


def _sign(x: float) -> int:
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


def _bracket_changed(prior: StopBracket, current: StopBracket) -> bool:
    return (
        abs(prior.stop_price - current.stop_price) > 1e-9
        or abs(prior.take_price - current.take_price) > 1e-9
    )
