"""Fill open/close classification for the trades table."""

from __future__ import annotations

from common.enums import Side
from common.types import Fill, Position
from engine.performance.fill_classification import classify_fill, position_before_fill


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


def test_close_after_account_update_uses_pre_fill_qty() -> None:
    """Post-fill book from ACCOUNT_UPDATE must not classify a reduce as an open."""
    post = Position(symbol="BTCUSDT", qty=1.0, avg_entry_price=100.0)
    f = _fill(Side.SELL, 1.0, 105.0)
    pre = position_before_fill(post, f)
    c = classify_fill(pre, f)
    assert c.action == "close"
    assert c.pnl == 5.0


def test_close_when_account_update_already_popped_row() -> None:
    """Flat venue row removed before ORDER_TRADE_UPDATE must still be a close."""
    f = _fill(Side.SELL, 1.0, 105.0)
    pre = position_before_fill(None, f, fallback_entry=100.0)
    assert pre is not None
    assert pre.qty == 1.0
    c = classify_fill(pre, f)
    assert c.action == "close"
    assert c.pnl == 5.0


def test_close_when_account_update_popped_row_uses_venue_rp() -> None:
    f = Fill(
        child_id="c1",
        parent_id=None,
        symbol="BTCUSDT",
        side=Side.SELL,
        qty=1.0,
        price=105.0,
        fee=0.0,
        fee_asset="USDT",
        realized_pnl=-3.5,
    )
    pre = position_before_fill(None, f)
    c = classify_fill(pre, f)
    assert c.action == "close"
    assert c.pnl == -3.5


def test_close_long_prefers_computed_when_venue_rp_is_dust() -> None:
    """Tiny non-zero Binance ``rp`` must not override clear entry/exit economics."""
    pos = Position(symbol="BTCUSDT", qty=1.0, avg_entry_price=100.0)
    f = Fill(
        child_id="c1",
        parent_id=None,
        symbol="BTCUSDT",
        side=Side.SELL,
        qty=1.0,
        price=105.0,
        fee=0.0,
        fee_asset="USDT",
        realized_pnl=0.002,
    )
    c = classify_fill(pos, f)
    assert c.pnl == 5.0
