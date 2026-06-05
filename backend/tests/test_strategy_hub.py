"""Tests for live strategy hub attribution and material-change detection."""

from __future__ import annotations

import pytest

from common.events import EventBus
from common.types import Position
from engine.performance.performance_tracker import PerformanceTracker
from engine.performance.strategy_hub import _leg_unrealized_venue_aligned
from engine.performance.strategy_attribution import split_pnl_by_strategy
from engine.performance.strategy_hub import StrategyHubService
from engine.portfolio.portfolio import Portfolio
from engine.position.position_tracker import PositionTracker
from engine.position.strategy_ledger import StrategyPositionLedger
from engine.strategies.strategy_base import StrategyBase


class _StubStrategy(StrategyBase):
    name = "stub"
    display_label = "Stub"

    def symbols(self) -> list[str]:
        return ["BTCUSDT"]

    def on_tick(self, features):
        return []

    def analytics_snapshot(self):
        return {"STRATEGY": self.display_label, "SIGNAL": "HOLD"}


def test_split_pnl_by_strategy_netted_weights() -> None:
    split = split_pnl_by_strategy(
        -100.0,
        "__netted__",
        {"flow_momentum": 0.01, "sma_crossover": 0.005},
    )
    assert split["flow_momentum"] == pytest.approx(-66.666666, rel=1e-4)
    assert split["sma_crossover"] == pytest.approx(-33.333333, rel=1e-4)


def test_performance_tracker_attributes_netted_parent_close() -> None:
    bus = EventBus()
    portfolio = Portfolio(bus=bus, position_tracker=PositionTracker(bus))
    perf = PerformanceTracker(portfolio)

    from common.types import Fill
    from common.enums import Side
    from engine.performance.fill_classification import FillClassification

    fill = Fill(
        trade_id="t1",
        child_id="c1",
        parent_id="P-1",
        symbol="BTCUSDT",
        side=Side.SELL,
        qty=1.0,
        price=100.0,
        fee=0.0,
        fee_asset="USDT",
        ts=1.0,
    )
    classification = FillClassification(
        action="close",
        entry_price=110.0,
        exit_price=100.0,
        pnl=-30.0,
    )
    perf.record_fill(
        fill,
        classification,
        strategy_name="__netted__",
        strategy_contributions={"flow_momentum": 1.0, "sma_crossover": 1.0},
    )
    perf.finalize_parent_close("P-1")

    realized = perf.realized_pnl_by_strategy()
    assert realized["flow_momentum"] == pytest.approx(-15.0)
    assert realized["sma_crossover"] == pytest.approx(-15.0)


def test_strategy_hub_material_change_gate() -> None:
    bus = EventBus()
    tracker = PositionTracker(bus)
    portfolio = Portfolio(bus=bus, position_tracker=tracker)
    perf = PerformanceTracker(portfolio)
    ledger = StrategyPositionLedger()
    hub = StrategyHubService(ledger=ledger, positions=tracker, performance=perf)
    strat = _StubStrategy()

    _, first = hub.refresh(
        ts=1.0,
        strategies=[strat],
        multi_mode=False,
        analytics={"stub": {"SIGNAL": "HOLD"}},
    )
    assert first is True

    _, second = hub.refresh(
        ts=2.0,
        strategies=[strat],
        multi_mode=False,
        analytics={"stub": {"SIGNAL": "HOLD"}},
    )
    assert second is False

    _, third = hub.refresh(
        ts=3.0,
        strategies=[strat],
        multi_mode=False,
        analytics={"stub": {"SIGNAL": "LONG"}},
    )
    assert third is True


def test_leg_unrealized_prefers_venue_upnl_share() -> None:
    pos = Position(
        symbol="BTCUSDT",
        qty=1.0,
        avg_entry_price=100.0,
        mark_price=110.0,
        exchange_unrealized_pnl=10.0,
    )
    full = _leg_unrealized_venue_aligned(1.0, 100.0, pos)
    half = _leg_unrealized_venue_aligned(0.5, 100.0, pos)
    assert full == pytest.approx(10.0)
    assert half == pytest.approx(5.0)


def test_peek_snapshot_does_not_consume_material_change_gate() -> None:
    bus = EventBus()
    tracker = PositionTracker(bus)
    portfolio = Portfolio(bus=bus, position_tracker=tracker)
    perf = PerformanceTracker(portfolio)
    ledger = StrategyPositionLedger()
    hub = StrategyHubService(ledger=ledger, positions=tracker, performance=perf)
    strat = _StubStrategy()

    _, first = hub.refresh(
        ts=1.0,
        strategies=[strat],
        multi_mode=False,
        analytics={"stub": {"SIGNAL": "HOLD"}},
    )
    assert first is True

    hub.peek_snapshot(
        ts=2.0,
        strategies=[strat],
        multi_mode=False,
        analytics={"stub": {"SIGNAL": "LONG"}},
    )

    _, should_emit = hub.refresh(
        ts=3.0,
        strategies=[strat],
        multi_mode=False,
        analytics={"stub": {"SIGNAL": "LONG"}},
    )
    assert should_emit is True
