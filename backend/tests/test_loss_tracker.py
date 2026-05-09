"""LossTracker + HWM drawdown."""

from __future__ import annotations

import time

from common.enums import Side
from common.events import EventBus
from common.types import Fill
from engine.performance.performance_tracker import PerformanceTracker
from engine.portfolio.portfolio import Portfolio
from engine.position.position_tracker import PositionTracker
from engine.risk.circuit_breaker import BreakerScope, CircuitBreaker
from engine.risk.loss_tracker import LossTracker
from engine.risk.pnl_tracker import PnLTracker


def _portfolio(cash: float) -> Portfolio:
    bus = EventBus()
    tracker = PositionTracker(bus)
    p = Portfolio(bus, tracker)
    p.seed_cash(cash)
    return p


def _record_pnl(perf: PerformanceTracker, pnl: float, idx: int = 0) -> None:
    perf.record_fill(
        Fill(
            child_id=f"C-{idx}",
            parent_id=None,
            symbol="BTCUSDT",
            side=Side.BUY,
            qty=1.0,
            price=100.0,
            fee=0.0,
            fee_asset="USDT",
        ),
        realized_pnl=pnl,
    )


def test_consecutive_losses_trip_major_breach() -> None:
    portfolio = _portfolio(1000.0)
    perf = PerformanceTracker(portfolio)
    breaker = CircuitBreaker()
    lt = LossTracker(
        portfolio=portfolio, performance=perf, breaker=breaker,
        daily_loss_kill_pct=0.0, max_consecutive_losses=3,
    )
    for i in range(3):
        _record_pnl(perf, -10.0, idx=i)
    lt.update()
    assert breaker.is_blocked(BreakerScope.ENGINE)


def test_winning_trade_resets_streak() -> None:
    portfolio = _portfolio(1000.0)
    perf = PerformanceTracker(portfolio)
    breaker = CircuitBreaker()
    lt = LossTracker(
        portfolio=portfolio, performance=perf, breaker=breaker,
        daily_loss_kill_pct=0.0, max_consecutive_losses=3,
    )
    _record_pnl(perf, -10.0, idx=0)
    _record_pnl(perf, -10.0, idx=1)
    _record_pnl(perf, +20.0, idx=2)
    lt.update()
    assert not breaker.is_blocked(BreakerScope.ENGINE)
    _record_pnl(perf, -10.0, idx=3)
    lt.update()
    # Only 1 loss in the streak; still safe.
    assert not breaker.is_blocked(BreakerScope.ENGINE)


def test_daily_loss_trips_when_equity_drops_past_threshold() -> None:
    portfolio = _portfolio(1000.0)
    perf = PerformanceTracker(portfolio)
    breaker = CircuitBreaker()
    lt = LossTracker(
        portfolio=portfolio, performance=perf, breaker=breaker,
        daily_loss_kill_pct=0.05, max_consecutive_losses=0,
    )
    lt.update(now=time.time())
    # Drop equity by 10% via cash overwrite.
    portfolio.update_cash(900.0)
    lt.update(now=time.time())
    assert breaker.is_blocked(BreakerScope.ENGINE)


def test_pnl_tracker_hwm_drawdown() -> None:
    portfolio = _portfolio(1000.0)
    pnl = PnLTracker(portfolio)
    pnl.update()
    portfolio.update_cash(1500.0)  # equity climbs
    pnl.update()
    assert pnl.hwm == 1500.0
    portfolio.update_cash(1200.0)  # gives back
    assert abs(pnl.hwm_drawdown_pct() - 0.20) < 1e-9
