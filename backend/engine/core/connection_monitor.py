"""WS / user-data freshness monitor.

A silent feed (network blip, server-side hiccup, exchange downtime,
expired listenKey) is one of the most dangerous failure modes: the
engine keeps emitting orders against a frozen view of the book and
never receives back the fills it expects.

`ConnectionMonitor.tick(now)` is called from the engine heartbeat with
the latest `last_tick_ts` (any market-data WS event) and
`last_user_data_ts` (user-data WS only: fills, order updates,
``ACCOUNT_UPDATE`` — not bumped by periodic REST reconcile). If either timestamp
exceeds `ws_stale_pause_sec` of staleness, a minor ENGINE-scope breach
is tripped which auto-clears (cooldown-resumes) once data flows again.

This module never *unpauses* the engine on its own — it only manages
the breach. Phase 0's auto-flatten path is deliberately limited to
MAJOR breaches; minor stale-tick trips are pure pauses.
"""

from __future__ import annotations

import logging

from common.config import Settings

from ..risk.circuit_breaker import (
    Breach,
    BreakerScope,
    BreakerSeverity,
    CircuitBreaker,
)

logger = logging.getLogger(__name__)


class ConnectionMonitor:
    def __init__(
        self,
        breaker: CircuitBreaker,
        ws_stale_pause_sec: float,
        user_data_stale_sec: float,
        cooldown_sec: float,
    ) -> None:
        self._breaker = breaker
        self._stale_threshold = max(0.0, ws_stale_pause_sec)
        self._user_stale_threshold = max(0.0, user_data_stale_sec)
        self._cooldown_sec = max(0.0, cooldown_sec)
        self._market_was_stale = False
        self._user_was_stale = False

    def apply_settings(self, settings: Settings) -> None:
        self._stale_threshold = max(0.0, settings.ws_stale_pause_sec)
        self._user_stale_threshold = max(0.0, settings.user_data_stale_sec)
        self._cooldown_sec = max(0.0, settings.breaker_minor_cooldown_sec)

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        breaker: CircuitBreaker,
    ) -> "ConnectionMonitor":
        return cls(
            breaker=breaker,
            ws_stale_pause_sec=settings.ws_stale_pause_sec,
            user_data_stale_sec=settings.user_data_stale_sec,
            cooldown_sec=settings.breaker_minor_cooldown_sec,
        )

    def evaluate(
        self,
        *,
        now: float,
        last_tick_ts: float,
        last_user_data_ts: float,
        engine_running: bool,
        check_user_data_stale: bool = True,
    ) -> None:
        """Trip / refresh the stale-feed breach according to current ages.

        ``last_user_data_ts`` is allowed to be ``0`` before any fill
        lands (user-data only emits when something changes). User-data
        staleness is only enforced while working orders are open — an
        idle account may not receive ACCOUNT_UPDATE for minutes.
        """
        if not engine_running or self._stale_threshold <= 0:
            return

        if last_tick_ts > 0:
            tick_age = max(0.0, now - last_tick_ts)
            if tick_age > self._stale_threshold:
                if not self._market_was_stale:
                    logger.warning(
                        "stale market data: tick_age=%.1fs threshold=%.1fs — tripping breaker",
                        tick_age,
                        self._stale_threshold,
                    )
                    self._market_was_stale = True
                self._breaker.trip(
                    Breach(
                        code="stale_market_data",
                        scope=BreakerScope.ENGINE,
                        severity=BreakerSeverity.MINOR,
                        cooldown_sec=self._cooldown_sec,
                        detail=f"tick_age={tick_age:.1f}s",
                    )
                )
            elif self._market_was_stale:
                logger.info(
                    "market data recovered: tick_age=%.1fs (threshold=%.1fs)",
                    tick_age,
                    self._stale_threshold,
                )
                self._market_was_stale = False

        user_limit = self._user_stale_threshold or self._stale_threshold
        if check_user_data_stale and last_user_data_ts > 0 and user_limit > 0:
            user_age = max(0.0, now - last_user_data_ts)
            if user_age > user_limit:
                if not self._user_was_stale:
                    logger.warning(
                        "stale user data: user_age=%.1fs threshold=%.1fs — tripping breaker",
                        user_age,
                        user_limit,
                    )
                    self._user_was_stale = True
                self._breaker.trip(
                    Breach(
                        code="stale_user_data",
                        scope=BreakerScope.ENGINE,
                        severity=BreakerSeverity.MINOR,
                        cooldown_sec=self._cooldown_sec,
                        detail=f"user_age={user_age:.1f}s",
                    )
                )
            elif self._user_was_stale:
                logger.info(
                    "user data recovered: user_age=%.1fs (threshold=%.1fs)",
                    user_age,
                    user_limit,
                )
                self._user_was_stale = False
