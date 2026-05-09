"""Daily-loss + consecutive-loss kill switches.

Two MAJOR breakers complement the drawdown guard already in
``RiskManager.monitor_tick``:

    - ``daily_loss``           : equity dropped by more than
                                 ``daily_loss_kill_pct`` since UTC
                                 midnight (boundary chosen so the cap
                                 resets at the same wall-clock instant
                                 every day, regardless of when the
                                 engine restarted).
    - ``consecutive_losses``   : ``max_consecutive_losses`` realised
                                 PnL records in a row are negative.

Both flow through the same `CircuitBreaker` instance the rest of the
engine consumes, latched until operator re-arm.
"""

from __future__ import annotations

import logging
import time as _time
from dataclasses import dataclass
from datetime import datetime, timezone

from common.config import Settings

from ..performance.performance_tracker import PerformanceTracker
from ..portfolio.portfolio import Portfolio
from .circuit_breaker import (
    Breach,
    BreakerScope,
    BreakerSeverity,
    CircuitBreaker,
)

logger = logging.getLogger(__name__)


def _utc_day_start(now: float) -> float:
    dt = datetime.fromtimestamp(now, tz=timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    return dt.timestamp()


@dataclass(slots=True)
class _DailyAnchor:
    day_start_ts: float
    equity_at_open: float


class LossTracker:
    def __init__(
        self,
        portfolio: Portfolio,
        performance: PerformanceTracker,
        breaker: CircuitBreaker,
        daily_loss_kill_pct: float,
        max_consecutive_losses: int,
    ) -> None:
        self._portfolio = portfolio
        self._performance = performance
        self._breaker = breaker
        self._daily_kill = max(0.0, daily_loss_kill_pct)
        self._max_streak = max(0, int(max_consecutive_losses))
        self._anchor: _DailyAnchor | None = None
        # Index into PerformanceTracker.trades() of the *oldest* trade we
        # haven't evaluated for the streak yet. The tracker stores newest
        # first, so we track by trade id to survive ring-buffer rotation.
        self._seen_trade_ids: set[str] = set()
        self._current_streak: int = 0

    def apply_settings(self, settings: Settings) -> None:
        self._daily_kill = max(0.0, settings.daily_loss_kill_pct)
        self._max_streak = max(0, int(settings.max_consecutive_losses))

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        portfolio: Portfolio,
        performance: PerformanceTracker,
        breaker: CircuitBreaker,
    ) -> "LossTracker":
        return cls(
            portfolio=portfolio,
            performance=performance,
            breaker=breaker,
            daily_loss_kill_pct=settings.daily_loss_kill_pct,
            max_consecutive_losses=settings.max_consecutive_losses,
        )

    # --- Heartbeat ---

    def update(self, now: float | None = None) -> None:
        """Refresh the daily anchor + streak; trip the breaker on breach.

        Called from ``Engine._on_clock_tick`` after mark-to-market.
        Idempotent — re-tripping a latched MAJOR breach is a no-op.
        """
        ts = now if now is not None else _time.time()
        self._refresh_daily_anchor(ts)
        self._check_daily_loss()
        self._check_streak()

    # --- Internal ---

    def _refresh_daily_anchor(self, ts: float) -> None:
        day_start = _utc_day_start(ts)
        if self._anchor is None or day_start != self._anchor.day_start_ts:
            equity = self._portfolio.snapshot().equity
            self._anchor = _DailyAnchor(day_start_ts=day_start, equity_at_open=equity)
            logger.info(
                "loss_tracker rolled to new day: anchor_equity=%.2f", equity,
            )

    def _check_daily_loss(self) -> None:
        if self._daily_kill <= 0 or self._anchor is None:
            return
        equity_at_open = self._anchor.equity_at_open
        if equity_at_open <= 0:
            return
        equity = self._portfolio.snapshot().equity
        if equity >= equity_at_open:
            return
        loss_pct = (equity_at_open - equity) / equity_at_open
        if loss_pct >= self._daily_kill:
            self._breaker.trip(
                Breach(
                    code="daily_loss",
                    scope=BreakerScope.ENGINE,
                    severity=BreakerSeverity.MAJOR,
                    detail=f"loss={loss_pct * 100:.2f}%",
                )
            )

    def _check_streak(self) -> None:
        if self._max_streak <= 0:
            return
        # PerformanceTracker.trades() returns newest first. We process in
        # chronological order so the streak counter reflects the order
        # fills landed.
        trades = list(reversed(self._performance.trades()))
        for trade in trades:
            if trade.id in self._seen_trade_ids:
                continue
            self._seen_trade_ids.add(trade.id)
            pnl = trade.pnl
            if pnl is None:
                continue
            if pnl < 0:
                self._current_streak += 1
            elif pnl > 0:
                self._current_streak = 0
            # pnl == 0 leaves the streak unchanged.
        if self._current_streak >= self._max_streak:
            self._breaker.trip(
                Breach(
                    code="consecutive_losses",
                    scope=BreakerScope.ENGINE,
                    severity=BreakerSeverity.MAJOR,
                    detail=f"streak={self._current_streak}",
                )
            )

    # --- Reads ---

    @property
    def consecutive_losses(self) -> int:
        return self._current_streak
