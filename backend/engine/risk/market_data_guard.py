"""Pre-trade market-data freshness + spread guard.

Sizing or exiting on a stale mid is one of the easier ways to blow up:
the WS feed can stall (server hiccup, local network), book diffs may
queue up, and the engine still confidently sends new orders against a
price that no longer exists. Likewise an abnormally wide spread (a
Binance halt, a sudden liquidity crater) means the next market fill
will land far from the mid the strategy reasoned about.

`MarketDataGuard.evaluate(...)` returns a `Breach` to be fed into the
shared `CircuitBreaker` so that:

    - `stale_tick`  -> minor SYMBOL trip; auto-resumes after fresh ticks
    - `wide_spread` -> minor SYMBOL trip; auto-resumes after spread
                       returns to normal

The guard never trips on missing data alone (cold-start symbols have no
``tick_ts`` until the first WS event); it only fires once a value is
present and exceeds the configured threshold.
"""

from __future__ import annotations

import time as _time

from common.config import Settings

from .circuit_breaker import Breach, BreakerScope, BreakerSeverity


class MarketDataGuard:
    """Stateless freshness + spread checker."""

    def __init__(
        self,
        max_tick_age_sec: float,
        max_entry_spread_bps: float,
        cooldown_sec: float,
    ) -> None:
        self._max_tick_age = max(0.0, max_tick_age_sec)
        self._max_spread_bps = max(0.0, max_entry_spread_bps)
        self._cooldown_sec = max(0.0, cooldown_sec)

    @classmethod
    def from_settings(cls, settings: Settings) -> "MarketDataGuard":
        return cls(
            max_tick_age_sec=settings.max_tick_age_sec,
            max_entry_spread_bps=settings.max_entry_spread_bps,
            cooldown_sec=settings.breaker_minor_cooldown_sec,
        )

    def evaluate(
        self,
        *,
        symbol: str,
        tick_ts: float | None,
        spread_bps: float | None,
    ) -> Breach | None:
        """Return a `Breach` if any guard fires, else None."""
        if tick_ts is not None and self._max_tick_age > 0:
            age = max(0.0, _time.time() - tick_ts)
            if age > self._max_tick_age:
                return Breach(
                    code="stale_tick",
                    scope=BreakerScope.SYMBOL,
                    severity=BreakerSeverity.MINOR,
                    target=symbol,
                    cooldown_sec=self._cooldown_sec,
                    detail=f"age={age:.1f}s>{self._max_tick_age:.1f}s",
                )
        if spread_bps is not None and self._max_spread_bps > 0:
            if spread_bps > self._max_spread_bps:
                return Breach(
                    code="wide_spread",
                    scope=BreakerScope.SYMBOL,
                    severity=BreakerSeverity.MINOR,
                    target=symbol,
                    cooldown_sec=self._cooldown_sec,
                    detail=f"spread={spread_bps:.1f}bps>{self._max_spread_bps:.1f}bps",
                )
        return None
