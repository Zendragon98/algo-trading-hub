"""MM inventory notional cap resolution."""

import pytest

from common.config import Settings
from engine.strategies.market_making.inventory_cap import resolve_mm_inventory_notional
from engine.strategies import mm_core


def test_resolve_mm_inventory_notional_prefers_mm2() -> None:
    s = Settings(
        binance_api_key="x",
        binance_api_secret="y",
        mm_max_inventory_notional=0.0,
        mm2_max_inventory_notional=300.0,
        max_symbol_notional_pct=0.5,
    )
    assert resolve_mm_inventory_notional(s, equity=10_000.0) == pytest.approx(300.0)


def test_feat_and_mm_core_inventory_ratio_match_with_mm2_cap() -> None:
    from engine.market_data.feature_store import _inventory_ratio

    s = Settings(
        binance_api_key="x",
        binance_api_secret="y",
        mm_max_inventory_notional=0.0,
        mm2_max_inventory_notional=300.0,
        max_symbol_notional_pct=0.5,
    )
    feat_ratio = _inventory_ratio(1.0, 100.0, 10_000.0, s, 0.0, 0.0)
    core_ratio = mm_core.inventory_ratio(1.0, 100.0, s, 10_000.0)
    assert feat_ratio == pytest.approx(core_ratio)
    assert feat_ratio == pytest.approx(100.0 / 300.0)


def test_feat_inventory_ratio_on_snapshot() -> None:
    from engine.market_data.feature_store import FeatureStore
    from engine.market_data.own_quote_book import OwnBookState
    from engine.market_data.orderbook import OrderBookStore
    from engine.market_data.trade_tape import TradeTape

    s = Settings(
        binance_api_key="x",
        binance_api_secret="y",
        mm2_max_inventory_notional=300.0,
    )
    books = OrderBookStore(["BTCUSDT"])
    books.get("BTCUSDT").apply_snapshot(
        bids=[(99.0, 1.0)],
        asks=[(101.0, 1.0)],
        last_update_id=1,
    )
    store = FeatureStore(books, TradeTape(window_sec=60.0), s)
    own = OwnBookState(symbol="BTCUSDT")
    feat = store.snapshot("BTCUSDT", own=own, position_qty=1.0, equity=10_000.0)
    assert feat.inventory_ratio == pytest.approx(100.0 / 300.0)
