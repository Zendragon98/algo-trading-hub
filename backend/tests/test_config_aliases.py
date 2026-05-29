"""Strategy name aliases and calibration path behaviour."""

from common.config import Settings, normalize_strategy_name


def test_mm_aliases_resolve_to_v2() -> None:
    assert normalize_strategy_name("mm") == "market_making_v2"
    assert normalize_strategy_name("market_making") == "market_making_v2"
    assert normalize_strategy_name("mm2") == "market_making_v2"


def test_exit_scratch_uses_settings_without_symbol_calibration_path() -> None:
    from engine.market_data.feature_store import Features
    from engine.market_data.own_quote_book import OwnBookState
    from engine.strategies import mm_core

    feat = Features(symbol="BTCUSDT", mid=100.0, jump_active=False)
    own = OwnBookState(symbol="BTCUSDT")
    own.last_fill_adverse_bps = 4.0
    s = Settings(
        symbol_calibration_path="",
        mm_spread_calibration_path="",
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
