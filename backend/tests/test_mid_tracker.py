"""MidReturnTracker jump detection."""

from common.config import Settings
from engine.market_data.mid_tracker import MidReturnTracker


def test_jump_latches_pause() -> None:
    s = Settings(
        binance_api_key="x",
        binance_api_secret="y",
        symbol_calibration_path="",
        mm_jump_return_bps=10.0,
        mm_jump_pause_sec=30.0,
    )
    tr = MidReturnTracker(s)
    tr.on_mid("BTCUSDT", 100.0, 0.0)
    tr.on_mid("BTCUSDT", 100.2, 1.0)
    st = tr.stats("BTCUSDT", now=2.0)
    assert st.jump_active is True


def test_tiny_tick_does_not_trigger_vol_relative_jump() -> None:
    s = Settings(
        binance_api_key="x",
        binance_api_secret="y",
        symbol_calibration_path="",
        mm_jump_return_bps=25.0,
        mm_jump_vol_mult=3.0,
        mm_jump_pause_sec=30.0,
    )
    tr = MidReturnTracker(s)
    tr.on_mid("BTCUSDT", 100.0, 0.0)
    tr.on_mid("BTCUSDT", 100.001, 1.0)
    st = tr.stats("BTCUSDT", now=2.0)
    assert st.jump_active is False
