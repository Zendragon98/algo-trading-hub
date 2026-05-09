"""AlgoWheel mode-selection rule contract.

The wheel must:
  - frontload BUY when book leans bid AND buyers were aggressive
  - backload BUY when book leans ask AND sellers were aggressive
  - mirror logic for SELL
  - normal otherwise
"""

from __future__ import annotations

from common.enums import AlgoMode, Side
from common.types import ParentOrder
from engine.execution.algo_wheel import AlgoWheel
from engine.market_data.feature_store import Features


def _features(imb: float, bid_hit: float, ask_hit: float) -> Features:
    return Features(
        symbol="BTCUSDT",
        mid=100.0,
        spread_bps=1.0,
        micro_price=100.0,
        imbalance_topn=imb,
        bid_hit_ratio=bid_hit,
        ask_hit_ratio=ask_hit,
    )


def _parent(side: Side) -> ParentOrder:
    return ParentOrder(id="P-1", symbol="BTCUSDT", side=side, qty=1.0)


def test_buy_frontloads_on_aggressive_buyers() -> None:
    wheel = AlgoWheel()
    mode = wheel.choose(_parent(Side.BUY), _features(imb=0.5, bid_hit=0.2, ask_hit=0.8))
    assert mode is AlgoMode.FRONTLOAD


def test_buy_backloads_on_passive_market() -> None:
    wheel = AlgoWheel()
    mode = wheel.choose(_parent(Side.BUY), _features(imb=-0.5, bid_hit=0.8, ask_hit=0.2))
    assert mode is AlgoMode.BACKLOAD


def test_sell_frontloads_on_aggressive_sellers() -> None:
    wheel = AlgoWheel()
    mode = wheel.choose(_parent(Side.SELL), _features(imb=-0.5, bid_hit=0.8, ask_hit=0.2))
    assert mode is AlgoMode.FRONTLOAD


def test_normal_when_signals_disagree() -> None:
    wheel = AlgoWheel()
    mode = wheel.choose(_parent(Side.BUY), _features(imb=0.0, bid_hit=0.5, ask_hit=0.5))
    assert mode is AlgoMode.NORMAL
