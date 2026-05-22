"""Market data quality monitoring — sequence gaps, crossed books, staleness."""

from __future__ import annotations

import logging
import time as _time
from dataclasses import dataclass
from enum import Enum

from gateways.gateway_interface import DepthDiff

from ..risk.circuit_breaker import (
    Breach,
    BreakerScope,
    BreakerSeverity,
    CircuitBreaker,
)

logger = logging.getLogger(__name__)


class DiffAction(str, Enum):
    APPLY = "apply"
    DROP_STALE = "drop_stale"
    RESNAPSHOT = "resnapshot"


@dataclass(slots=True)
class SymbolMdHealth:
    symbol: str
    sequence_gaps: int = 0
    crossed_count: int = 0
    last_diff_ts: float = 0.0
    last_diff_age_ms: float = 0.0
    needs_resnapshot: bool = False


class DataQualityMonitor:
    """Track per-symbol MD health and optionally trip breakers."""

    def __init__(
        self,
        breaker: CircuitBreaker,
        *,
        stale_resnapshot_sec: float = 30.0,
        crossed_book_breaker: bool = True,
    ) -> None:
        self.breaker = breaker
        self.stale_resnapshot_sec = stale_resnapshot_sec
        self.crossed_book_breaker = crossed_book_breaker
        self._health: dict[str, SymbolMdHealth] = {}
        self._last_expected_id: dict[str, int] = {}

    def assess(
        self,
        diff: DepthDiff,
        *,
        book_ready: bool,
        book_last_update_id: int,
    ) -> tuple[DiffAction, int]:
        """Decide whether a depth diff can be applied without corrupting the book.

        Binance sequencing (see futures diff-depth docs):
            - Drop events with ``u <= lastUpdateId``.
            - First event after a snapshot needs ``U <= lastUpdateId + 1`` and
              ``u >= lastUpdateId + 1``.
            - Later events need ``pu ==`` the previous event's ``u`` when ``pu``
              is present; otherwise ``U <= prev_u + 1``.
        """
        if not book_ready:
            return DiffAction.RESNAPSHOT, 0

        last_id = book_last_update_id
        if diff.final_update_id <= last_id:
            return DiffAction.DROP_STALE, 0

        gap = 0
        if diff.prev_final_update_id is not None:
            if diff.prev_final_update_id != last_id:
                gap = max(0, diff.first_update_id - last_id - 1)
                return DiffAction.RESNAPSHOT, gap
        elif diff.first_update_id > last_id + 1:
            gap = diff.first_update_id - last_id - 1
            return DiffAction.RESNAPSHOT, gap

        return DiffAction.APPLY, 0

    def on_applied(
        self,
        diff: DepthDiff,
        *,
        best_bid: float | None,
        best_ask: float | None,
    ) -> SymbolMdHealth:
        """Record a successfully applied diff and check top-of-book sanity."""
        sym = diff.symbol
        now = _time.time()
        h = self._health.setdefault(sym, SymbolMdHealth(symbol=sym))
        h.last_diff_ts = now
        h.last_diff_age_ms = 0.0
        h.needs_resnapshot = False
        self._last_expected_id[sym] = diff.final_update_id

        if (
            best_bid is not None
            and best_ask is not None
            and best_bid > best_ask
        ):
            h.crossed_count += 1
            logger.warning("%s crossed book: bid=%.8f ask=%.8f", sym, best_bid, best_ask)
            if self.crossed_book_breaker:
                self.breaker.trip(
                    Breach(
                        code="md_crossed_book",
                        scope=BreakerScope.SYMBOL,
                        severity=BreakerSeverity.MINOR,
                        target=sym,
                        detail=f"bid={best_bid} ask={best_ask}",
                        cooldown_sec=30.0,
                    )
                )
        return h

    def record_gap(self, symbol: str, gap: int) -> None:
        if gap <= 0:
            return
        # Large single-shot gaps are snapshot/stream desync, not packet loss.
        if gap > 500:
            logger.debug("%s MD resync: skipped %d stale update ids (not counted)", symbol, gap)
            return
        h = self._health.setdefault(symbol, SymbolMdHealth(symbol=symbol))
        h.sequence_gaps += gap
        h.needs_resnapshot = True
        logger.warning("%s MD sequence gap: skipped %d update ids", symbol, gap)

    def on_snapshot(self, symbol: str, last_update_id: int) -> None:
        self._last_expected_id[symbol] = last_update_id
        h = self._health.setdefault(symbol, SymbolMdHealth(symbol=symbol))
        h.sequence_gaps = 0
        h.crossed_count = 0
        h.needs_resnapshot = False

    def invalidate(self, symbols: list[str]) -> None:
        """Forget sequence state after a market WebSocket reconnect."""
        for sym in symbols:
            self._last_expected_id.pop(sym, None)
            h = self._health.get(sym)
            if h is not None:
                h.needs_resnapshot = True

    def tick_staleness(self, now: float | None = None) -> list[str]:
        """Return symbols that need a REST resnapshot."""
        ts = now if now is not None else _time.time()
        stale: list[str] = []
        for sym, h in self._health.items():
            if h.last_diff_ts <= 0:
                continue
            age = ts - h.last_diff_ts
            h.last_diff_age_ms = age * 1000.0
            if age >= self.stale_resnapshot_sec:
                h.needs_resnapshot = True
                stale.append(sym)
        return stale

    def metrics(self) -> dict[str, dict[str, float | int | bool]]:
        now = _time.time()
        out: dict[str, dict[str, float | int | bool]] = {}
        for sym, h in self._health.items():
            age_ms = (now - h.last_diff_ts) * 1000.0 if h.last_diff_ts > 0 else -1.0
            out[sym] = {
                "sequence_gaps": h.sequence_gaps,
                "crossed_count": h.crossed_count,
                "last_diff_age_ms": age_ms,
                "needs_resnapshot": h.needs_resnapshot,
            }
        return out
