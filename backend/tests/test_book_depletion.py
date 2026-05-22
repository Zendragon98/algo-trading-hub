"""BookDepletionTracker scores."""

from common.config import Settings
from engine.market_data.book_depletion import BookDepletionTracker, _depletion_score
from engine.market_data.orderbook import OrderBook


def test_depletion_score_increases_when_thin() -> None:
    assert _depletion_score(0.3) > _depletion_score(0.9)


def test_on_depth_updates_ratios() -> None:
    tr = BookDepletionTracker(Settings(mm_depletion_top_n=5))
    book = OrderBook(symbol="BTCUSDT")
    book.apply_snapshot([(100.0, 10.0)], [(101.0, 10.0)], last_update_id=1)
    tr.on_depth("BTCUSDT", book, ts=1.0)
    book.apply_snapshot([(100.0, 1.0)], [(101.0, 10.0)], last_update_id=2)
    tr.on_depth("BTCUSDT", book, ts=2.0)
    st = tr.stats("BTCUSDT")
    assert st.bid_depletion_score > 0.5
