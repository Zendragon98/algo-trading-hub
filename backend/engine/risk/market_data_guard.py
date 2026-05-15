"""Pre-trade market-data freshness + spread guard.

Sizing or exiting on a stale mid is one of the easier ways to blow up:
the WS feed can stall (server hiccup, local network), book diffs may
queue up, and the engine still confidently sends new orders against a
price that no longer exists. Likewise an abnormally wide spread (a
Binance halt, a sudden liquidity crater) means the next market fill
will land far from the mid the strategy reasoned about.

Wide-spread detection can be **static** (``spread > MAX_ENTRY_SPREAD_BPS``)
or **dynamic** (default): each symbol keeps an EWMA of observed quoted
spreads; we veto when the current spread exceeds a multiplier of that
baseline (clamped by floor/ceiling). Illiquid names naturally allow wider
quotes without tuning per coin.

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
    """Freshness + spread checker (optional EWMA-relative spread gate)."""

    def __init__(
        self,
        max_tick_age_sec: float,
        max_entry_spread_bps: float,
        cooldown_sec: float,
        *,
        spread_dynamic_enabled: bool = True,
        spread_baseline_alpha: float = 0.06,
        spread_wide_multiplier: float = 2.5,
        spread_wide_floor_bps: float = 8.0,
        spread_wide_ceiling_bps: float = 400.0,
    ) -> None:
        self._max_tick_age = max(0.0, max_tick_age_sec)
        self._max_spread_bps = max(0.0, max_entry_spread_bps)
        self._cooldown_sec = max(0.0, cooldown_sec)

        self._spread_dynamic_enabled = spread_dynamic_enabled
        self._spread_alpha = max(1e-6, min(spread_baseline_alpha, 1.0))
        self._spread_mult = max(1.0, spread_wide_multiplier)
        self._spread_floor = max(0.0, spread_wide_floor_bps)
        self._spread_ceiling = max(0.0, spread_wide_ceiling_bps)

        self._spread_ewma: dict[str, float] = {}

    def apply_settings(self, settings: Settings) -> None:
        """Update thresholds without clearing per-symbol spread EWMA memory."""
        self._max_tick_age = max(0.0, settings.max_tick_age_sec)
        self._max_spread_bps = max(0.0, settings.max_entry_spread_bps)
        self._cooldown_sec = max(0.0, settings.breaker_minor_cooldown_sec)
        self._spread_dynamic_enabled = settings.spread_dynamic_enabled
        self._spread_alpha = max(1e-6, min(settings.spread_baseline_alpha, 1.0))
        self._spread_mult = max(1.0, settings.spread_wide_multiplier)
        self._spread_floor = max(0.0, settings.spread_wide_floor_bps)
        self._spread_ceiling = max(0.0, settings.spread_wide_ceiling_bps)

    @classmethod
    def from_settings(cls, settings: Settings) -> "MarketDataGuard":
        return cls(
            max_tick_age_sec=settings.max_tick_age_sec,
            max_entry_spread_bps=settings.max_entry_spread_bps,
            cooldown_sec=settings.breaker_minor_cooldown_sec,
            spread_dynamic_enabled=settings.spread_dynamic_enabled,
            spread_baseline_alpha=settings.spread_baseline_alpha,
            spread_wide_multiplier=settings.spread_wide_multiplier,
            spread_wide_floor_bps=settings.spread_wide_floor_bps,
            spread_wide_ceiling_bps=settings.spread_wide_ceiling_bps,
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

        if spread_bps is None:
            return None

        if self._spread_dynamic_enabled:
            breach = self._evaluate_spread_dynamic(symbol, spread_bps)
        else:
            breach = self._evaluate_spread_static(symbol, spread_bps)

        return breach

    def _evaluate_spread_static(self, symbol: str, spread_bps: float) -> Breach | None:
        if self._max_spread_bps <= 0:
            return None
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

    def _evaluate_spread_dynamic(self, symbol: str, spread_bps: float) -> Breach | None:
        """EWMA-relative gate; compare using pre-update baseline so one spike can trip."""
        prev = self._spread_ewma.get(symbol)
        # Threshold uses EWMA *before* blending in this tick so a lone toxic print trips.
        baseline = prev if prev is not None else spread_bps
        allowed = self._allowed_spread_bps(baseline)

        prev_val = prev if prev is not None else spread_bps
        self._spread_ewma[symbol] = (
            self._spread_alpha * spread_bps + (1.0 - self._spread_alpha) * prev_val
        )

        if spread_bps > allowed:
            ewma_after = self._spread_ewma[symbol]
            return Breach(
                code="wide_spread",
                scope=BreakerScope.SYMBOL,
                severity=BreakerSeverity.MINOR,
                target=symbol,
                cooldown_sec=self._cooldown_sec,
                detail=(
                    f"spread={spread_bps:.1f}bps>{allowed:.1f}bps "
                    f"(dyn mult={self._spread_mult}:1 ewma~{baseline:.1f}->{ewma_after:.1f})"
                ),
            )
        return None

    def _allowed_spread_bps(self, baseline_ewma: float) -> float:
        """Upper spread (bps) we still accept for this symbol."""
        dynamic = self._spread_mult * baseline_ewma
        return min(self._spread_ceiling, max(self._spread_floor, dynamic))
