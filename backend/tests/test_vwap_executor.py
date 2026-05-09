"""VwapExecutor end-to-end with a mock gateway.

Mocks live ONLY in tests per the project rules; the engine and gateway
code never imports these helpers.
"""

from __future__ import annotations

import asyncio
import os

import pytest

os.environ.setdefault("BINANCE_API_KEY", "test")
os.environ.setdefault("BINANCE_API_SECRET", "test")

from common.config import Settings  # noqa: E402
from common.enums import AlgoMode, OrderStatus, Side  # noqa: E402
from common.events import EventBus  # noqa: E402
from common.types import ChildOrder, Kline, ParentOrder, Position  # noqa: E402
from engine.execution.vwap_executor import ExecutorConfig, VwapExecutor  # noqa: E402
from engine.market_data.feature_store import FeatureStore  # noqa: E402
from engine.market_data.orderbook import OrderBookStore  # noqa: E402
from engine.market_data.trade_tape import TradeTape  # noqa: E402
from engine.orders.order_manager import OrderManager  # noqa: E402
from gateways.gateway_interface import GatewayInterface, SymbolFilters  # noqa: E402


class _MockGateway(GatewayInterface):
    def __init__(self, filters: dict[str, SymbolFilters] | None = None) -> None:
        self.placed: list[ChildOrder] = []
        self._filters = filters or {}

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def subscribe_market_data(self, *a, **kw) -> None: ...
    async def subscribe_user_data(self, *a, **kw) -> None: ...

    async def place_order(self, order: ChildOrder) -> ChildOrder:
        order.venue_order_id = f"V-{len(self.placed)}"
        order.status = OrderStatus.FILLED
        order.filled_qty = order.qty
        order.avg_fill_price = order.price or 100.0
        self.placed.append(order)
        return order

    async def cancel_order(self, symbol: str, client_order_id: str) -> None:
        return

    def get_symbol_filters(self, symbol: str) -> SymbolFilters | None:
        return self._filters.get(symbol.upper())

    async def fetch_positions(self) -> list[Position]:
        return []

    async def fetch_balance(self) -> float:
        return 1000.0

    async def book_snapshot(self, symbol: str, depth: int = 100) -> dict:
        return {"lastUpdateId": 0, "bids": [], "asks": []}

    async def klines(self, symbol: str, interval: str, limit: int = 200) -> list[Kline]:
        return []


@pytest.mark.asyncio
async def test_executor_runs_full_schedule() -> None:
    bus = EventBus()
    settings = Settings(binance_api_key="x", binance_api_secret="y",
                        vwap_duration_sec=1, vwap_num_slices=3, symbols=["BTCUSDT"])
    gateway = _MockGateway()
    om = OrderManager(gateway, bus)
    books = OrderBookStore(["BTCUSDT"])
    books.get("BTCUSDT").apply_snapshot(
        bids=[(100.0, 1.0)], asks=[(100.5, 1.0)], last_update_id=1,
    )
    features = FeatureStore(books, TradeTape(window_sec=10), settings)

    executor = VwapExecutor(
        order_manager=om,
        gateway=gateway,
        features=features,
        price_provider=lambda _sym: 100.0,
        settings=settings,
        config=ExecutorConfig(duration_sec=0.3, n_slices=3, slice_timeout_sec=0.1),
    )

    parent = ParentOrder(id="P-1", symbol="BTCUSDT", side=Side.BUY, qty=0.6,
                          algo_mode=AlgoMode.NORMAL)
    await executor.execute(parent)
    # Wait for the schedule to drain.
    await asyncio.sleep(1.2)

    assert len(gateway.placed) == 3
    assert pytest.approx(sum(o.qty for o in gateway.placed)) == 0.6


@pytest.mark.asyncio
async def test_executor_collapses_slices_when_lot_step_exceeds_slice_qty() -> None:
    """0.001 BTC split across 6 slices is below many futures LOT_SIZE steps."""
    bus = EventBus()
    settings = Settings(binance_api_key="x", binance_api_secret="y",
                        vwap_duration_sec=1, vwap_num_slices=6, symbols=["BTCUSDT"])
    gateway = _MockGateway(filters={
        "BTCUSDT": SymbolFilters(symbol="BTCUSDT", step_size=0.001, min_qty=0.001),
    })
    om = OrderManager(gateway, bus)
    books = OrderBookStore(["BTCUSDT"])
    books.get("BTCUSDT").apply_snapshot(
        bids=[(100.0, 1.0)], asks=[(100.5, 1.0)], last_update_id=1,
    )
    features = FeatureStore(books, TradeTape(window_sec=10), settings)

    executor = VwapExecutor(
        order_manager=om,
        gateway=gateway,
        features=features,
        price_provider=lambda _sym: 100.0,
        settings=settings,
        config=ExecutorConfig(duration_sec=0.3, n_slices=6, slice_timeout_sec=0.1),
    )

    parent = ParentOrder(id="P-small", symbol="BTCUSDT", side=Side.BUY, qty=0.001,
                         algo_mode=AlgoMode.NORMAL)
    await executor.execute(parent)
    await asyncio.sleep(0.8)

    assert len(gateway.placed) == 1
    assert pytest.approx(gateway.placed[0].qty) == 0.001


@pytest.mark.asyncio
async def test_executor_respects_min_notional_filter() -> None:
    """A 6-way split that would put each slice under MIN_NOTIONAL must collapse."""
    bus = EventBus()
    settings = Settings(binance_api_key="x", binance_api_secret="y",
                        vwap_duration_sec=1, vwap_num_slices=6, symbols=["BTCUSDT"])
    # 0.06 BTC at $100 = $6 total parent notional. Per-slice $1 < $5 min.
    gateway = _MockGateway(filters={
        "BTCUSDT": SymbolFilters(symbol="BTCUSDT", step_size=0.001, min_notional=5.0),
    })
    om = OrderManager(gateway, bus)
    books = OrderBookStore(["BTCUSDT"])
    books.get("BTCUSDT").apply_snapshot(
        bids=[(100.0, 1.0)], asks=[(100.5, 1.0)], last_update_id=1,
    )
    features = FeatureStore(books, TradeTape(window_sec=10), settings)

    executor = VwapExecutor(
        order_manager=om,
        gateway=gateway,
        features=features,
        price_provider=lambda _sym: 100.0,
        settings=settings,
        config=ExecutorConfig(duration_sec=0.3, n_slices=6, slice_timeout_sec=0.1),
    )

    parent = ParentOrder(id="P-notional", symbol="BTCUSDT", side=Side.BUY, qty=0.06,
                         algo_mode=AlgoMode.NORMAL)
    await executor.execute(parent)
    await asyncio.sleep(0.8)

    # 0.06 / n * 100 >= 5  =>  n <= 1.2  => collapses to 1 slice.
    assert len(gateway.placed) == 1
    assert pytest.approx(gateway.placed[0].qty) == 0.06


@pytest.mark.asyncio
async def test_reduce_only_order_skips_min_notional() -> None:
    """A sub-MIN_NOTIONAL position must still be closeable as reduce-only.

    Mirrors the live failure from BTCUSDT testnet: a 0.0002 BTC stop-loss
    at $80 000 has $16 notional, well below the $50 MIN_NOTIONAL floor.
    Without reduce-only the executor pre-validates the slice and the
    venue rejects with -4164. With reduce-only the venue waives
    MIN_NOTIONAL, so the slicer must let the qty through and the gateway
    must see `reduce_only=True` on every child.
    """
    bus = EventBus()
    settings = Settings(binance_api_key="x", binance_api_secret="y",
                        vwap_duration_sec=1, vwap_num_slices=6, symbols=["BTCUSDT"])
    # Step floor at 0.0002 so the executor can't split the 0.0002 parent;
    # this isolates the MIN_NOTIONAL waiver from the slicer's natural
    # collapse-to-fewer-slices behavior.
    gateway = _MockGateway(filters={
        "BTCUSDT": SymbolFilters(symbol="BTCUSDT", step_size=0.0002, min_notional=50.0),
    })
    om = OrderManager(gateway, bus)
    books = OrderBookStore(["BTCUSDT"])
    books.get("BTCUSDT").apply_snapshot(
        bids=[(80000.0, 1.0)], asks=[(80000.5, 1.0)], last_update_id=1,
    )
    features = FeatureStore(books, TradeTape(window_sec=10), settings)

    executor = VwapExecutor(
        order_manager=om,
        gateway=gateway,
        features=features,
        price_provider=lambda _sym: 80000.0,
        settings=settings,
        config=ExecutorConfig(duration_sec=0.3, n_slices=6, slice_timeout_sec=0.1),
    )

    parent = ParentOrder(
        id="P-reduce", symbol="BTCUSDT", side=Side.SELL, qty=0.0002,
        algo_mode=AlgoMode.NORMAL, reduce_only=True,
    )
    await executor.execute(parent)
    await asyncio.sleep(0.8)

    assert len(gateway.placed) == 1
    placed = gateway.placed[0]
    assert pytest.approx(placed.qty) == 0.0002
    assert placed.reduce_only is True


@pytest.mark.asyncio
async def test_non_reduce_only_order_still_blocks_on_min_notional() -> None:
    """An entry order (reduce_only=False) below MIN_NOTIONAL must still
    be rejected pre-flight; the waiver only applies to reduce-only."""
    bus = EventBus()
    settings = Settings(binance_api_key="x", binance_api_secret="y",
                        vwap_duration_sec=1, vwap_num_slices=6, symbols=["BTCUSDT"])
    gateway = _MockGateway(filters={
        "BTCUSDT": SymbolFilters(symbol="BTCUSDT", step_size=0.0002, min_notional=50.0),
    })
    om = OrderManager(gateway, bus)
    books = OrderBookStore(["BTCUSDT"])
    books.get("BTCUSDT").apply_snapshot(
        bids=[(80000.0, 1.0)], asks=[(80000.5, 1.0)], last_update_id=1,
    )
    features = FeatureStore(books, TradeTape(window_sec=10), settings)

    executor = VwapExecutor(
        order_manager=om,
        gateway=gateway,
        features=features,
        price_provider=lambda _sym: 80000.0,
        settings=settings,
        config=ExecutorConfig(duration_sec=0.3, n_slices=6, slice_timeout_sec=0.1),
    )

    parent = ParentOrder(
        id="P-entry-tiny", symbol="BTCUSDT", side=Side.BUY, qty=0.0002,
        algo_mode=AlgoMode.NORMAL,  # reduce_only defaults to False
    )
    await executor.execute(parent)
    await asyncio.sleep(0.8)

    # Pre-flight validation should refuse: 0.0002 * 80000 = $16 < $50.
    assert gateway.placed == []


def test_parse_symbol_filters_extracts_step_tick_min_qty_min_notional() -> None:
    from gateways.binance.binance_gateway import _parse_symbol_filters

    parsed = _parse_symbol_filters({
        "symbols": [{
            "symbol": "BTCUSDT",
            "filters": [
                {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                {"filterType": "MIN_NOTIONAL", "notional": "5"},
            ],
        }],
    })

    f = parsed["BTCUSDT"]
    assert f.step_size == 0.001
    assert f.tick_size == 0.10
    assert f.min_qty == 0.001
    assert f.min_notional == 5.0


def test_parse_symbol_filters_accepts_notional_variant() -> None:
    """Binance may emit NOTIONAL/minNotional instead of MIN_NOTIONAL/notional."""
    from gateways.binance.binance_gateway import _parse_symbol_filters

    parsed = _parse_symbol_filters({
        "symbols": [{
            "symbol": "ETHUSDT",
            "filters": [
                {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                {"filterType": "NOTIONAL", "minNotional": "20"},
            ],
        }],
    })

    f = parsed["ETHUSDT"]
    assert f.step_size == 0.001
    assert f.tick_size == 0.01
    assert f.min_qty == 0.001
    assert f.min_notional == 20.0
