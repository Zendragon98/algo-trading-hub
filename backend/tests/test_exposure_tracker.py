"""ExposureTracker: per-symbol cap + free-margin floor."""

from __future__ import annotations

from common.events import EventBus
from common.types import Position
from engine.portfolio.portfolio import Portfolio
from engine.position.position_tracker import PositionTracker
from engine.risk.exposure_tracker import ExposureTracker


def _portfolio_with_cash(cash: float, positions: list[Position] | None = None) -> Portfolio:
    bus = EventBus()
    tracker = PositionTracker(bus)
    if positions:
        tracker.seed(positions)
    portfolio = Portfolio(bus, tracker)
    portfolio.seed_cash(cash)
    return portfolio


def test_symbol_cap_allows_when_below_threshold() -> None:
    portfolio = _portfolio_with_cash(1000.0)
    tracker = ExposureTracker(
        portfolio=portfolio, max_symbol_notional_pct=0.50, min_free_margin_pct=0.0,
    )
    # 1000 * 0.5 = 500 cap. Adding 100 is fine.
    assert tracker.symbol_ok("BTCUSDT", 100.0)


def test_symbol_cap_rejects_when_existing_plus_added_exceeds() -> None:
    existing = Position(symbol="BTCUSDT", qty=1.0, avg_entry_price=300.0, mark_price=300.0)
    portfolio = _portfolio_with_cash(1000.0, [existing])
    tracker = ExposureTracker(
        portfolio=portfolio, max_symbol_notional_pct=0.50, min_free_margin_pct=0.0,
    )
    # Cap = 500; existing notional = 300; adding 250 -> 550 > 500.
    assert not tracker.symbol_ok("BTCUSDT", 250.0)


def test_symbol_cap_disabled_when_pct_zero() -> None:
    portfolio = _portfolio_with_cash(100.0)
    tracker = ExposureTracker(
        portfolio=portfolio, max_symbol_notional_pct=0.0, min_free_margin_pct=0.0,
    )
    assert tracker.symbol_ok("BTCUSDT", 1_000_000.0)


def test_free_margin_floor_rejects_thin_headroom() -> None:
    big = Position(symbol="ETHUSDT", qty=2.0, avg_entry_price=400.0, mark_price=400.0)
    portfolio = _portfolio_with_cash(1000.0, [big])
    tracker = ExposureTracker(
        portfolio=portfolio, max_symbol_notional_pct=1.0, min_free_margin_pct=0.30,
    )
    # gross = 800; equity = 1000; adding 100 -> used=900 -> free=10% < 30%.
    assert not tracker.margin_ok(100.0)


def test_free_margin_floor_allows_when_plenty() -> None:
    portfolio = _portfolio_with_cash(1000.0)
    tracker = ExposureTracker(
        portfolio=portfolio, max_symbol_notional_pct=1.0, min_free_margin_pct=0.30,
    )
    assert tracker.margin_ok(100.0)


def test_negative_equity_rejects_everything() -> None:
    portfolio = _portfolio_with_cash(0.0)
    tracker = ExposureTracker(
        portfolio=portfolio, max_symbol_notional_pct=0.5, min_free_margin_pct=0.1,
    )
    assert not tracker.symbol_ok("BTCUSDT", 1.0)
    assert not tracker.margin_ok(1.0)
