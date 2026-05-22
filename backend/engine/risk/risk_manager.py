"""Pre-trade risk gate + live monitor.

`RiskManager.check(signal, mid_price)` is called *before* a signal is
turned into a parent order. It either:
    - returns a (possibly downscaled) qty for the signal to execute, or
    - returns None to veto the signal entirely.

`RiskManager.monitor_tick(...)` is called from the engine clock with the
latest tick for each symbol. It evaluates stop-loss / take-profit
brackets for the corresponding position and surfaces exit signals back
to the caller.

The risk manager *never* places orders directly; it returns intent and
lets the engine route through the execution layer like any other order.

All breach decisions (kill switch, stale tick, spread blowout, exposure
cap, etc.) are recorded on a shared `CircuitBreaker` so the API/UI can
observe a single source of truth for safety state.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

from common.config import Settings
from common.types import Fill, Position, Signal, Tick

from ..portfolio.portfolio import Portfolio
from .circuit_breaker import (
    Breach,
    BreakerScope,
    BreakerSeverity,
    CircuitBreaker,
)
from .exposure_tracker import ExposureTracker
from .limits import Limits
from .market_data_guard import MarketDataGuard
from .pnl_tracker import PnLTracker
from .stop_loss import StopLossMonitor

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RiskDecision:
    """The risk manager's verdict on a pre-trade signal."""

    approved: bool
    qty: float = 0.0
    reason: str = ""


@dataclass(slots=True)
class ExitIntent:
    """Risk-driven exit request emitted by the live monitor."""

    symbol: str
    qty: float           # absolute quantity to close
    side: str            # "buy" to cover a short, "sell" to close a long
    reason: str          # e.g. "stop_loss", "take_profit", "max_drawdown"


class RiskManager:
    def __init__(
        self,
        settings: Settings,
        portfolio: Portfolio,
        pnl: PnLTracker,
        stop_monitor: StopLossMonitor,
        breaker: CircuitBreaker | None = None,
        market_data_guard: MarketDataGuard | None = None,
        exposure_tracker: ExposureTracker | None = None,
    ) -> None:
        self._settings = settings
        self._limits = Limits.from_settings(settings)
        self._portfolio = portfolio
        self._pnl = pnl
        self._stops = stop_monitor
        self._breaker = breaker or CircuitBreaker()
        self._md_guard = market_data_guard or MarketDataGuard.from_settings(settings)
        self._exposure = exposure_tracker or ExposureTracker.from_settings(
            settings, portfolio,
        )

    @property
    def kill_switch(self) -> bool:
        """Back-compat: True iff a MAJOR engine-scope breach is active."""
        return self._breaker.is_engine_halted()

    @property
    def breaker(self) -> CircuitBreaker:
        return self._breaker

    @property
    def limits(self) -> Limits:
        return self._limits

    def evaluate_market_data(
        self,
        *,
        symbol: str,
        tick_ts: float | None,
        spread_bps: float | None,
    ) -> Breach | None:
        """Public wrapper around the market-data guard.

        Used by the engine's group-dispatch path so a stale leg can short
        -circuit the all-or-none submission and trip the symbol breaker
        before the partner leg is also vetted.
        """
        return self._md_guard.evaluate(
            symbol=symbol, tick_ts=tick_ts, spread_bps=spread_bps,
        )

    def update_max_risk_pct(self, value: float) -> None:
        """Allow the UI risk slider to adjust per-trade risk live."""
        self._limits = Limits(
            max_risk_pct=max(0.01, min(value, 1.0)),
            max_gross_notional=self._limits.max_gross_notional,
            max_drawdown_pct=self._limits.max_drawdown_pct,
            default_stop_loss_pct=self._limits.default_stop_loss_pct,
            default_take_profit_pct=self._limits.default_take_profit_pct,
        )
        logger.info("max_risk_pct updated to %.2f", self._limits.max_risk_pct)

    def apply_settings(self, settings: Settings) -> None:
        """Refresh limits + guards after ``PATCH /api/settings``."""
        self._settings = settings
        self._limits = Limits.from_settings(settings)
        self._md_guard.apply_settings(settings)
        self._exposure = ExposureTracker.from_settings(settings, self._portfolio)

    # --- Pre-trade gate ---

    def check(
        self,
        signal: Signal,
        mid_price: float,
        *,
        tick_ts: float | None = None,
        spread_bps: float | None = None,
    ) -> RiskDecision:
        # Engine- or symbol-scope breach blocks every entry path.
        if self._breaker.is_engine_halted():
            logger.warning("risk veto kill_switch %s", signal.symbol)
            return RiskDecision(False, reason="kill_switch active")
        if self._breaker.is_blocked(BreakerScope.SYMBOL, signal.symbol):
            logger.warning("risk veto symbol_breaker %s", signal.symbol)
            return RiskDecision(False, reason="symbol breaker active")

        if mid_price <= 0:
            logger.warning("risk veto no_mid %s", signal.symbol)
            return RiskDecision(False, reason="no mid price for symbol")

        # Market-data freshness + spread blowout (minor symbol-scope trips).
        md_breach = self._md_guard.evaluate(
            symbol=signal.symbol, tick_ts=tick_ts, spread_bps=spread_bps,
        )
        if md_breach is not None:
            self._breaker.trip(md_breach)
            return RiskDecision(False, reason=md_breach.code)

        snap = self._portfolio.snapshot()
        equity = snap.equity
        if equity <= 0:
            return RiskDecision(False, reason="non-positive equity")

        # Cap the signal so that the resulting notional is at most
        # max_risk_pct of equity, and not more than remaining headroom under
        # the per-symbol cap (defaults had max_risk_pct > max_symbol_notional_pct,
        # which previously scaled to the risk cap then always failed symbol_ok).
        max_notional_per_trade = equity * self._limits.max_risk_pct
        sym_budget = self._exposure.symbol_additional_budget(signal.symbol)
        max_notional_per_trade = min(max_notional_per_trade, sym_budget)
        requested_notional = signal.qty * mid_price
        if requested_notional > max_notional_per_trade:
            scaled_qty = max_notional_per_trade / mid_price
            logger.info(
                "scaling signal %s %.4f -> %.4f (risk cap)",
                signal.symbol, signal.qty, scaled_qty,
            )
            qty = scaled_qty
        else:
            qty = signal.qty

        notional = qty * mid_price

        # Per-symbol exposure cap (in addition to the global gross cap below).
        if not self._exposure.symbol_ok(signal.symbol, notional):
            logger.warning(
                "risk veto symbol_exposure_cap %s notional=%.2f",
                signal.symbol,
                notional,
            )
            return RiskDecision(False, reason="symbol_exposure_cap")

        # Free-margin floor: refuse to open new exposure if equity headroom
        # is already thin.
        if not self._exposure.margin_ok(notional):
            logger.warning(
                "risk veto free_margin_floor %s notional=%.2f equity=%.2f",
                signal.symbol,
                notional,
                equity,
            )
            return RiskDecision(False, reason="free_margin_floor")

        # Reject if the post-trade gross would exceed the hard ceiling.
        projected_gross = snap.gross_notional + notional
        if projected_gross > self._limits.max_gross_notional:
            logger.warning(
                "risk veto max_gross_notional %s projected=%.2f cap=%.2f",
                signal.symbol,
                projected_gross,
                self._limits.max_gross_notional,
            )
            return RiskDecision(False, reason="max_gross_notional breach")

        if qty <= 0:
            return RiskDecision(False, reason="qty rounded to zero")

        return RiskDecision(True, qty=qty)

    # --- Post-fill bookkeeping ---

    def on_fill(self, fill: Fill, position: Position) -> None:
        """Re-arm SL/TP brackets after the position changes."""
        if position.qty == 0:
            self._stops.disarm(position.symbol)
            return
        try:
            self._stops.arm(position)
        except ValueError:
            self._stops.disarm(position.symbol)

    # --- Live monitor (called from the clock) ---

    def monitor_tick(
        self,
        tick: Tick,
        positions: Iterable[Position],
    ) -> ExitIntent | None:
        # Two MAJOR engine-scope guards take precedence over per-position
        # SL/TP: session-start drawdown (legacy) and high-water-mark
        # drawdown. Either trip latches the breaker; the engine flattens
        # and stays paused until operator re-arm.
        dd = self._pnl.drawdown_pct()
        if dd >= self._limits.max_drawdown_pct:
            self._maybe_trip_drawdown("max_drawdown", dd)
            position = next((p for p in positions if p.symbol == tick.symbol), None)
            if position is not None and position.qty != 0:
                return _exit_intent(position, "max_drawdown")
            return None

        hwm_dd = self._pnl.hwm_drawdown_pct()
        hwm_kill = getattr(self._settings, "hwm_drawdown_kill_pct", 0.0)
        if hwm_kill > 0 and hwm_dd >= hwm_kill:
            self._maybe_trip_drawdown("hwm_drawdown", hwm_dd)
            position = next((p for p in positions if p.symbol == tick.symbol), None)
            if position is not None and position.qty != 0:
                return _exit_intent(position, "hwm_drawdown")
            return None

        position = next((p for p in positions if p.symbol == tick.symbol), None)
        if position is None or position.qty == 0:
            return None
        reason = self._stops.evaluate(position, tick)
        if reason is None:
            return None
        logger.info("%s triggered on %s @ %.4f", reason, tick.symbol, tick.mid)
        return _exit_intent(position, reason)

    # --- Internal ---

    def _maybe_trip_drawdown(self, code: str, dd: float) -> None:
        if self._breaker.is_blocked(BreakerScope.ENGINE):
            return
        self._breaker.trip(
            Breach(
                code=code,
                scope=BreakerScope.ENGINE,
                severity=BreakerSeverity.MAJOR,
                detail=f"dd={dd * 100:.2f}%",
            )
        )


def _exit_intent(position: Position, reason: str) -> ExitIntent:
    return ExitIntent(
        symbol=position.symbol,
        qty=abs(position.qty),
        side="sell" if position.qty > 0 else "buy",
        reason=reason,
    )
