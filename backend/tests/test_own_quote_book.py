"""OwnQuoteBook level tracking."""

from common.enums import OrderStatus, OrderType, Side
from common.types import ChildOrder, Fill
from engine.market_data.own_quote_book import OwnQuoteBook


def test_sync_working_tracks_bid_ask() -> None:
    book = OwnQuoteBook()
    children = [
        ChildOrder(
            id="b1",
            parent_id="Q-BTCUSDT-abc",
            symbol="BTCUSDT",
            side=Side.BUY,
            qty=0.01,
            price=99.0,
            order_type=OrderType.LIMIT,
            status=OrderStatus.NEW,
        ),
        ChildOrder(
            id="a1",
            parent_id="Q-BTCUSDT-abc",
            symbol="BTCUSDT",
            side=Side.SELL,
            qty=0.01,
            price=101.0,
            order_type=OrderType.LIMIT,
            status=OrderStatus.NEW,
        ),
    ]
    st = book.sync_working("BTCUSDT", children)
    assert st.own_bid is not None
    assert st.own_ask is not None


def test_level_fill_updates_ledger() -> None:
    book = OwnQuoteBook()
    fill = Fill(
        child_id="b1",
        parent_id="Q-BTCUSDT-abc",
        symbol="BTCUSDT",
        side=Side.BUY,
        qty=0.01,
        price=100.0,
        fee=0.0,
        fee_asset="USDT",
    )
    book.on_level_fill("BTCUSDT", fill, position_qty=0.01, adverse_bps=5.0)
    st = book.state("BTCUSDT")
    assert st.ledger.entry_qty > 0
