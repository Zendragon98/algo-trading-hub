"""Fill open/close classification for the trades table."""

from __future__ import annotations

from common.enums import Side
from common.types import Fill, Position
from engine.performance.fill_classification import classify_fill


def _fill(side: Side, qty: float, price: float) -> Fill:
    return Fill(
        child_id="c1",
        parent_id=None,
        symbol="BTCUSDT",
        side=side,
        qty=qty,
        price=price,
        fee=0.0,
        fee_asset="USDT",
    )


def test_open_from_flat() -> None:
    c = classify_fill(None, _fill(Side.BUY, 1.0, 100.0))
    assert c.action == "open"
    assert c.entry_price == 100.0
    assert c.exit_price is None
    assert c.pnl is None


def test_close_long() -> None:
    pos = Position(symbol="BTCUSDT", qty=2.0, avg_entry_price=100.0)
    c = classify_fill(pos, _fill(Side.SELL, 1.0, 105.0))
    assert c.action == "close"
    assert c.entry_price == 100.0
    assert c.exit_price == 105.0
    assert c.pnl == 5.0


def test_close_short() -> None:
    pos = Position(symbol="BTCUSDT", qty=-2.0, avg_entry_price=100.0)
    c = classify_fill(pos, _fill(Side.BUY, 1.0, 95.0))
    assert c.action == "close"
    assert c.pnl == 5.0
