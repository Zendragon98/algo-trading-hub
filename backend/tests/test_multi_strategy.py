"""Multi-strategy mode: all strategies run with internal netting."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("BINANCE_API_KEY", "test")
os.environ.setdefault("BINANCE_API_SECRET", "test")

from collections.abc import Iterable  # noqa: E402

from common.config import Settings  # noqa: E402
from common.enums import EngineStatus, Side  # noqa: E402
from common.events import EventBus  # noqa: E402
from common.types import ChildOrder, Kline, Position, QuoteIntent, Signal  # noqa: E402
from engine.core.engine import ALL_STRATEGIES_MODE, Engine  # noqa: E402
from engine.market_data.feature_store import Features  # noqa: E402
from engine.strategies.flow_momentum import FlowMomentumStrategy  # noqa: E402
from engine.strategies.strategy_base import StrategyBase  # noqa: E402
from gateways.gateway_interface import GatewayInterface, SymbolFilters  # noqa: E402


class _MmStubStrategy(StrategyBase):
    name = "market_making_v2"
    display_label = "MM stub"
    description = "stub"

    def __init__(self) -> None:
        self.quote_tick_count = 0

    def symbols(self) -> list[str]:
        return ["BTCUSDT"]

    def on_tick(self, features: dict[str, Features]) -> Iterable[Signal]:
        return []

    def on_tick_quotes(self, features: dict[str, Features]) -> list[QuoteIntent]:
        self.quote_tick_count += 1
        return []


class _EmitStrategy(StrategyBase):
    def __init__(self, name: str, sym: str, signal: Signal | None = None) -> None:
        self.name = name
        self.display_label = name
        self.description = name
        self._sym = sym
        self._signal = signal
        self.tick_count = 0

    def symbols(self) -> list[str]:
        return [self._sym]

    def on_tick(self, features: dict[str, Features]) -> Iterable[Signal]:
        self.tick_count += 1
        return [self._signal] if self._signal is not None else []


class _MockGateway(GatewayInterface):
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def subscribe_market_data(self, *a, **kw) -> None: ...
    async def subscribe_user_data(self, *a, **kw) -> None: ...

    async def place_order(self, order: ChildOrder) -> ChildOrder:
        return order

    async def cancel_order(self, symbol: str, client_order_id: str) -> None:
        return

    def get_symbol_filters(self, symbol: str) -> SymbolFilters | None:
        return None

    async def fetch_positions(self) -> list[Position]:
        return []

    async def fetch_balance(self) -> float:
        return 0.0

    async def book_snapshot(self, symbol: str, depth: int = 100) -> dict:
        return {"lastUpdateId": 0, "bids": [], "asks": []}

    async def klines(self, symbol: str, interval: str, limit: int = 200) -> list[Kline]:
        return []


def _engine(strategies: list[_EmitStrategy]) -> Engine:
    settings = Settings(
        binance_api_key="x",
        binance_api_secret="y",
        symbols=[s.symbols()[0] for s in strategies],
        strategy=ALL_STRATEGIES_MODE,
    )
    return Engine(settings=settings, bus=EventBus(), gateway=_MockGateway(), strategies=strategies)


def test_set_active_all_mode() -> None:
    a = _EmitStrategy("a", "BTCUSDT")
    b = _EmitStrategy("b", "ETHUSDT")
    engine = _engine([a, b])
    engine.set_active_strategy(ALL_STRATEGIES_MODE)
    assert engine.is_multi_strategy_mode()
    assert engine.active_strategy_name == ALL_STRATEGIES_MODE


@pytest.mark.asyncio
async def test_flow_momentum_ticks_in_all_mode() -> None:
    settings = Settings(
        binance_api_key="x",
        binance_api_secret="y",
        symbols=["BTCUSDT"],
        strategy=ALL_STRATEGIES_MODE,
        flow_symbols=["BTCUSDT"],
    )
    flow_tick = 0

    class _CountFlow(FlowMomentumStrategy):
        def on_tick(self, features: dict[str, Features]):
            nonlocal flow_tick
            flow_tick += 1
            return super().on_tick(features)

    counted = _CountFlow(settings)
    engine = Engine(
        settings=settings,
        bus=EventBus(),
        gateway=_MockGateway(),
        strategies=[_EmitStrategy("other", "ETHUSDT"), counted],
    )
    engine.set_active_strategy(ALL_STRATEGIES_MODE)
    await engine._evaluate_strategies()
    assert flow_tick == 1


@pytest.mark.asyncio
async def test_market_making_quotes_in_all_mode() -> None:
    mm = _MmStubStrategy()
    engine = _engine([_EmitStrategy("alpha", "ETHUSDT"), mm])
    engine.set_active_strategy(ALL_STRATEGIES_MODE)
    engine._state.status = EngineStatus.RUNNING
    await engine._evaluate_strategies()
    assert mm.quote_tick_count == 1


@pytest.mark.asyncio
async def test_all_strategies_evaluate_on_tick() -> None:
    buy = Signal(symbol="BTCUSDT", side=Side.BUY, qty=1.0, reason="buy")
    sell = Signal(symbol="BTCUSDT", side=Side.SELL, qty=0.4, reason="sell")
    a = _EmitStrategy("strat_a", "BTCUSDT", buy)
    b = _EmitStrategy("strat_b", "BTCUSDT", sell)
    engine = _engine([a, b])
    engine.set_active_strategy(ALL_STRATEGIES_MODE)
    await engine._evaluate_strategies()
    assert a.tick_count == 1
    assert b.tick_count == 1


def test_strategy_ledger_applies_attributed_fill() -> None:
    engine = _engine([_EmitStrategy("a", "BTCUSDT"), _EmitStrategy("b", "ETHUSDT")])
    engine.set_active_strategy(ALL_STRATEGIES_MODE)
    parent_id = "p1"
    engine._parent_attribution[parent_id] = {
        "strat_a": {"BTCUSDT": 1.0},
        "strat_b": {"BTCUSDT": -0.3},
    }
    from common.types import Fill

    fill = Fill(
        child_id="c1",
        parent_id=parent_id,
        symbol="BTCUSDT",
        side=Side.BUY,
        qty=0.7,
        price=100.0,
        fee=0.0,
        fee_asset="USDT",
    )
    engine._apply_attributed_fill(
        fill,
        parent_id,
        engine._parent_attribution[parent_id],
        None,
    )
    assert abs(engine.strategy_ledger.qty("strat_a", "BTCUSDT") - 1.0) < 1e-9
    assert abs(engine.strategy_ledger.qty("strat_b", "BTCUSDT") - (-0.3)) < 1e-9
