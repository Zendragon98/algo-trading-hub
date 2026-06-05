"""Margin-ratio monitor with automatic position reduction.

When ``gross_notional / equity`` exceeds ``margin_ratio_reduce_pct`` the
guard emits a reduce-only exit intent for a fraction of the largest
position. Unlike MAJOR breakers this does not halt new entries — it
trims exposure until the ratio falls back under the threshold.
"""

from __future__ import annotations

import logging
import time as _time

from common.config import Settings
from common.types import Position

from ..portfolio.portfolio import Portfolio
from .risk_manager import ExitIntent

logger = logging.getLogger(__name__)


class MarginRatioGuard:
    def __init__(
        self,
        portfolio: Portfolio,
        *,
        margin_ratio_reduce_pct: float = 0.0,
        reduce_frac: float = 0.25,
        cooldown_sec: float = 30.0,
    ) -> None:
        self._portfolio = portfolio
        self._threshold = max(0.0, float(margin_ratio_reduce_pct))
        self._reduce_frac = max(0.01, min(float(reduce_frac), 1.0))
        self._cooldown_sec = max(0.0, float(cooldown_sec))
        self._last_reduce_ts: float = 0.0

    def apply_settings(self, settings: Settings) -> None:
        self._threshold = max(0.0, float(settings.margin_ratio_reduce_pct))
        self._reduce_frac = max(
            0.01, min(float(settings.margin_ratio_reduce_frac), 1.0),
        )
        self._cooldown_sec = max(0.0, float(settings.margin_ratio_reduce_cooldown_sec))

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        portfolio: Portfolio,
    ) -> MarginRatioGuard:
        return cls(
            portfolio=portfolio,
            margin_ratio_reduce_pct=settings.margin_ratio_reduce_pct,
            reduce_frac=settings.margin_ratio_reduce_frac,
            cooldown_sec=settings.margin_ratio_reduce_cooldown_sec,
        )

    def evaluate(self, now: float | None = None) -> ExitIntent | None:
        if self._threshold <= 0:
            return None
        ts = now if now is not None else _time.time()
        if self._cooldown_sec > 0 and (ts - self._last_reduce_ts) < self._cooldown_sec:
            return None

        snap = self._portfolio.snapshot()
        equity = snap.equity
        if equity <= 0 or snap.gross_notional <= 0:
            return None

        margin_ratio = snap.gross_notional / equity
        if margin_ratio < self._threshold:
            return None

        open_positions = [p for p in snap.positions if abs(p.qty) > 1e-12]
        if not open_positions:
            return None

        largest = max(open_positions, key=lambda p: p.notional)
        close_qty = abs(largest.qty) * self._reduce_frac
        if close_qty <= 0:
            return None

        self._last_reduce_ts = ts
        logger.warning(
            "margin_ratio %.1f%% >= %.1f%% — trimming %.0f%% of %s (qty=%.6f)",
            margin_ratio * 100,
            self._threshold * 100,
            self._reduce_frac * 100,
            largest.symbol,
            close_qty,
        )
        return _exit_intent(largest, close_qty, "margin_ratio")


def _exit_intent(position: Position, qty: float, reason: str) -> ExitIntent:
    return ExitIntent(
        symbol=position.symbol,
        qty=qty,
        side="sell" if position.qty > 0 else "buy",
        reason=reason,
    )
