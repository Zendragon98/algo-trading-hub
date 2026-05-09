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
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

from common.config import Settings
from common.types import Fill, Position, Signal, Tick

from ..portfolio.portfolio import Portfolio
from .limits import Limits
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
    ) -> None:
        self._settings = settings
        self._limits = Limits.from_settings(settings)
        self._portfolio = portfolio
        self._pnl = pnl
        self._stops = stop_monitor
        self._kill_switch = False  # tripped by max-drawdown breach

    @property
    def kill_switch(self) -> bool:
        return self._kill_switch

    @property
    def limits(self) -> Limits:
        return self._limits

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

    # --- Pre-trade gate ---

    def check(self, signal: Signal, mid_price: float) -> RiskDecision:
        if self._kill_switch:
            return RiskDecision(False, reason="kill_switch active")

        if mid_price <= 0:
            return RiskDecision(False, reason="no mid price for symbol")

        snap = self._portfolio.snapshot()
        equity = snap.equity
        if equity <= 0:
            return RiskDecision(False, reason="non-positive equity")

        # Cap the signal so that the resulting notional is at most
        # max_risk_pct of equity. If the strategy already requested less
        # we leave the qty untouched.
        max_notional_per_trade = equity * self._limits.max_risk_pct
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

        # Reject if the post-trade gross would exceed the hard ceiling.
        projected_gross = snap.gross_notional + qty * mid_price
        if projected_gross > self._limits.max_gross_notional:
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
        # Drawdown guard takes precedence; if breached, trip the kill
        # switch and ask the engine to flatten everything.
        dd = self._pnl.drawdown_pct()
        if dd >= self._limits.max_drawdown_pct and not self._kill_switch:
            self._kill_switch = True
            logger.error("MAX DRAWDOWN breached (%.2f%%); kill switch armed", dd * 100)
            # Emit a synthetic exit for the symbol of this tick so the
            # engine starts unwinding immediately. The engine will iterate
            # remaining positions on subsequent ticks.
            position = next((p for p in positions if p.symbol == tick.symbol), None)
            if position is not None and position.qty != 0:
                return _exit_intent(position, "max_drawdown")
            return None

        position = next((p for p in positions if p.symbol == tick.symbol), None)
        if position is None or position.qty == 0:
            return None
        reason = self._stops.evaluate(position, tick)
        if reason is None:
            return None
        logger.info("%s triggered on %s @ %.4f", reason, tick.symbol, tick.mid)
        return _exit_intent(position, reason)


def _exit_intent(position: Position, reason: str) -> ExitIntent:
    return ExitIntent(
        symbol=position.symbol,
        qty=abs(position.qty),
        side="sell" if position.qty > 0 else "buy",
        reason=reason,
    )
