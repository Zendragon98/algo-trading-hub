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
from typing import Callable

from common.config import Settings
from common.enums import OrderStatus

from ..risk.circuit_breaker import (
    Breach,
    BreakerScope,
    BreakerSeverity,
    CircuitBreaker,
)

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
    ) -> None:
        self._breaker = breaker
        self._open_parent_count = open_parent_count
        self._max_open_parents = max(0, int(max_open_parents))
        self._max_rejects = max(1, int(max_consecutive_rejects))
        self._reject_cooldown_sec = max(0.0, reject_cooldown_sec)
        self._bucket = _TokenBucket(submit_rate_per_sec)
        self._symbol_buckets: dict[str, _TokenBucket] = {}
        self._symbol_rate = submit_rate_per_sec
        self._reject_streak: dict[str, int] = defaultdict(int)

    def apply_settings(self, settings: Settings) -> None:
        self._max_open_parents = max(0, int(settings.max_open_parents))
        self._max_rejects = max(1, int(settings.max_consecutive_rejects))
        self._reject_cooldown_sec = max(0.0, settings.reject_cooldown_sec)
        sym_rate = float(settings.per_symbol_submit_rate or 0.0)
        self._symbol_rate = sym_rate if sym_rate > 0 else settings.submit_rate_per_sec
        self._bucket = _TokenBucket(settings.submit_rate_per_sec)
        self._symbol_buckets = {}

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        breaker: CircuitBreaker,
        open_parent_count: Callable[[], int],
    ) -> "SubmitGuard":
        return cls(
            breaker=breaker,
            open_parent_count=open_parent_count,
            max_open_parents=settings.max_open_parents,
            submit_rate_per_sec=settings.submit_rate_per_sec,
            max_consecutive_rejects=settings.max_consecutive_rejects,
            reject_cooldown_sec=settings.reject_cooldown_sec,
        )

    # --- Pre-submit ---

    def can_submit_parent(self, symbol: str) -> tuple[bool, str]:
        """Pre-router check used by ExecutionRouter.submit."""
        if self._breaker.is_blocked(BreakerScope.ENGINE):
            return False, "engine_breaker"
        if self._breaker.is_blocked(BreakerScope.SYMBOL, symbol):
            return False, "symbol_breaker"
        if (
            self._max_open_parents > 0
            and self._open_parent_count() >= self._max_open_parents
        ):
            return False, "max_open_parents"
        return True, ""

    async def gate_child(self, symbol: str, *, reduce_only: bool) -> tuple[bool, str]:
        """Pre-OMS check + throttle wait used by OrderManager.submit_child.

        Reduce-only orders bypass *both* the engine and symbol breakers:
        these breakers exist to halt new exposure and further loss
        accumulation, but the breakers themselves trip drawdown / stale
        / kill-switch flows that need closing orders to actually leave
        the engine. Cancel-and-flatten paths set ``reduce_only`` so this
        is the right discriminator.
        """
        if not reduce_only:
            if self._breaker.is_blocked(BreakerScope.ENGINE):
                return False, "engine_breaker"
            if self._breaker.is_blocked(BreakerScope.SYMBOL, symbol):
                return False, "symbol_breaker"
        await self._bucket.acquire()
        if float(self._symbol_rate) > 0:
            sym_bucket = self._symbol_buckets.get(symbol)
            if sym_bucket is None:
                sym_bucket = _TokenBucket(self._symbol_rate)
                self._symbol_buckets[symbol] = sym_bucket
            await sym_bucket.acquire()
        return True, ""

    # --- Post-submit ---

    def record_status(self, symbol: str, status: OrderStatus) -> None:
        """Update the consecutive-reject counter from a venue response."""
        if status is OrderStatus.REJECTED:
            self._reject_streak[symbol] += 1
            if self._reject_streak[symbol] >= self._max_rejects:
                self._breaker.trip(
                    Breach(
                        code="repeat_reject",
                        scope=BreakerScope.SYMBOL,
                        severity=BreakerSeverity.MINOR,
                        target=symbol,
                        cooldown_sec=self._reject_cooldown_sec,
                        detail=f"streak={self._reject_streak[symbol]}",
                    )
                )
                # Reset so a single trip per cooldown is enough; the
                # breaker will block further submits until ARMED again.
                self._reject_streak[symbol] = 0
            return
        if status in (OrderStatus.ACK, OrderStatus.PARTIAL, OrderStatus.FILLED):
            # A successful round-trip clears the streak.
            self._reject_streak.pop(symbol, None)
