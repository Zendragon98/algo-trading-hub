"""Realised + unrealised PnL aggregation.

Most of the heavy lifting already happens in `PositionTracker`; this
module is a thin facade that lets the risk manager and the dashboard
ask "what's my PnL right now?" without needing to know the position
schema.

In addition to the session-start drawdown number used by the existing
`MAX_DRAWDOWN_PCT` kill switch, this tracker also maintains a live
high-water-mark so a session that profits and then gives back equity
trips a HWM-drawdown breaker before cumulative loss exceeds limits.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..portfolio.portfolio import Portfolio

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PnLSnapshot:
    realized: float
    unrealized: float

    @property
    def total(self) -> float:
        return self.realized + self.unrealized


class PnLTracker:
    def __init__(self, portfolio: Portfolio) -> None:
        self._portfolio = portfolio
        # Lazily initialised on first update so unit tests with a freshly
        # constructed portfolio don't anchor HWM at zero.
        self._hwm: float | None = None

    def snapshot(self) -> PnLSnapshot:
        snap = self._portfolio.snapshot()
        return PnLSnapshot(realized=snap.realized_pnl, unrealized=snap.unrealized_pnl)

    def drawdown_pct(self) -> float:
        """Drawdown from session-start equity, in (0, 1)."""
        start = self._portfolio.session_start_equity
        if start <= 0:
            return 0.0
        current = self._portfolio.snapshot().equity
        if current >= start:
            return 0.0
        return (start - current) / start

    def update(self) -> None:
        """Refresh the high-water mark. Called once per heartbeat."""
        equity = self._portfolio.snapshot().equity
        if equity <= 0:
            return
        if self._hwm is None or equity > self._hwm:
            self._hwm = equity

    def hwm_drawdown_pct(self) -> float:
        """Drawdown from the running peak equity, in [0, 1).

        Returns 0 until the HWM has been seeded (`update()` called at
        least once with positive equity).
        """
        if self._hwm is None or self._hwm <= 0:
            return 0.0
        current = self._portfolio.snapshot().equity
        if current >= self._hwm:
            return 0.0
        return (self._hwm - current) / self._hwm

    @property
    def hwm(self) -> float:
        return self._hwm or 0.0

    def reanchor_hwm_after_drawdown_rearm(self) -> None:
        """Set HWM to current equity (operator ``hwm_drawdown`` rearm).

        Otherwise ``hwm_drawdown_pct`` immediately exceeds the kill threshold
        again while equity remains below the previous peak.
        """
        equity = self._portfolio.snapshot().equity
        if equity <= 0:
            logger.info("HWM re-arm skipped: non-positive equity")
            return
        self._hwm = equity
        logger.info("HWM re-anchored after rearm: %.2f", equity)

    def reset_session(self) -> None:
        """Clear high-water mark so the next session peaks from current equity."""
        self._hwm = None
        logger.info("pnl_tracker session reset (HWM cleared)")
