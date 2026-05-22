"""Execution-quality circuit breaker.

If the rolling average slippage on completed parents exceeds
``EXEC_QUALITY_KILL_BPS`` over the last ``EXEC_QUALITY_WINDOW`` parents,
trip a MAJOR engine breach. This catches systemic execution problems
(market microstructure regime change, wheel mis-calibration, gateway
returning stale fills) that no per-parent slippage cap would notice.
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
from .execution_metrics import ExecutionTracker

logger = logging.getLogger(__name__)


class ExecutionQualityGuard:
    def __init__(
        self,
        breaker: CircuitBreaker,
        tracker: ExecutionTracker,
        kill_bps: float,
        window: int,
    ) -> None:
        self._breaker = breaker
        self._tracker = tracker
        self._kill_bps = max(0.0, kill_bps)
        self._window = max(1, int(window))

    def apply_settings(self, settings: Settings) -> None:
        self._kill_bps = max(0.0, settings.exec_quality_kill_bps)
        self._window = max(1, int(settings.exec_quality_window))

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        breaker: CircuitBreaker,
        tracker: ExecutionTracker,
    ) -> ExecutionQualityGuard:
        return cls(
            breaker=breaker,
            tracker=tracker,
            kill_bps=settings.exec_quality_kill_bps,
            window=settings.exec_quality_window,
        )

    def evaluate(self) -> None:
        """Trip the breaker when the rolling avg slippage exceeds the cap."""
        if self._kill_bps <= 0:
            return
        history = self._tracker.history()
        if len(history) < self._window:
            return
        # `history()` returns newest first; take the most recent window.
        sample = history[: self._window]
        avg = sum(abs(r.slippage_bps) for r in sample) / len(sample)
        if avg <= self._kill_bps:
            return
        logger.warning(
            "exec quality breach avg_slip=%.1fbps > kill=%.1fbps (window=%d)",
            avg,
            self._kill_bps,
            len(sample),
        )
        self._breaker.trip(
            Breach(
                code="exec_quality",
                scope=BreakerScope.ENGINE,
                severity=BreakerSeverity.MAJOR,
                detail=f"avg_slip={avg:.1f}bps>{self._kill_bps:.1f}bps n={len(sample)}",
            )
        )
