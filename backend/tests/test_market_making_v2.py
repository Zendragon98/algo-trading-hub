from __future__ import annotations

import os

os.environ.setdefault("BINANCE_API_KEY", "test")
os.environ.setdefault("BINANCE_API_SECRET", "test")

from common.config import Settings  # noqa: E402
from engine.market_data.feature_store import Features  # noqa: E402
from engine.market_data.own_quote_book import OwnBookState  # noqa: E402
from engine.strategies.market_making import MarketMakingV2Strategy  # noqa: E402


def _gate_test_settings(**overrides: object) -> Settings:
    """Isolate gate tests from repo .env (two-sided / spread-gate overrides)."""
    base: dict[str, object] = {
        "binance_api_key": "x",
        "binance_api_secret": "y",
        "symbol_calibration_path": "",
        "mm_spread_calibration_path": "",
        "mm2_two_sided_always": False,
        "mm2_spread_gate_mode": "standard",
        "mm2_tape_confirm": 0.0,
        "mm2_assume_maker_rebate": True,
        "mm2_spread_buffer_bps": 0.0,
    }
    base.update(overrides)
    return Settings(**base)


def test_mm2_spread_gate_pulls_quotes() -> None:
    strat = MarketMakingV2Strategy(
        _gate_test_settings(
            mm2_symbols=["BTCUSDT"],
            mm2_min_spread_bps=50.0,
            mm2_min_samples=1,
        )
    )
    strat.attach_own_book_provider(lambda _s: OwnBookState(symbol="BTCUSDT"))
    feat = {
        "BTCUSDT": Features(
            symbol="BTCUSDT",
            mid=100.0,
            spread_bps=2.0,
            micro_price=100.0,
        )
    }
    intents = strat.on_tick_quotes(feat)
    assert intents[0].bid_price is None
    assert intents[0].ask_price is None
    assert intents[0].reason == "mm2_spread_gate"


def test_mm2_spread_gate_uses_dynamic_quote_width() -> None:
    strat = MarketMakingV2Strategy(
        _gate_test_settings(
            mm2_symbols=["BTCUSDT"],
            mm2_min_spread_bps=0.0,
            mm2_min_skew_bps=0.0,
            mm2_min_samples=1,
            mm2_assume_maker_rebate=False,
            mm2_spread_buffer_bps=2.0,
            post_only_enabled=True,
            mm_quote_use_venue_spread_floor=True,
        )
    )
    strat.attach_own_book_provider(lambda _s: OwnBookState(symbol="BTCUSDT"))
    wide = {
        "BTCUSDT": Features(
            symbol="BTCUSDT",
            mid=100.0,
            spread_bps=14.0,
            micro_price=100.0,
        )
    }
    tight = {
        "BTCUSDT": Features(
            symbol="BTCUSDT",
            mid=100.0,
            spread_bps=4.0,
            micro_price=100.0,
        )
    }
    assert strat.on_tick_quotes(wide)[0].reason != "mm2_spread_gate"
    gated = strat.on_tick_quotes(tight)[0]
    assert gated.reason == "mm2_spread_gate"
    assert gated.venue_mid == 100.0


def test_mm2_gate_summary_logs(caplog) -> None:
    import logging

    caplog.set_level(logging.INFO)
    strat = MarketMakingV2Strategy(
        _gate_test_settings(
            mm2_symbols=["BTCUSDT"],
            mm2_min_spread_bps=50.0,
            mm2_scan_log_interval_sec=-1.0,
        )
    )
    strat.attach_own_book_provider(lambda _s: OwnBookState(symbol="BTCUSDT"))
    feat = {
        "BTCUSDT": Features(
            symbol="BTCUSDT",
            mid=100.0,
            spread_bps=2.0,
            micro_price=100.0,
        )
    }
    strat.on_tick_quotes(feat)
    assert any("MM2 gates BTCUSDT" in r.message for r in caplog.records)


def test_mm2_quote_during_warmup_uses_tape_path() -> None:
    strat = MarketMakingV2Strategy(
        Settings(
            binance_api_key="x",
            binance_api_secret="y",
            mm2_symbols=["BTCUSDT"],
            mm2_min_spread_bps=0.0,
            mm2_min_skew_bps=0.0,
            mm2_min_samples=50,
            mm2_quote_during_warmup=True,
            mm2_tape_confirm=0.0,
        )
    )
    strat.attach_own_book_provider(lambda _s: OwnBookState(symbol="BTCUSDT"))
    feat = {
        "BTCUSDT": Features(
            symbol="BTCUSDT",
            mid=100.0,
            spread_bps=20.0,
            micro_price=100.05,
            bid_hit_ratio=0.5,
            ask_hit_ratio=0.5,
        )
    }
    intents = strat.on_tick_quotes(feat)
    assert intents[0].reason != "mm2_skew_warmup"


def test_mm2_skew_warmup_suppresses_quotes() -> None:
    strat = MarketMakingV2Strategy(
        _gate_test_settings(
            mm2_symbols=["BTCUSDT"],
            mm2_min_spread_bps=0.0,
            mm2_min_samples=50,
            mm2_quote_during_warmup=False,
        )
    )
    strat.attach_own_book_provider(lambda _s: OwnBookState(symbol="BTCUSDT"))
    feat = {
        "BTCUSDT": Features(
            symbol="BTCUSDT",
            mid=100.0,
            spread_bps=20.0,
            micro_price=100.05,
        )
    }
    intents = strat.on_tick_quotes(feat)
    assert intents[0].reason == "mm2_skew_warmup"
    assert intents[0].bid_qty == 0.0
    assert intents[0].ask_qty == 0.0


def test_mm2_two_sided_always_quotes_despite_low_skew() -> None:
    strat = MarketMakingV2Strategy(
        Settings(
            binance_api_key="x",
            binance_api_secret="y",
            symbol_calibration_path="",
            mm_spread_calibration_path="",
            mm2_symbols=["BTCUSDT"],
            mm2_min_spread_bps=0.0,
            mm2_min_skew_bps=5.0,
            mm2_min_samples=1,
            mm2_two_sided_always=True,
            mm2_spread_gate_mode="off",
            mm_quote_use_venue_spread_floor=False,
        )
    )
    strat.attach_own_book_provider(lambda _s: OwnBookState(symbol="BTCUSDT"))
    feat = {
        "BTCUSDT": Features(
            symbol="BTCUSDT",
            mid=100.0,
            spread_bps=20.0,
            micro_price=100.0,
            best_bid=99.9,
            best_ask=100.1,
            bid_depth_ratio=1.0,
            ask_depth_ratio=1.0,
            jump_active=False,
            is_toxic=False,
        )
    }
    intent = strat.on_tick_quotes(feat)[0]
    assert intent.reason != "mm2_skew_gate"
    assert intent.bid_price is not None
    assert intent.ask_price is not None


def test_mm2_calibrated_spread_gate_uses_tier_floor() -> None:
    strat = MarketMakingV2Strategy(
        _gate_test_settings(
            mm2_symbols=["BTCUSDT"],
            mm2_spread_gate_mode="calibrated",
            mm2_min_spread_bps=0.0,
            mm2_min_samples=1,
            mm_quote_use_venue_spread_floor=False,
        )
    )
    strat.attach_own_book_provider(lambda _s: OwnBookState(symbol="BTCUSDT"))
    ok = {
        "BTCUSDT": Features(
            symbol="BTCUSDT",
            mid=100.0,
            spread_bps=0.6,
            micro_price=100.0,
        )
    }
    tight = {
        "BTCUSDT": Features(
            symbol="BTCUSDT",
            mid=100.0,
            spread_bps=0.3,
            micro_price=100.0,
        )
    }
    assert strat.on_tick_quotes(ok)[0].reason != "mm2_spread_gate"
    assert strat.on_tick_quotes(tight)[0].reason == "mm2_spread_gate"


def test_mm2_skew_gate_pulls_quotes() -> None:
    strat = MarketMakingV2Strategy(
        _gate_test_settings(
            mm2_symbols=["BTCUSDT"],
            mm2_min_spread_bps=0.0,
            mm2_min_skew_bps=5.0,
            mm2_min_samples=1,
            mm2_skew_window_sec=300.0,
        )
    )
    strat.attach_own_book_provider(lambda _s: OwnBookState(symbol="BTCUSDT"))
    feat = {
        "BTCUSDT": Features(
            symbol="BTCUSDT",
            mid=100.0,
            spread_bps=20.0,
            micro_price=100.0,
        )
    }
    intents = strat.on_tick_quotes(feat)
    assert len(intents) == 1
    assert intents[0].bid_price is None
    assert intents[0].ask_price is None
    assert intents[0].reason == "mm2_skew_gate"
