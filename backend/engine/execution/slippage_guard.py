"""In-flight slippage abort.

`ParentOrder.max_slippage_bps` declares the worst slippage the strategy
is willing to tolerate for a given parent. The VWAP executor never
enforced it: a flash move could push the realised VWAP arbitrarily far
from the arrival mid before the schedule completes.

`SlippageGuard.on_fill(parent_id)` is called immediately after every
ExecutionTracker fill update. It reads the live report, compares
``slippage_bps`` against the parent's declared cap, and trips a minor
PARENT-scope breach when exceeded. The engine reacts by cancelling the
parent so the remaining schedule never fires.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from ..risk.circuit_breaker import (
    Breach,
    BreakerScope,
    BreakerSeverity,
    CircuitBreaker,
)
from .execution_metrics import ExecutionTracker

logger = logging.getLogger(__name__)

# Cancel hook the engine wires in (`router.cancel(parent_id)`).
CancelParent = Callable[[str], Awaitable[None]]


class SlippageGuard:
    def __init__(
        self,
        breaker: CircuitBreaker,
        tracker: ExecutionTracker,
        cancel_parent: CancelParent,
        cooldown_sec: float = 60.0,
    ) -> None:
        self._breaker = breaker
        self._tracker = tracker
        self._cancel_parent = cancel_parent
        self._cooldown_sec = max(0.0, cooldown_sec)
        # Avoid issuing a cancel for the same parent multiple times when
        # additional fills arrive after the breach trips.
        self._aborted: set[str] = set()

    def set_cooldown_sec(self, sec: float) -> None:
        self._cooldown_sec = max(0.0, sec)

    async def on_fill(self, parent_id: str, max_slippage_bps: float) -> None:
        """Check the live execution report after a fill.

        Aborts (cancel + breach) once the absolute slippage exceeds the
        declared cap. Idempotent: subsequent fills on the same parent
        no-op until the breach record is cleared.
        """
        if parent_id in self._aborted:
            return
        report = next(
            (r for r in self._tracker.open_reports() if r.parent_id == parent_id),
            None,
        )
        if report is None:
            return
        if max_slippage_bps <= 0:
            return
        if abs(report.slippage_bps) <= max_slippage_bps:
            return

        self._aborted.add(parent_id)
        logger.warning(
            "slippage breach parent=%s slip=%.1fbps cap=%.1fbps symbol=%s — cancel + trip",
            parent_id,
            report.slippage_bps,
            max_slippage_bps,
            report.symbol,
        )
        self._breaker.trip(
            Breach(
                code="slippage_breach",
                scope=BreakerScope.PARENT,
                severity=BreakerSeverity.MINOR,
                target=parent_id,
                cooldown_sec=self._cooldown_sec,
                detail=(
                    f"slip={report.slippage_bps:.1f}bps "
                    f"cap={max_slippage_bps:.1f}bps symbol={report.symbol}"
                ),
            )
        )
        try:
            await self._cancel_parent(parent_id)
        except Exception:  # noqa: BLE001
            logger.exception("cancel_parent failed for %s after slippage breach", parent_id)
