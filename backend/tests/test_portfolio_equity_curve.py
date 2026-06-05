"""Equity curve mark-to-market when venue user-data is stale."""

from __future__ import annotations

import pytest

from common.events import EventBus
from common.types import Position
from engine.portfolio.portfolio import Portfolio, _extrema_indices
from engine.position.position_tracker import PositionTracker


@pytest.mark.asyncio
async def test_mark_to_market_uses_tick_marks_when_user_data_stale() -> None:
    bus = EventBus()
    tracker = PositionTracker(bus)
    portfolio = Portfolio(bus=bus, position_tracker=tracker)
    portfolio.seed_balances({"USDT": 10_000.0})
    tracker.seed(
        [
            Position(
                symbol="ETHUSDT",
                qty=-1.0,
                avg_entry_price=3000.0,
                mark_price=2900.0,
                exchange_unrealized_pnl=50.0,
            )
        ]
    )

    frozen = await portfolio.mark_to_market(use_mark_pnl=False)
    live = await portfolio.mark_to_market(use_mark_pnl=True)

    assert frozen.equity == 10_050.0
    assert live.equity == 10_100.0


@pytest.mark.asyncio
async def test_equity_curve_retains_full_session() -> None:
    bus = EventBus()
    tracker = PositionTracker(bus)
    portfolio = Portfolio(bus=bus, position_tracker=tracker)
    portfolio.seed_balances({"USDT": 10_000.0})

    for i in range(20):
        portfolio._cash_by_asset["USDT"] = 10_000.0 + float(i)
        await portfolio.mark_to_market()

    curve = portfolio.equity_curve()
    assert len(curve) == 20
    assert curve[0].equity == 10_000.0
    assert curve[-1].equity == 10_019.0


@pytest.mark.asyncio
async def test_session_max_drawdown_tracks_full_resolution_equity() -> None:
    bus = EventBus()
    tracker = PositionTracker(bus)
    portfolio = Portfolio(bus=bus, position_tracker=tracker)
    portfolio.seed_balances({"USDT": 10_000.0})

    equities = [10_000.0, 10_200.0, 9_800.0, 10_050.0]
    for eq in equities[1:]:
        portfolio._cash_by_asset["USDT"] = eq
        await portfolio.mark_to_market()

    assert portfolio.session_peak_equity == pytest.approx(10_200.0)
    assert portfolio.session_max_drawdown_abs == pytest.approx(400.0)
    assert portfolio.session_max_drawdown_pct == pytest.approx(400.0 / 10_200.0 * 100.0)


def test_extrema_downsample_preserves_drawdown_trough() -> None:
    values = [10_000.0, 10_200.0, 10_150.0, 9_800.0, 10_050.0, 10_100.0]
    keep = _extrema_indices(values, max_points=6)
    sampled = [values[i] for i in keep]
    assert min(sampled) == pytest.approx(9_800.0)
