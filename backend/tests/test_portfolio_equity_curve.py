"""Equity curve mark-to-market when venue user-data is stale."""

from __future__ import annotations

import pytest

from common.events import EventBus
from common.types import Position
from engine.portfolio.portfolio import Portfolio
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
