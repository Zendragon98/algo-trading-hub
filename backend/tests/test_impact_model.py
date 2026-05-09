"""Square-root impact model: sign, magnitude, and disable behaviour."""

from __future__ import annotations

import math

from common.enums import Side
from engine.execution.impact_model import ImpactConfig, ImpactModel
from engine.market_data.orderbook import OrderBook


def _book(top_qty: float = 100.0) -> OrderBook:
    book = OrderBook(symbol="BTCUSDT")
    book.apply_snapshot(
        bids=[(100.0, top_qty), (99.5, top_qty), (99.0, top_qty)],
        asks=[(100.5, top_qty), (101.0, top_qty), (101.5, top_qty)],
        last_update_id=1,
    )
    return book


def test_disabled_model_returns_zero_impact() -> None:
    model = ImpactModel(ImpactConfig(enabled=False, k=10.0))
    bps = model.estimate_bps(Side.BUY, qty=10.0, book=_book())
    assert bps == 0.0
    price, bps = model.apply(Side.BUY, qty=10.0, raw_price=100.5, book=_book())
    assert price == 100.5
    assert bps == 0.0


def test_buy_pays_more_sell_receives_less() -> None:
    model = ImpactModel(ImpactConfig(enabled=True, k=1.0, top_n=3))
    book = _book(top_qty=100.0)

    buy_price, buy_bps = model.apply(Side.BUY, qty=50.0, raw_price=100.5, book=book)
    sell_price, sell_bps = model.apply(Side.SELL, qty=50.0, raw_price=100.0, book=book)

    assert buy_bps > 0
    assert sell_bps > 0
    assert buy_price > 100.5    # buyer pays a worse price
    assert sell_price < 100.0   # seller receives a worse price
    assert math.isclose(buy_bps, sell_bps, rel_tol=1e-6)


def test_impact_grows_with_size() -> None:
    model = ImpactModel(ImpactConfig(enabled=True, k=0.5, top_n=3))
    book = _book(top_qty=100.0)

    small = model.estimate_bps(Side.BUY, qty=1.0, book=book)
    big = model.estimate_bps(Side.BUY, qty=100.0, book=book)
    assert big > small
    # Square-root: 100x size -> 10x impact.
    assert math.isclose(big / small, 10.0, rel_tol=1e-6)


def test_no_book_no_impact() -> None:
    model = ImpactModel(ImpactConfig(enabled=True, k=1.0))
    assert model.estimate_bps(Side.BUY, qty=10.0, book=None) == 0.0
    book = OrderBook(symbol="BTCUSDT")  # never seeded
    assert model.estimate_bps(Side.BUY, qty=10.0, book=book) == 0.0
