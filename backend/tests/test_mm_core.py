"""mm_core inventory, reservation mid, and quote intent."""

import time

import pytest

from common.config import Settings
from engine.market_data.feature_store import Features
from engine.market_data.own_quote_book import EntryLedger, OwnBookState
from engine.strategies import mm_core


def test_entry_blocked_vol_regime() -> None:
    feat = Features(
        symbol="BTCUSDT",
        mid=100.0,
        jump_active=False,
        is_toxic=False,
    )
    own = OwnBookState(symbol="BTCUSDT")
    own.vol_regime_halt_until = time.time() + 60.0
    s = Settings()
    assert mm_core.entry_blocked(feat, s, want_long=True, own=own, now=time.time()) == "vol_regime"
    assert mm_core.entry_blocked(feat, s, want_long=False, own=own, now=time.time()) == "vol_regime"


def test_inventory_blocks_long_when_full() -> None:
    feat = Features(
        symbol="BTCUSDT",
        mid=100.0,
        inventory_ratio=0.9,
        jump_active=False,
        is_toxic=False,
    )
    s = Settings(mm_inventory_hard_ratio=0.85)
    assert mm_core.entry_blocked(feat, s, want_long=True) == "inventory"


def test_compute_quote_intent_caps_reduce_only_side_to_position() -> None:
    feat = Features(
        symbol="FILUSDT",
        mid=0.95,
        spread_bps=10.0,
        imbalance_topn=0.0,
        jump_active=False,
        is_toxic=False,
        bid_depth_ratio=1.0,
        ask_depth_ratio=1.0,
        best_bid=0.949,
        best_ask=0.951,
    )
    own = OwnBookState(symbol="FILUSDT")
    s = Settings(mm_quote_size_pct=0.5, mm_max_inventory_notional=100.0)
    intent = mm_core.compute_quote_intent(
        feat=feat,
        settings=s,
        own=own,
        position_qty=-0.031,
        equity=10_000.0,
        skew_avg=0.0,
        strategy_name="market_making_v2",
    )
    assert intent.reduce_only_bid is True
    assert intent.bid_qty == pytest.approx(0.031, rel=1e-6)
    assert intent.bid_qty <= abs(-0.031) + 1e-12


def test_clamp_targets_no_cross_in_intent() -> None:
    feat = Features(
        symbol="BTCUSDT",
        mid=100.0,
        spread_bps=10.0,
        best_bid=99.5,
        best_ask=100.5,
        jump_active=False,
        is_toxic=False,
        bid_depth_ratio=1.0,
        ask_depth_ratio=1.0,
    )
    own = OwnBookState(symbol="BTCUSDT")
    s = Settings(mm_inventory_hard_ratio=0.0)
    intent = mm_core.compute_quote_intent(
        feat=feat,
        settings=s,
        own=own,
        position_qty=0.0,
        equity=10_000.0,
        skew_avg=0.0,
        strategy_name="market_making_v2",
    )
    if intent.bid_price is not None:
        assert intent.bid_price < feat.best_ask


def test_position_limit_blocks_bid() -> None:
    feat = Features(
        symbol="BTCUSDT",
        mid=100.0,
        spread_bps=5.0,
        jump_active=False,
        is_toxic=False,
        bid_depth_ratio=1.0,
        ask_depth_ratio=1.0,
    )
    own = OwnBookState(symbol="BTCUSDT")
    s = Settings(
        mm_buy_position_limit_notional=500.0,
        mm_inventory_hard_ratio=0.0,
    )
    intent = mm_core.compute_quote_intent(
        feat=feat,
        settings=s,
        own=own,
        position_qty=6.0,
        equity=10_000.0,
        skew_avg=0.0,
        strategy_name="market_making_v2",
    )
    assert intent.bid_price is None


def test_compute_quote_intent_has_two_sides() -> None:
    feat = Features(
        symbol="BTCUSDT",
        mid=100.0,
        spread_bps=5.0,
        imbalance_topn=0.0,
        jump_active=False,
        is_toxic=False,
        bid_depth_ratio=1.0,
        ask_depth_ratio=1.0,
    )
    own = OwnBookState(symbol="BTCUSDT")
    s = Settings()
    intent = mm_core.compute_quote_intent(
        feat=feat,
        settings=s,
        own=own,
        position_qty=0.0,
        equity=10_000.0,
        skew_avg=0.0,
        strategy_name="market_making_v2",
    )
    assert intent.bid_price is not None
    assert intent.ask_price is not None
    assert intent.ask_price > intent.bid_price
    assert intent.reservation_mid == intent.venue_mid


def test_long_inventory_lowers_reservation_and_skews_spread() -> None:
    feat = Features(
        symbol="BTCUSDT",
        mid=100.0,
        jump_active=False,
        is_toxic=False,
        bid_depth_ratio=1.0,
        ask_depth_ratio=1.0,
    )
    own = OwnBookState(symbol="BTCUSDT")
    s = Settings(
        mm_max_inventory_notional=100.0,
        mm_reservation_inventory_bps=20.0,
        mm_inventory_spread_skew_bps=8.0,
        mm_quote_half_spread_bps=4.0,
    )
    intent = mm_core.compute_quote_intent(
        feat=feat,
        settings=s,
        own=own,
        position_qty=1.0,
        equity=10_000.0,
        skew_avg=0.0,
        strategy_name="market_making_v2",
    )
    assert intent.inventory_ratio > 0.9
    assert intent.reservation_mid < intent.venue_mid
    assert intent.bid_half_bps > intent.ask_half_bps


def test_exit_limit_price_ramps_toward_touch_when_long_losing() -> None:
    feat = Features(
        symbol="BTCUSDT",
        mid=100.0,
        best_bid=99.95,
        best_ask=100.05,
    )
    mild = mm_core.exit_limit_price(
        feat,
        position_qty=1.0,
        scratch_bps=5.0,
        aggressive_bps=35.0,
        pnl_bps=-5.0,
        ramp_bps=20.0,
        cross_touch=True,
    )
    deep = mm_core.exit_limit_price(
        feat,
        position_qty=1.0,
        scratch_bps=5.0,
        aggressive_bps=35.0,
        pnl_bps=-25.0,
        ramp_bps=20.0,
        cross_touch=True,
    )
    assert mild > deep
    assert deep < 99.95


def test_plan_exit_reason_adverse_fill_before_profit() -> None:
    feat = Features(symbol="BTCUSDT", mid=100.0, inventory_ratio=0.0)
    own = OwnBookState(symbol="BTCUSDT")
    own.ledger = EntryLedger(entry_mid=99.5, opened_ts=time.time() - 5.0)
    own.last_fill_adverse_bps = 5.0
    s = Settings(
        symbol_calibration_path="",
        mm_scratch_loss_bps=3.0,
        mm_min_exit_profit_bps=100.0,
        mm_market_exit_loss_bps=50.0,
    )
    reason = mm_core.plan_exit_reason(
        feat=feat,
        settings=s,
        own=own,
        position_qty=1.0,
        mid=100.0,
    )
    assert reason is not None
    assert reason.startswith("mm_aggressive_exit adverse_fill")


def test_plan_exit_reason_jump_before_market_loss() -> None:
    feat = Features(
        symbol="BTCUSDT",
        mid=100.0,
        inventory_ratio=0.0,
        jump_active=True,
    )
    own = OwnBookState(symbol="BTCUSDT")
    own.ledger = EntryLedger(entry_mid=100.5, opened_ts=time.time() - 5.0)
    s = Settings(mm_jump_flatten=True, mm_market_exit_loss_bps=1.0)
    reason = mm_core.plan_exit_reason(
        feat=feat,
        settings=s,
        own=own,
        position_qty=1.0,
        mid=100.0,
    )
    assert reason == "mm_market_exit jump_flatten"


def test_entry_risk_moderate_widens_half_spread() -> None:
    feat = Features(
        symbol="BTCUSDT",
        mid=100.0,
        spread_bps=20.0,
        toxicity_score=0.5,
        markout_adverse_ewma_bps=0.0,
        jump_active=False,
        is_toxic=False,
        bid_depth_ratio=1.0,
        ask_depth_ratio=1.0,
    )
    own = OwnBookState(symbol="BTCUSDT")
    s = Settings(
        mm2_toxicity_moderate=0.45,
        mm_toxicity_threshold=0.65,
        mm2_risk_widen_multiplier=2.0,
        mm2_risk_size_damp=0.5,
        mm_quote_use_venue_spread_floor=False,
    )
    assert mm_core.entry_risk_tier(feat, s, own=own, now=time.time()) == "moderate"
    intent = mm_core.compute_quote_intent(
        feat=feat,
        settings=s,
        own=own,
        position_qty=0.0,
        equity=10_000.0,
        skew_avg=0.0,
        strategy_name="market_making_v2",
    )
    assert intent.bid_price is not None
    assert "risk=moderate" in intent.reason
    assert intent.bid_half_bps > 3.0


def test_on_mm_fill_skips_hedge_when_taker() -> None:
    from engine.market_data.feature_store import Features
    from engine.market_data.own_quote_book import OwnBookState

    feat = Features(
        symbol="BTCUSDT",
        mid=100.0,
        vol_5m_bps=100.0,
        jump_active=False,
    )
    own = OwnBookState(symbol="BTCUSDT")
    own.last_fill_adverse_bps = 10.0
    s = Settings(mm_fill_hedge_ioc_enabled=True, mm_fill_hedge_adverse_bps=1.0)
    mm_core.on_mm_fill(own, feat, s, side="buy", maker=False)
    assert own.pending_take_ask is False
    assert own.pending_take_bid is False


def test_on_mm_fill_queues_hedge_when_maker() -> None:
    from engine.market_data.feature_store import Features
    from engine.market_data.own_quote_book import OwnBookState

    feat = Features(
        symbol="BTCUSDT",
        mid=100.0,
        vol_5m_bps=100.0,
        jump_active=False,
    )
    own = OwnBookState(symbol="BTCUSDT")
    own.last_fill_adverse_bps = 10.0
    s = Settings(mm_fill_hedge_ioc_enabled=True, mm_fill_hedge_adverse_bps=1.0)
    mm_core.on_mm_fill(own, feat, s, side="buy", maker=True)
    assert own.pending_take_ask is True


def test_plan_exit_reason_market_when_deep_loss() -> None:
    feat = Features(symbol="BTCUSDT", mid=100.0, inventory_ratio=0.2)
    own = OwnBookState(symbol="BTCUSDT")
    own.ledger = EntryLedger(entry_mid=100.2, opened_ts=time.time() - 10.0)
    s = Settings(mm_market_exit_loss_bps=10.0, mm_max_hold_sec=150.0)
    reason = mm_core.plan_exit_reason(
        feat=feat,
        settings=s,
        own=own,
        position_qty=1.0,
        mid=100.0,
    )
    assert reason is not None
    assert reason.startswith("mm_market_exit")
