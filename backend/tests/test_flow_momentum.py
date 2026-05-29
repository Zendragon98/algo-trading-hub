"""Tests for tape-flow momentum strategy."""

from __future__ import annotations

import time

import pytest

from common.config import Settings
from common.enums import Side
from engine.market_data.feature_store import Features
from engine.strategies.flow_momentum import FlowMomentumStrategy

pytestmark = pytest.mark.filterwarnings("ignore")


def _settings(**overrides: object) -> Settings:
    base = {
        "flow_symbols": ["BTCUSDT"],
        "flow_tape_threshold": 0.10,
        "flow_imbalance_min": 0.05,
        "flow_confirm_ticks": 3,
        "flow_cooldown_sec": 0.0,
        "flow_stop_loss_bps": 10.0,
        "flow_take_profit_bps": 20.0,
        "flow_max_hold_sec": 120.0,
        "flow_exit_tape_threshold": 0.05,
        "flow_qty": 0.01,
        "flow_skip_toxic": False,
    }
    base.update(overrides)
    return Settings.model_validate(base)


def _feat(
    *,
    tape: float = 0.0,
    imb: float = 0.0,
    mid: float = 100.0,
) -> Features:
    ask = 0.5 + tape / 2.0
    bid = 1.0 - ask
    return Features(
        symbol="BTCUSDT",
        mid=mid,
        imbalance_topn=imb,
        bid_hit_ratio=bid,
        ask_hit_ratio=ask,
        tape_bid_hit_count=50,
        tape_ask_hit_count=50,
    )


def test_no_entry_below_tape_threshold() -> None:
    strat = FlowMomentumStrategy(_settings())
    feats = {"BTCUSDT": _feat(tape=0.05, imb=0.10)}
    for _ in range(5):
        assert list(strat.on_tick(feats)) == []


def test_long_entry_after_confirm_ticks() -> None:
    strat = FlowMomentumStrategy(_settings())
    strat.attach_position_provider(lambda _s: 0.0)
    feats = {"BTCUSDT": _feat(tape=0.20, imb=0.12)}
    signals = []
    for _ in range(3):
        signals = list(strat.on_tick(feats))
    assert len(signals) == 1
    assert signals[0].side is Side.BUY
    assert not signals[0].reduce_only
    assert "flow_momentum_enter" in signals[0].reason


def test_stop_loss_exit() -> None:
    strat = FlowMomentumStrategy(_settings(flow_stop_loss_bps=5.0))
    state = strat._state_for("BTCUSDT", 3)
    state.entry_mid = 100.0
    state.entry_ts = time.time() - 1.0
    state.open_side = 1
    strat.attach_position_provider(lambda _s: 0.01)

    feats = {"BTCUSDT": _feat(tape=0.20, imb=0.12, mid=99.94)}
    signals = list(strat.on_tick(feats))
    assert len(signals) == 1
    assert signals[0].reduce_only
    assert signals[0].side is Side.SELL
    assert "flow_stop_loss" in signals[0].reason


def test_flow_reversal_exit_on_short() -> None:
    strat = FlowMomentumStrategy(_settings())
    strat.attach_position_provider(lambda _s: -0.01)
    state = strat._state_for("BTCUSDT", 3)
    state.entry_mid = 100.0
    state.entry_ts = time.time()
    state.open_side = -1

    feats = {"BTCUSDT": _feat(tape=0.10, imb=-0.02, mid=100.0)}
    signals = list(strat.on_tick(feats))
    assert len(signals) == 1
    assert signals[0].reduce_only
    assert "flow_reversal" in signals[0].reason


def test_manages_own_risk() -> None:
    strat = FlowMomentumStrategy(_settings())
    assert strat.manages_own_risk() is True
