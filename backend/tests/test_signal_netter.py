"""Tests for cross-strategy signal netting."""

from __future__ import annotations

import os

os.environ.setdefault("BINANCE_API_KEY", "test")
os.environ.setdefault("BINANCE_API_SECRET", "test")

from common.enums import Side  # noqa: E402
from common.types import Signal  # noqa: E402
from engine.strategies.signal_netter import net_strategy_signals  # noqa: E402


def _sig(symbol: str, side: Side, qty: float, **kw) -> Signal:
    return Signal(symbol=symbol, side=side, qty=qty, reason="test", **kw)


def test_nets_opposing_single_leg_signals() -> None:
    tagged = [
        ("sma_crossover", _sig("BTCUSDT", Side.BUY, 1.0)),
        ("market_making", _sig("BTCUSDT", Side.SELL, 0.3)),
    ]
    result = net_strategy_signals(tagged)
    assert len(result.loose) == 1
    net = result.loose[0]
    assert net.signal.symbol == "BTCUSDT"
    assert net.signal.side is Side.BUY
    assert abs(net.signal.qty - 0.7) < 1e-9
    assert net.contributions["sma_crossover"] == 1.0
    assert net.contributions["market_making"] == -0.3


def test_cancels_fully_opposing_signals() -> None:
    tagged = [
        ("a", _sig("ETHUSDT", Side.BUY, 0.5)),
        ("b", _sig("ETHUSDT", Side.SELL, 0.5)),
    ]
    result = net_strategy_signals(tagged)
    assert result.loose == []


def test_pair_groups_pass_through() -> None:
    tagged = [
        (
            "pairs_trading_usdt_usdc",
            _sig("BTCUSDT", Side.BUY, 1.0, group_id="g1"),
        ),
        (
            "pairs_trading_usdt_usdc",
            _sig("BTCUSDC", Side.SELL, 1.0, group_id="g1"),
        ),
    ]
    result = net_strategy_signals(tagged)
    assert result.loose == []
    assert len(result.groups["g1"]) == 2
    assert all(s.strategy_name == "pairs_trading_usdt_usdc" for s in result.groups["g1"])
