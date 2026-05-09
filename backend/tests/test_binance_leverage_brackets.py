"""Unit tests for Binance leverage bracket parsing."""

from gateways.binance.binance_gateway import _max_initial_leverage_from_brackets


def test_max_initial_leverage_first_bracket() -> None:
    brackets = [
        {"bracket": 1, "initialLeverage": 50, "notionalCap": 50000},
        {"bracket": 2, "initialLeverage": 25, "notionalCap": 250000},
    ]
    assert _max_initial_leverage_from_brackets(brackets) == 50


def test_max_initial_leverage_empty() -> None:
    assert _max_initial_leverage_from_brackets([]) is None


def test_max_initial_leverage_missing_field() -> None:
    assert _max_initial_leverage_from_brackets([{"bracket": 1}]) is None
