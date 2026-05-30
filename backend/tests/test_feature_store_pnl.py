"""Feature store PnL uses venue_pnl path."""

from __future__ import annotations

from common.config import Settings
from engine.market_data.feature_store import FeatureStore
from engine.market_data.orderbook import OrderBookStore
from engine.market_data.own_quote_book import EntryLedger, OwnBookState
from engine.market_data.trade_tape import TradeTape
from engine.strategies.position_sync import VenuePosition


def test_feature_snapshot_uses_fill_vwap_and_venue_upnl() -> None:
    settings = Settings()
    books = OrderBookStore(["ETHUSDT"])
    books.get("ETHUSDT").apply_snapshot(
        bids=[(100.0, 10.0)],
        asks=[(100.02, 10.0)],
        last_update_id=1,
    )
    store = FeatureStore(books, TradeTape(window_sec=60.0), settings)
    own = OwnBookState(symbol="ETHUSDT")
    own.ledger = EntryLedger(entry_mid=98.0, entry_qty=1.0, opened_ts=1.0)
    venue = VenuePosition(
        qty=1.0,
        avg_entry_price=99.0,
        mark_price=100.01,
        exchange_unrealized_pnl=1.0,
    )
    feat = store.snapshot(
        "ETHUSDT",
        own=own,
        position_qty=1.0,
        equity=10_000.0,
        venue=venue,
        fill_vwap=99.5,
    )
    assert feat.entry_mid == 99.5
    assert feat.unrealized_pnl_bps > 0.0
