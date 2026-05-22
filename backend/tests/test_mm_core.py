"""mm_core inventory, reservation mid, and quote intent."""

from common.config import Settings
from engine.market_data.feature_store import Features
from engine.market_data.own_quote_book import OwnBookState
from engine.strategies import mm_core


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
