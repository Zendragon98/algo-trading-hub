"""Tests for tape-flow momentum strategy and PnL helpers."""

from __future__ import annotations

import logging
import time

import pytest

from common.config import Settings
from common.enums import Side
from engine.market_data.feature_store import Features
from engine.position.venue_pnl import compute_venue_pnl
from engine.strategies.flow_momentum import FlowMomentumStrategy
from engine.strategies.flow_pnl import maybe_log_pnl_verification
from engine.strategies.position_sync import VenuePosition

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
        "flow_trail_stop_bps": 0.0,
        "flow_max_hold_sec": 120.0,
        "flow_exit_tape_threshold": 0.05,
        "flow_qty": 0.01,
        "flow_skip_toxic": False,
        "flow_pnl_verify_log_interval_sec": 0.0,
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
    state.fill_vwap = 100.0
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
    state.fill_vwap = 100.0
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


def test_take_profit_uses_binance_unrealized_pnl() -> None:
    strat = FlowMomentumStrategy(
        _settings(flow_take_profit_bps=15.0, flow_trail_stop_bps=0.0)
    )
    strat.attach_position_provider(lambda _s: -3551.7)
    strat.attach_venue_position_provider(
        lambda _s: VenuePosition(
            qty=-3551.7,
            avg_entry_price=1.006,
            mark_price=1.0,
            exchange_unrealized_pnl=17.90,
        )
    )
    state = strat._state_for("FILUSDT", 3)
    state.open_side = -1
    state.fill_vwap = 1.006
    state.entry_ts = time.time() - 5.0

    feats = {"FILUSDT": _feat(tape=0.0, imb=0.0, mid=1.0)}
    strat._symbols = ["FILUSDT"]
    signals = list(strat.on_tick(feats))
    assert len(signals) == 1
    assert "flow_take_profit" in signals[0].reason
    assert "venue_upnl" in signals[0].reason or "fill_vwap" in signals[0].reason


def test_trailing_stop_captures_extension_then_exits_on_pullback() -> None:
    strat = FlowMomentumStrategy(
        _settings(
            flow_take_profit_bps=15.0,
            flow_trail_stop_bps=8.0,
            flow_trail_arm_bps=15.0,
        )
    )
    strat.attach_position_provider(lambda _s: 100.0)
    strat.attach_venue_position_provider(
        lambda _s: VenuePosition(
            qty=100.0,
            avg_entry_price=100.0,
            mark_price=100.50,
            exchange_unrealized_pnl=50.0,
        )
    )
    state = strat._state_for("BTCUSDT", 3)
    state.open_side = 1
    state.fill_vwap = 100.0
    state.entry_ts = time.time() - 5.0

    # Peak ~50 bps — trail armed, no exit yet
    feats = {"BTCUSDT": _feat(tape=0.0, imb=0.0, mid=100.50)}
    assert list(strat.on_tick(feats)) == []
    assert state.peak_pnl_bps >= 49.0

    # Pull back 8+ bps from peak → trail exit
    strat.attach_venue_position_provider(
        lambda _s: VenuePosition(
            qty=100.0,
            avg_entry_price=100.0,
            mark_price=100.41,
            exchange_unrealized_pnl=41.0,
        )
    )
    feats = {"BTCUSDT": _feat(tape=0.0, imb=0.0, mid=100.41)}
    signals = list(strat.on_tick(feats))
    assert len(signals) == 1
    assert "flow_trail_stop" in signals[0].reason


def test_on_fill_records_fill_vwap() -> None:
    strat = FlowMomentumStrategy(_settings())
    strat.attach_position_provider(lambda _s: 1.0)
    state = strat._state_for("BTCUSDT", 3)
    state.open_side = 1
    strat.on_fill("BTCUSDT", 1.0, "buy", price=100.5)
    assert state.fill_vwap == pytest.approx(100.5)
    strat.on_fill("BTCUSDT", 1.0, "buy", price=101.5)
    assert state.fill_vwap == pytest.approx(101.0)


def test_exit_signal_keeps_entry_until_flat() -> None:
    strat = FlowMomentumStrategy(_settings(flow_exit_tape_threshold=0.05))
    strat.attach_position_provider(lambda _s: 0.01)
    state = strat._state_for("BTCUSDT", 3)
    state.fill_vwap = 100.0
    state.entry_ts = time.time()
    state.open_side = 1

    feats = {"BTCUSDT": _feat(tape=-0.10, imb=-0.02, mid=100.0)}
    signals = list(strat.on_tick(feats))
    assert len(signals) == 1
    assert state.fill_vwap == 100.0
    assert state.entry_ts > 0


def test_pnl_verify_flags_drift(caplog: pytest.LogCaptureFixture) -> None:
    venue = VenuePosition(
        qty=-100.0,
        avg_entry_price=1.0,
        mark_price=0.995,
        exchange_unrealized_pnl=1.0,
    )
    snap = compute_venue_pnl(
        pos_side=-1,
        pos_qty=-100.0,
        mid=0.995,
        fill_vwap=0.0,
        venue=venue,
    )
    assert snap.internal_bps == pytest.approx(50.0, rel=1e-3)
    assert snap.venue_bps == pytest.approx(100.0, rel=1e-3)
    assert abs(snap.drift_bps or 0.0) > 1.0

    with caplog.at_level(logging.WARNING, logger="engine.position.venue_pnl"):
        maybe_log_pnl_verification(
            symbol="FILUSDT",
            snap=snap,
            pos_qty=-100.0,
            pos_side=-1,
            now=100.0,
            last_log_ts=0.0,
            log_interval_sec=1.0,
            max_drift_bps=1.0,
        )
    assert any("MISMATCH" in r.message for r in caplog.records)


def test_entry_source_hierarchy() -> None:
    venue = VenuePosition(qty=1.0, avg_entry_price=99.0, mark_price=100.0)
    snap = compute_venue_pnl(
        pos_side=1,
        pos_qty=1.0,
        mid=100.0,
        fill_vwap=98.0,
        venue=venue,
    )
    assert snap.entry_source == "fill_vwap"
    assert snap.entry_price == 98.0

    snap2 = compute_venue_pnl(
        pos_side=1,
        pos_qty=1.0,
        mid=100.0,
        fill_vwap=98.0,
        venue=None,
    )
    assert snap2.entry_source == "fill_vwap"
    assert snap2.entry_price == 98.0
