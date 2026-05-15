"""OrderBook snapshot + diff application + imbalance math."""

from __future__ import annotations

from engine.market_data.orderbook import OrderBook
from gateways.gateway_interface import DepthDiff


def test_snapshot_orders_and_imbalance() -> None:
    book = OrderBook(symbol="BTCUSDT")
    book.apply_snapshot(
        bids=[(100.0, 1.0), (99.5, 2.0), (99.0, 3.0)],
        asks=[(100.5, 1.0), (101.0, 2.0), (101.5, 3.0)],
        last_update_id=10,
    )
    assert book.ready()
    assert book.best_bid() == 100.0
    assert book.best_ask() == 100.5
    assert book.mid() == 100.25
    # Symmetric depths => imbalance ~ 0.
    assert abs(book.imbalance(top_n=3)) < 1e-9


def test_diff_inserts_and_removes() -> None:
    book = OrderBook(symbol="BTCUSDT")
    book.apply_snapshot(
        bids=[(100.0, 1.0)],
        asks=[(100.5, 1.0)],
        last_update_id=5,
    )

    # Stale diff (<= last_update_id) is ignored.
    book.apply_diff(
        DepthDiff(
            symbol="BTCUSDT",
            bids=[(100.0, 5.0)],
            asks=[],
            first_update_id=1,
            final_update_id=5,
        )
    )
    assert book.bids[0].qty == 1.0

    # Insert two new bid levels and remove the old one.
    book.apply_diff(
        DepthDiff(
            symbol="BTCUSDT",
            bids=[(100.0, 0.0), (99.5, 2.0), (99.0, 3.0)],
            asks=[(100.5, 4.0)],
            first_update_id=6,
            final_update_id=8,
        )
    )
    bid_prices = [lvl.price for lvl in book.bids]
    assert bid_prices == [99.5, 99.0]
    assert book.asks[0].qty == 4.0


def test_invalidate_clears_ready() -> None:
    book = OrderBook(symbol="BTCUSDT")
    book.apply_snapshot(
        bids=[(100.0, 1.0)],
        asks=[(100.5, 1.0)],
        last_update_id=5,
    )
    assert book.ready()
    book.invalidate()
    assert not book.ready()
    assert book.last_update_id == 0


def test_imbalance_signs() -> None:
    book = OrderBook(symbol="BTCUSDT")
    book.apply_snapshot(
        bids=[(100.0, 10.0)],
        asks=[(100.5, 1.0)],
        last_update_id=1,
    )
    # 10 vs 1 -> heavily bid-leaning -> positive imbalance close to +1.
    assert book.imbalance(top_n=1) > 0.8
