"""Pre-submit safety net for the OMS.

Three checks happen on every child-order submission, before the gateway
REST call leaves the process:

    1. Engine-scope breaker — blocks every submit when latched.
    2. Symbol-scope breaker — blocks all submits for a symbol whose
       previous parents have been auto-paused.
    3. Open-parent ceiling — caps simultaneous in-flight parents.
    4. Token-bucket throttle — limits global REST submit rate so a
       runaway loop cannot spam the venue and trigger a ban.

Post-submit, `record_status()` updates a per-symbol consecutive-reject
counter. Hitting `max_consecutive_rejects` trips a minor symbol-scope
breach for `reject_cooldown_sec` so the symbol pauses while the operator
investigates, and auto-resumes after the cooldown.
"""

from __future__ import annotations

import asyncio
import logging
import time as _time
from collections import defaultdict
from collections.abc import Callable

from common.config import Settings
from common.enums import OrderStatus

from ..risk.circuit_breaker import (
    Breach,
    BreakerScope,
    BreakerSeverity,
    CircuitBreaker,
)
from ..risk.order_exposure_guard import OrderExposureGuard

logger = logging.getLogger(__name__)


class _TokenBucket:
    """Simple monotonic-clock token bucket for global submit throttling."""

    def __init__(self, rate_per_sec: float, capacity: float | None = None) -> None:
        self._rate = max(0.0, rate_per_sec)
        # Capacity defaults to one second worth of tokens so short bursts
        # are not penalised but a sustained loop is.
        self._capacity = capacity if capacity is not None else max(1.0, self._rate)
        self._tokens = self._capacity
        self._last = _time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        if self._rate <= 0:
            return
        async with self._lock:
            now = _time.monotonic()
            elapsed = max(0.0, now - self._last)
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._last = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            wait = (1.0 - self._tokens) / self._rate
        await asyncio.sleep(wait)
        # After sleeping the bucket has at least one token; consume it.
        async with self._lock:
            self._tokens = max(0.0, self._tokens - 1.0)


class SubmitGuard:
    def __init__(
        self,
        breaker: CircuitBreaker,
        open_parent_count: Callable[[], int],
        max_open_parents: int,
        submit_rate_per_sec: float,
        max_consecutive_rejects: int,
        reject_cooldown_sec: float,
        order_exposure: OrderExposureGuard | None = None,
    ) -> None:
        self._breaker = breaker
        self._open_parent_count = open_parent_count
        self._max_open_parents = max(0, int(max_open_parents))
        self._max_rejects = max(1, int(max_consecutive_rejects))
        self._reject_cooldown_sec = max(0.0, reject_cooldown_sec)
        self._bucket = _TokenBucket(submit_rate_per_sec)
        self._symbol_buckets: dict[str, _TokenBucket] = {}
        self._symbol_rate = submit_rate_per_sec
        self._reject_streak: dict[tuple[str, str], int] = defaultdict(int)
        self._order_exposure = order_exposure

    def apply_settings(self, settings: Settings) -> None:
        self._max_open_parents = max(0, int(settings.max_open_parents))
        self._max_rejects = max(1, int(settings.max_consecutive_rejects))
        self._reject_cooldown_sec = max(0.0, settings.reject_cooldown_sec)
        sym_rate = float(settings.per_symbol_submit_rate or 0.0)
        self._symbol_rate = sym_rate if sym_rate > 0 else settings.submit_rate_per_sec
        self._bucket = _TokenBucket(settings.submit_rate_per_sec)
        self._symbol_buckets = {}
        if self._order_exposure is not None:
            self._order_exposure.apply_settings(settings)

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        breaker: CircuitBreaker,
        open_parent_count: Callable[[], int],
        *,
        working_children: Callable | None = None,
        mid_for_symbol: Callable[[str], float | None] | None = None,
    ) -> SubmitGuard:
        exposure = None
        if working_children is not None and mid_for_symbol is not None:
            exposure = OrderExposureGuard.from_settings(
                settings,
                working_children=working_children,
                mid_for_symbol=mid_for_symbol,
            )
        return cls(
            breaker=breaker,
            open_parent_count=open_parent_count,
            max_open_parents=settings.max_open_parents,
            submit_rate_per_sec=settings.submit_rate_per_sec,
            max_consecutive_rejects=settings.max_consecutive_rejects,
            reject_cooldown_sec=settings.reject_cooldown_sec,
            order_exposure=exposure,
        )

    # --- Pre-submit ---

    def can_submit_parent(
        self, symbol: str, *, strategy_name: str = ""
    ) -> tuple[bool, str]:
        """Pre-router check used by ExecutionRouter.submit."""
        if self._breaker.is_engine_halted():
            logger.debug("submit blocked %s: engine_breaker", symbol)
            return False, "engine_breaker"
        if self._breaker.is_blocked(
            BreakerScope.SYMBOL, symbol, strategy_name=strategy_name
        ):
            logger.debug("submit blocked %s: symbol_breaker", symbol)
            return False, "symbol_breaker"
        if (
            self._max_open_parents > 0
            and self._open_parent_count() >= self._max_open_parents
        ):
            logger.debug("submit blocked %s: max_open_parents", symbol)
            return False, "max_open_parents"
        if self._order_exposure is not None:
            ok, reason = self._order_exposure.check(symbol, qty=0.0)
            if not ok:
                logger.debug("submit blocked %s: %s", symbol, reason)
                return False, reason
        return True, ""

    async def gate_child(
        self,
        symbol: str,
        *,
        reduce_only: bool,
        qty: float = 0.0,
        price: float | None = None,
        strategy_name: str = "",
    ) -> tuple[bool, str]:
        """Pre-OMS check + throttle wait used by OrderManager.submit_child.

        Reduce-only orders bypass *both* the engine and symbol breakers:
        these breakers exist to halt new exposure and further loss
        accumulation, but the breakers themselves trip drawdown / stale
        / kill-switch flows that need closing orders to actually leave
        the engine. Cancel-and-flatten paths set ``reduce_only`` so this
        is the right discriminator.
        """
        if not reduce_only:
            if self._breaker.is_engine_halted():
                logger.debug("child gate blocked %s: engine_breaker", symbol)
                return False, "engine_breaker"
            if self._breaker.is_blocked(
                BreakerScope.SYMBOL, symbol, strategy_name=strategy_name
            ):
                logger.debug("child gate blocked %s: symbol_breaker", symbol)
                return False, "symbol_breaker"
            if self._order_exposure is not None:
                ok, reason = self._order_exposure.check(symbol, qty=qty, price=price)
                if not ok:
                    logger.debug("child gate blocked %s: %s", symbol, reason)
                    return False, reason
        await self._bucket.acquire()
        if float(self._symbol_rate) > 0:
            sym_bucket = self._symbol_buckets.get(symbol)
            if sym_bucket is None:
                sym_bucket = _TokenBucket(self._symbol_rate)
                self._symbol_buckets[symbol] = sym_bucket
            await sym_bucket.acquire()
        return True, ""

    # --- Post-submit ---

    def clear_reject_streak(self, symbol: str, *, strategy_name: str = "") -> None:
        """Reset repeat_reject counter (e.g. benign -2022 on reduce-only)."""
        self._reject_streak.pop((symbol, strategy_name), None)

    def record_status(
        self, symbol: str, status: OrderStatus, *, strategy_name: str = ""
    ) -> None:
        """Update the consecutive-reject counter from a venue response."""
        key = (symbol, strategy_name)
        if status is OrderStatus.REJECTED:
            self._reject_streak[key] += 1
            if self._reject_streak[key] >= self._max_rejects:
                streak = self._reject_streak[key]
                logger.warning(
                    "repeat_reject streak=%d on %s strategy=%s — tripping breaker",
                    streak,
                    symbol,
                    strategy_name or "-",
                )
                self._breaker.trip(
                    Breach(
                        code="repeat_reject",
                        scope=BreakerScope.SYMBOL,
                        severity=BreakerSeverity.MINOR,
                        target=symbol,
                        cooldown_sec=self._reject_cooldown_sec,
                        detail=f"streak={streak}",
                        strategy_name=strategy_name,
                    )
                )
                # Reset so a single trip per cooldown is enough; the
                # breaker will block further submits until ARMED again.
                self._reject_streak[key] = 0
            return
        if status in (OrderStatus.ACK, OrderStatus.PARTIAL, OrderStatus.FILLED):
            # A successful round-trip clears the streak.
            self._reject_streak.pop(key, None)
