"""MarginRatioGuard auto-reduce."""

from __future__ import annotations

from common.events import EventBus
from common.types import Position
from engine.portfolio.portfolio import Portfolio
from engine.position.position_tracker import PositionTracker
from engine.risk.margin_ratio_guard import MarginRatioGuard


def _portfolio_with_position(notional: float, equity_cash: float) -> Portfolio:
    bus = EventBus()
    tracker = PositionTracker(bus)
    portfolio = Portfolio(bus, tracker)
    portfolio.seed_cash(equity_cash)
    tracker.seed([
        Position(
            symbol="BTCUSDT",
            qty=1.0,
            avg_entry_price=notional,
            mark_price=notional,
        ),
    ])
    return portfolio


def test_margin_ratio_emits_trim_intent() -> None:
    portfolio = _portfolio_with_position(notional=900.0, equity_cash=1000.0)
    guard = MarginRatioGuard(
        portfolio,
        margin_ratio_reduce_pct=0.85,
        reduce_frac=0.25,
        cooldown_sec=0.0,
    )
    intent = guard.evaluate(now=1.0)
    assert intent is not None
    assert intent.symbol == "BTCUSDT"
    assert intent.reason == "margin_ratio"
    assert intent.qty == 0.25


def test_margin_ratio_disabled_when_threshold_zero() -> None:
    portfolio = _portfolio_with_position(notional=900.0, equity_cash=1000.0)
    guard = MarginRatioGuard(portfolio, margin_ratio_reduce_pct=0.0)
    assert guard.evaluate() is None
