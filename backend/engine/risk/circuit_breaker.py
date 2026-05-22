"""Unified circuit-breaker state machine for tiered safety failsafes.

Every safety trip — pre-trade veto, in-flight slippage abort, drawdown
kill, WS disconnect, etc. — is recorded as a `Breach` against this
breaker. Each breach has:

    scope     = ENGINE | SYMBOL | PARENT
    severity  = MINOR (auto-resume after cooldown) | MAJOR (latched)
    code      = stable identifier ("stale_tick", "max_drawdown", ...)
    target    = symbol / parent_id when scope is narrower than ENGINE

Consumers ask `is_blocked(scope, target)` instead of holding their own
ad-hoc booleans. The engine clock calls `tick()` once per heartbeat to
advance any cooled-down minor breaches back to `ARMED`. The operator
re-arms latched majors via the API.

Notifications are published on the EventBus (`EventType.BREAKER`) so the
React console can render an audit log of every trip and recovery.
"""

from __future__ import annotations

import logging
import time as _time
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from common.enums import EventType
from common.events import Event, EventBus

logger = logging.getLogger(__name__)


class BreakerScope(str, Enum):
    ENGINE = "engine"   # halts new orders system-wide
    SYMBOL = "symbol"   # halts new orders for one symbol
    PARENT = "parent"   # affects a single parent order


class BreakerSeverity(str, Enum):
    MINOR = "minor"     # auto-resume after `cooldown_sec`
    MAJOR = "major"     # latched until operator re-arm


class BreakerState(str, Enum):
    ARMED = "armed"        # ready, no breach active
    TRIPPED = "tripped"    # just fired; consumers should react this tick
    COOLDOWN = "cooldown"  # minor breach waiting for cooldown to elapse
    LATCHED = "latched"    # major breach; operator must re-arm


@dataclass(frozen=True, slots=True)
class Breach:
    """One failsafe trip request."""

    code: str
    scope: BreakerScope
    severity: BreakerSeverity
    target: str | None = None
    cooldown_sec: float = 60.0
    detail: str = ""


@dataclass(slots=True)
class BreakerStatus:
    """Live state of one active breach (one per (code, target))."""

    code: str
    scope: BreakerScope
    severity: BreakerSeverity
    target: str | None
    state: BreakerState
    tripped_at: float
    cooldown_until: float | None  # None for LATCHED
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "scope": self.scope.value,
            "severity": self.severity.value,
            "target": self.target,
            "state": self.state.value,
            "tripped_at": self.tripped_at,
            "cooldown_until": self.cooldown_until,
            "detail": self.detail,
        }


class CircuitBreaker:
    """Tiered breach registry shared across the risk + execution stack."""

    def __init__(self, bus: EventBus | None = None) -> None:
        self._bus = bus
        # Active breach key -> status. Key is (code, target) so distinct
        # breaches don't overwrite each other (e.g. stale_tick on ETHUSDT
        # vs BTCUSDT can both be active simultaneously).
        self._active: dict[tuple[str, str | None], BreakerStatus] = {}
        # Audit log of completed breaches (resumed or re-armed). Bounded
        # so a long session doesn't bloat memory.
        self._history: list[BreakerStatus] = []
        self._history_size = 100

    # --- Trip / clear ---

    def trip(self, breach: Breach) -> BreakerStatus:
        """Record `breach` and return its live status.

        Idempotent: re-tripping a breach that's already active refreshes
        the timestamp + detail but does NOT downgrade severity (a minor
        cannot demote a latched major).
        """
        key = (breach.code, breach.target)
        now = _time.time()
        existing = self._active.get(key)
        if existing is not None and existing.severity is BreakerSeverity.MAJOR:
            existing.tripped_at = now
            existing.detail = breach.detail or existing.detail
            return existing

        if breach.severity is BreakerSeverity.MAJOR:
            state = BreakerState.LATCHED
            cooldown_until: float | None = None
        else:
            state = BreakerState.COOLDOWN
            cooldown_until = now + max(0.0, breach.cooldown_sec)

        status = BreakerStatus(
            code=breach.code,
            scope=breach.scope,
            severity=breach.severity,
            target=breach.target,
            state=state,
            tripped_at=now,
            cooldown_until=cooldown_until,
            detail=breach.detail,
        )
        self._active[key] = status
        logger.warning(
            "breaker tripped: code=%s scope=%s severity=%s target=%s detail=%s",
            breach.code, breach.scope.value, breach.severity.value,
            breach.target or "-", breach.detail or "-",
        )
        self._publish(status, action="tripped")
        return status

    def rearm(self, code: str | None = None, target: str | None = None) -> int:
        """Operator-driven re-arm. Returns count of cleared breaches.

        - rearm()                   -> clear ALL latched (majors)
        - rearm(code=X)             -> clear all latched for `code`
        - rearm(code=X, target=Y)   -> clear that specific breach
        """
        cleared = 0
        for key in list(self._active.keys()):
            status = self._active[key]
            if code is not None and status.code != code:
                continue
            if target is not None and status.target != target:
                continue
            self._archive(status, reason="rearmed")
            del self._active[key]
            cleared += 1
        return cleared

    def tick(self) -> None:
        """Advance any COOLDOWN breaches whose timer has elapsed."""
        now = _time.time()
        for key in list(self._active.keys()):
            status = self._active[key]
            if status.state is not BreakerState.COOLDOWN:
                continue
            if status.cooldown_until is not None and now >= status.cooldown_until:
                self._archive(status, reason="cooled_down")
                del self._active[key]

    # --- Reads ---

    def is_engine_halted(self) -> bool:
        """True when a latched MAJOR engine breach is active (kill switch).

        MINOR engine trips (stale user stream, reconcile lag) are recorded
        for the dashboard but must not halt entries — they auto-cool down.
        """
        for status in self._active.values():
            if status.scope is not BreakerScope.ENGINE:
                continue
            if status.severity is not BreakerSeverity.MAJOR:
                continue
            if status.state in (
                BreakerState.COOLDOWN,
                BreakerState.LATCHED,
                BreakerState.TRIPPED,
            ):
                return True
        return False

    def is_blocked(
        self,
        scope: BreakerScope,
        target: str | None = None,
    ) -> bool:
        """True iff any active breach blocks the given scope/target.

        Engine-scope breaches block everything below them. Symbol-scope
        breaches block that symbol (and the engine when `scope=ENGINE`).

        ENGINE-scope *minor* breaches are excluded: they are telemetry /
        auto-cooling only and must not block symbol-level entry gates.
        """
        for status in self._active.values():
            if status.scope is BreakerScope.ENGINE and status.severity is BreakerSeverity.MINOR:
                continue
            if not _affects(status, scope, target):
                continue
            if status.state in (BreakerState.COOLDOWN, BreakerState.LATCHED, BreakerState.TRIPPED):
                return True
        return False

    def active(self) -> list[BreakerStatus]:
        return list(self._active.values())

    def history(self) -> list[BreakerStatus]:
        return list(reversed(self._history))

    # --- Internal ---

    def _archive(self, status: BreakerStatus, *, reason: str) -> None:
        status.state = BreakerState.ARMED
        self._history.append(status)
        if len(self._history) > self._history_size:
            self._history = self._history[-self._history_size:]
        logger.info(
            "breaker cleared: code=%s target=%s reason=%s",
            status.code, status.target or "-", reason,
        )
        self._publish(status, action=reason)

    def _publish(self, status: BreakerStatus, *, action: str) -> None:
        if self._bus is None:
            return
        payload = status.to_dict()
        payload["action"] = action
        # Fire-and-forget; publish_nowait drops silently when no event
        # loop is running (e.g. unit tests) so this is safe from any path.
        self._bus.publish_nowait(Event(type=EventType.BREAKER, payload=payload))


def _affects(status: BreakerStatus, scope: BreakerScope, target: str | None) -> bool:
    """Whether `status` blocks the requested (scope, target)."""
    if status.scope is BreakerScope.ENGINE:
        return True
    if status.scope is BreakerScope.SYMBOL:
        if scope is BreakerScope.ENGINE:
            return False
        return target is None or status.target == target
    if status.scope is BreakerScope.PARENT:
        if scope is BreakerScope.PARENT:
            return target is None or status.target == target
        return False
    return False


def keys_for(breaches: Iterable[Breach]) -> list[tuple[str, str | None]]:
    """Helper for tests: stable keying for active-breach lookup."""
    return [(b.code, b.target) for b in breaches]
