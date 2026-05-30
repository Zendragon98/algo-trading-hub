"""Per-symbol MM spread resolution."""

from common.config import Settings
from engine.market_data.feature_store import Features
from engine.strategies.mm_core import compute_half_spreads_bps, compute_quote_pricing
from engine.strategies.mm_symbol_params import required_min_spread_bps, resolve_mm_params


def test_per_symbol_half_spread_from_map() -> None:
    s = Settings(
        mm_quote_half_spread_bps=3.0,
        mm_symbol_half_spread_bps={"BTCUSDT": 2.0, "DOGEUSDT": 18.0},
        mm_quote_use_venue_spread_floor=False,
    )
    btc = resolve_mm_params("BTCUSDT", s)
    doge = resolve_mm_params("DOGEUSDT", s)
    assert btc.half_spread_bps == 2.0
    assert doge.half_spread_bps == 18.0


def test_csv_env_style_half_spread() -> None:
    s = Settings.model_validate(
        {"mm_symbol_half_spread_bps": "BTCUSDT:2.5,ETHUSDT:3.5"},
    )
    assert resolve_mm_params("ETHUSDT", s).half_spread_bps == 3.5


def test_venue_spread_floor_widens_illiquid_symbol() -> None:
    s = Settings(
        mm_quote_half_spread_bps=2.0,
        mm_quote_use_venue_spread_floor=True,
        mm_quote_venue_spread_mult=1.0,
    )
    feat = Features(symbol="DOGEUSDT", mid=0.1, spread_bps=40.0)
    p = resolve_mm_params("DOGEUSDT", s, feat)
    assert p.half_spread_bps >= 20.0
    assert p.venue_half_floor_bps >= 20.0


def test_required_min_spread_tracks_venue_floor() -> None:
    s = Settings(
        symbol_calibration_path="",
        mm_spread_calibration_path="",
        mm_quote_half_spread_bps=3.0,
        mm_quote_use_venue_spread_floor=True,
        mm_quote_venue_spread_mult=1.0,
        post_only_enabled=True,
        mm2_maker_fee_bps=2.0,
        mm2_spread_buffer_bps=2.0,
        mm2_assume_maker_rebate=False,
    )
    feat = Features(symbol="BTCUSDT", mid=100.0, spread_bps=14.0)
    required = required_min_spread_bps("BTCUSDT", s, feat)
    assert required == 14.0


def test_required_min_spread_calibration_respects_fee_floor() -> None:
    s = Settings(
        symbol_calibration_path="",
        mm_spread_calibration_path="",
        mm_quote_half_spread_bps=3.0,
        mm_quote_use_venue_spread_floor=False,
        post_only_enabled=True,
        mm2_maker_fee_bps=2.0,
        mm2_spread_buffer_bps=2.0,
        mm2_assume_maker_rebate=False,
        mm_symbol_quote_overrides={
            "BTCUSDT": {"min_spread_bps": 4.0},
        },
    )
    feat = Features(symbol="BTCUSDT", mid=100.0, spread_bps=20.0)
    required = required_min_spread_bps("BTCUSDT", s, feat)
    assert required == 6.0


def test_calibrated_spread_gate_ignores_fee_floor() -> None:
    s = Settings(
        symbol_calibration_path="",
        mm_spread_calibration_path="",
        post_only_enabled=True,
        mm2_maker_fee_bps=2.0,
        mm2_spread_buffer_bps=2.0,
        mm2_assume_maker_rebate=False,
        mm_symbol_quote_overrides={"BTCUSDT": {"min_spread_bps": 0.5}},
    )
    feat = Features(symbol="BTCUSDT", mid=100.0, spread_bps=2.4)
    required = required_min_spread_bps(
        "BTCUSDT",
        s,
        feat,
        calibrated_only=True,
    )
    assert required == 0.5


def test_required_min_spread_fee_floor_when_book_tighter_than_quotes() -> None:
    s = Settings(
        symbol_calibration_path="",
        mm_spread_calibration_path="",
        mm_quote_half_spread_bps=3.0,
        mm_quote_use_venue_spread_floor=True,
        post_only_enabled=False,
        mm2_taker_fee_bps=4.0,
        mm2_spread_buffer_bps=2.0,
        mm2_assume_maker_rebate=False,
    )
    feat = Features(symbol="BTCUSDT", mid=100.0, spread_bps=8.0)
    required = required_min_spread_bps("BTCUSDT", s, feat)
    assert required == 10.0


def test_half_spread_caps_to_tight_venue_book() -> None:
    s = Settings(
        mm_quote_half_spread_bps=5.0,
        mm_quote_use_venue_spread_floor=False,
    )
    feat = Features(symbol="SOLUSDT", mid=82.5, spread_bps=1.2)
    bid_half, ask_half = compute_half_spreads_bps(feat, s, inv_ratio=0.0)
    assert bid_half <= 1.2 * 0.48 + 1e-9
    assert ask_half <= 1.2 * 0.48 + 1e-9


def test_quote_pricing_uses_different_half_per_symbol() -> None:
    s = Settings(
        mm_quote_use_venue_spread_floor=False,
        mm_symbol_half_spread_bps={"BTCUSDT": 2.0, "DOGEUSDT": 20.0},
    )
    feat_btc = Features(symbol="BTCUSDT", mid=100.0, spread_bps=1.0)
    feat_doge = Features(symbol="DOGEUSDT", mid=0.1, spread_bps=30.0)
    btc = compute_quote_pricing(feat=feat_btc, settings=s, skew_avg=0.0, inv_ratio=0.0)
    doge = compute_quote_pricing(feat=feat_doge, settings=s, skew_avg=0.0, inv_ratio=0.0)
    assert doge.bid_half_bps > btc.bid_half_bps * 3
