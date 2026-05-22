"""Per-symbol analytics calibration consumed by live MM paths."""

from __future__ import annotations

import json
import os

os.environ.setdefault("BINANCE_API_KEY", "test")
os.environ.setdefault("BINANCE_API_SECRET", "test")

from common.config import Settings  # noqa: E402
from engine.market_data.feature_store import Features  # noqa: E402
from engine.market_data.mid_tracker import MidReturnTracker  # noqa: E402
from engine.market_data.own_quote_book import OwnBookState  # noqa: E402
from engine.market_data.symbol_calibration import invalidate_cache  # noqa: E402
from engine.risk.mm_flow_guard import MmFlowGuard  # noqa: E402
from engine.strategies.market_making_v2 import MarketMakingV2Strategy  # noqa: E402
from engine.strategies.mm_calibrated import mm2_fee_round_trip_bps, mm2_spread_buffer_bps  # noqa: E402


def _write_cal(path, symbols: dict) -> None:
    path.write_text(
        json.dumps({"symbols": symbols}),
        encoding="utf-8",
    )
    invalidate_cache()


def test_mid_tracker_uses_calibrated_jump_threshold(tmp_path) -> None:
    cal = tmp_path / "symbol_calibration.json"
    _write_cal(
        cal,
        {
            "BTCUSDT": {
                "mm": {"jump_return_bps": 50.0, "jump_vol_mult": 0.0},
            },
        },
    )
    s = Settings(
        binance_api_key="x",
        binance_api_secret="y",
        symbol_calibration_path=str(cal),
        mm_jump_return_bps=10.0,
        mm_jump_pause_sec=30.0,
    )
    tr = MidReturnTracker(s)
    tr.on_mid("BTCUSDT", 100.0, 0.0)
    tr.on_mid("BTCUSDT", 100.15, 1.0)
    assert tr.stats("BTCUSDT", now=2.0).jump_active is False
    tr.on_mid("BTCUSDT", 100.70, 2.0)
    assert tr.stats("BTCUSDT", now=3.0).jump_active is True


def test_mm2_spread_gate_uses_calibrated_fees(tmp_path) -> None:
    cal = tmp_path / "symbol_calibration.json"
    _write_cal(
        cal,
        {
            "BTCUSDT": {
                "mm": {"min_spread_bps": 0.0},
                "fees": {
                    "maker_fee_bps": 1.0,
                    "taker_fee_bps": 2.0,
                    "spread_buffer_bps": 1.0,
                },
            },
        },
    )
    strat = MarketMakingV2Strategy(
        Settings(
            binance_api_key="x",
            binance_api_secret="y",
            symbol_calibration_path=str(cal),
            mm2_symbols=["BTCUSDT"],
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
            spread_bps=3.0,
            micro_price=100.0,
        )
    }
    intents = strat.on_tick_quotes(feat)
    assert intents[0].reason != "mm2_spread_gate"
    assert mm2_fee_round_trip_bps("BTCUSDT", strat._settings) == 2.0
    assert mm2_spread_buffer_bps("BTCUSDT", strat._settings) == 1.0


def test_mm_flow_guard_uses_calibrated_depletion_ratio(tmp_path) -> None:
    cal = tmp_path / "symbol_calibration.json"
    _write_cal(
        cal,
        {
            "ETHUSDT": {
                "mm": {"depletion_breaker_ratio": 0.05},
            },
        },
    )
    s = Settings(
        binance_api_key="x",
        binance_api_secret="y",
        symbol_calibration_path=str(cal),
        mm_depletion_breaker_ratio=0.25,
    )
    guard = MmFlowGuard(s)
    feat = Features(
        symbol="ETHUSDT",
        mid=100.0,
        bid_depth_ratio=0.10,
        ask_depth_ratio=1.0,
    )
    assert guard.evaluate_entry(feat, reduce_only=False) is None
    feat2 = Features(
        symbol="ETHUSDT",
        mid=100.0,
        bid_depth_ratio=0.02,
        ask_depth_ratio=1.0,
    )
    breach = guard.evaluate_entry(feat2, reduce_only=False)
    assert breach is not None
    assert breach.code == "book_depleted"
