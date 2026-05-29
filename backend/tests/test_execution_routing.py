"""MM strategies must not use VWAP path."""

from engine.strategies import mm_core


def test_mm_strategy_names() -> None:
    assert mm_core.is_mm_strategy("market_making_v2")
    assert not mm_core.is_mm_strategy("market_making")
    assert not mm_core.is_mm_strategy("pairs_trading")
