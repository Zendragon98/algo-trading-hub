"""mm_core inventory, reservation mid, and quote intent."""

import time

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
        strategy_name="market_making",
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
        strategy_name="market_making",
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
        mm_scratch_loss_bps=3.0,
        mm_min_exit_profit_bps=1.0,
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
