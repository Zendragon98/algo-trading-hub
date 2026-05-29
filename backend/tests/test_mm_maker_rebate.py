"""Maker-rebate fee model and touch quoting."""

from common.config import Settings
from engine.market_data.feature_store import Features
from engine.market_data.own_quote_book import OwnBookState
from engine.strategies import mm_core
from engine.strategies.market_making_v2 import MarketMakingV2Strategy
from engine.strategies.mm_calibrated import (
    mm2_fee_edge_floor_bps,
    mm2_fee_round_trip_bps,
    mm2_spread_gate_fee_rt_bps,
)


def test_assume_maker_rebate_zeros_spread_gate_fee() -> None:
    s = Settings(
        symbol_calibration_path="",
        mm_spread_calibration_path="",
        mm2_maker_fee_bps=2.0,
        mm2_spread_buffer_bps=2.0,
        mm2_assume_maker_rebate=True,
        post_only_enabled=True,
    )
    assert mm2_spread_gate_fee_rt_bps("BTCUSDT", s) == 0.0
    assert mm2_fee_edge_floor_bps("BTCUSDT", s) == 2.0
    assert mm2_fee_round_trip_bps("BTCUSDT", s) == 4.0


def test_negative_maker_fee_lowers_edge_floor() -> None:
    s = Settings(
        symbol_calibration_path="",
        mm_spread_calibration_path="",
        mm2_maker_fee_bps=-2.0,
        mm2_spread_buffer_bps=2.0,
        mm2_assume_maker_rebate=False,
        post_only_enabled=True,
    )
    assert mm2_fee_round_trip_bps("BTCUSDT", s) == -4.0
    assert mm2_fee_edge_floor_bps("BTCUSDT", s) == 0.0


def test_mm2_fee_floor_gate_allows_tight_spread_with_rebate() -> None:
    strat = MarketMakingV2Strategy(
        Settings(
            binance_api_key="x",
            binance_api_secret="y",
            symbol_calibration_path="",
            mm_spread_calibration_path="",
            mm2_symbols=["BTCUSDT"],
            mm2_spread_gate_mode="fee_floor",
            mm2_assume_maker_rebate=True,
            mm2_spread_buffer_bps=0.0,
            mm2_min_spread_bps=0.0,
            mm2_min_samples=1,
            mm2_min_skew_bps=0.0,
            post_only_enabled=True,
        )
    )
    strat.attach_own_book_provider(lambda _s: OwnBookState(symbol="BTCUSDT"))
    feat = {
        "BTCUSDT": Features(
            symbol="BTCUSDT",
            mid=100.0,
            spread_bps=0.5,
            micro_price=100.0,
        )
    }
    assert strat.on_tick_quotes(feat)[0].reason != "mm2_spread_gate"


def test_quote_inside_touch_pegs_one_tick_inside_spread() -> None:
    feat = Features(
        symbol="BTCUSDT",
        mid=100.0,
        best_bid=99.8,
        best_ask=100.2,
        spread_bps=4.0,
        jump_active=False,
        is_toxic=False,
        bid_depth_ratio=1.0,
        ask_depth_ratio=1.0,
    )
    own = OwnBookState(symbol="BTCUSDT")
    s = Settings(
        symbol_calibration_path="",
        mm_spread_calibration_path="",
        mm_quote_at_touch=False,
        mm_quote_inside_touch_ticks=1,
        mm_quote_half_spread_bps=5.0,
        mm_quote_use_venue_spread_floor=False,
    )
    intent = mm_core.compute_quote_intent(
        feat=feat,
        settings=s,
        own=own,
        position_qty=0.0,
        equity=10_000.0,
        skew_avg=0.0,
        strategy_name="market_making",
    )
    assert intent.bid_price == 99.81
    assert intent.ask_price == 100.19


def test_quote_at_touch_pegs_to_best_bid_ask() -> None:
    feat = Features(
        symbol="BTCUSDT",
        mid=100.0,
        best_bid=99.98,
        best_ask=100.02,
        spread_bps=4.0,
        jump_active=False,
        is_toxic=False,
        bid_depth_ratio=1.0,
        ask_depth_ratio=1.0,
    )
    own = OwnBookState(symbol="BTCUSDT")
    s = Settings(
        symbol_calibration_path="",
        mm_spread_calibration_path="",
        mm_quote_at_touch=True,
        mm_quote_half_spread_bps=5.0,
    )
    intent = mm_core.compute_quote_intent(
        feat=feat,
        settings=s,
        own=own,
        position_qty=0.0,
        equity=10_000.0,
        skew_avg=0.0,
        strategy_name="market_making",
    )
    assert intent.bid_price == 99.98
    assert intent.ask_price == 100.02
