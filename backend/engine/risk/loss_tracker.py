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
                                 PnL records in a row are negative above
                                 an optional magnitude floor.

Both flow through the same `CircuitBreaker` instance the rest of the
engine consumes, latched until operator re-arm.
"""

from __future__ import annotations

import logging
import time as _time
from dataclasses import dataclass
from datetime import UTC, datetime

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
    dt = datetime.fromtimestamp(now, tz=UTC).replace(
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
        streak_loss_min_abs_usd: float = 0.0,
        daily_loss_kill_usd: float = 0.0,
    ) -> None:
        self._portfolio = portfolio
        self._performance = performance
        self._breaker = breaker
        self._daily_kill = max(0.0, daily_loss_kill_pct)
        self._daily_kill_usd = max(0.0, float(daily_loss_kill_usd))
        self._max_streak = max(0, int(max_consecutive_losses))
        self._streak_loss_min_abs_usd = max(0.0, float(streak_loss_min_abs_usd))
        self._anchor: _DailyAnchor | None = None
        # Realized closes only; ids de-dup streak evaluation as the ring rotates.
        self._seen_trade_ids: set[str] = set()
        self._current_streak: int = 0

    def apply_settings(self, settings: Settings) -> None:
        self._daily_kill = max(0.0, settings.daily_loss_kill_pct)
        self._daily_kill_usd = max(0.0, float(settings.daily_loss_kill_usd))
        self._max_streak = max(0, int(settings.max_consecutive_losses))
        self._streak_loss_min_abs_usd = max(
            0.0, float(settings.consecutive_loss_min_abs_usd),
        )

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        portfolio: Portfolio,
        performance: PerformanceTracker,
        breaker: CircuitBreaker,
    ) -> LossTracker:
        return cls(
            portfolio=portfolio,
            performance=performance,
            breaker=breaker,
            daily_loss_kill_pct=settings.daily_loss_kill_pct,
            max_consecutive_losses=settings.max_consecutive_losses,
            streak_loss_min_abs_usd=settings.consecutive_loss_min_abs_usd,
            daily_loss_kill_usd=settings.daily_loss_kill_usd,
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
        if (self._daily_kill <= 0 and self._daily_kill_usd <= 0) or self._anchor is None:
            return
        equity_at_open = self._anchor.equity_at_open
        if equity_at_open <= 0:
            return
        equity = self._portfolio.snapshot().equity
        if equity >= equity_at_open:
            return
        loss_usd = equity_at_open - equity
        loss_pct = loss_usd / equity_at_open
        if self._daily_kill_usd > 0 and loss_usd >= self._daily_kill_usd:
            self._breaker.trip(
                Breach(
                    code="daily_loss",
                    scope=BreakerScope.ENGINE,
                    severity=BreakerSeverity.MAJOR,
                    detail=f"loss_usd={loss_usd:.2f}",
                )
            )
            return
        if self._daily_kill > 0 and loss_pct >= self._daily_kill:
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
        # Realized rows only, oldest→newest so the streak matches venue PnL
        # history (opens in the fill tape are ignored).
        trades = list(reversed(self._performance.realized_transactions()))
        for trade in trades:
            if trade.id in self._seen_trade_ids:
                continue
            self._seen_trade_ids.add(trade.id)
            if trade.exclude_from_streak:
                continue
            pnl = trade.pnl
            if pnl is None:
                continue
            if pnl < 0:
                if (
                    self._streak_loss_min_abs_usd <= 0
                    or abs(pnl) + 1e-12 >= self._streak_loss_min_abs_usd
                ):
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

    def clear_streak_after_rearm(self) -> None:
        """Reset the loss streak after operator rearms ``consecutive_losses``.

        Without this, ``_check_streak`` would re-trip on the very next tick
        because ``_current_streak`` still exceeds ``max_consecutive_losses``
        even though the latched breach was cleared.
        """
        if self._current_streak == 0:
            return
        self._current_streak = 0
        logger.info("consecutive-loss streak cleared (breaker rearm)")

    def reanchor_daily_baseline_after_rearm(self, now: float | None = None) -> None:
        """Reset today's daily-loss reference to current equity (operator rearm).

        Otherwise ``_check_daily_loss`` immediately re-trips with the same
        equity shortfall vs the previous UTC-day anchor.
        """
        ts = now if now is not None else _time.time()
        day_start = _utc_day_start(ts)
        equity = self._portfolio.snapshot().equity
        self._anchor = _DailyAnchor(day_start_ts=day_start, equity_at_open=equity)
        logger.info(
            "daily_loss baseline re-anchored after rearm: anchor_equity=%.2f",
            equity,
        )
