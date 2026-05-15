"""Market data quality monitoring — sequence gaps, crossed books, staleness."""

from __future__ import annotations

import logging
import time as _time
from dataclasses import dataclass

from gateways.gateway_interface import DepthDiff

from ..risk.circuit_breaker import (
    Breach,
    BreakerScope,
    BreakerSeverity,
    CircuitBreaker,
)

logger = logging.getLogger(__name__)


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

    def on_diff(self, diff: DepthDiff, *, best_bid: float | None, best_ask: float | None) -> SymbolMdHealth:
        sym = diff.symbol
        now = _time.time()
        h = self._health.setdefault(sym, SymbolMdHealth(symbol=sym))
        h.last_diff_ts = now
        h.last_diff_age_ms = 0.0

        prev = self._last_expected_id.get(sym)
        if prev is not None and diff.final_update_id > prev + 1:
            gap = diff.final_update_id - prev - 1
            h.sequence_gaps += int(gap)
            h.needs_resnapshot = True
            logger.warning("%s MD sequence gap: skipped %d update ids", sym, gap)
        self._last_expected_id[sym] = diff.final_update_id

        if (
            best_bid is not None
            and best_ask is not None
            and best_bid >= best_ask
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

    def on_snapshot(self, symbol: str, last_update_id: int) -> None:
        self._last_expected_id[symbol] = last_update_id
        h = self._health.setdefault(symbol, SymbolMdHealth(symbol=symbol))
        h.needs_resnapshot = False

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
