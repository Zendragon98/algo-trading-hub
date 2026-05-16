"""Operator halt API + engine auto-flatten on MAJOR trips."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("BINANCE_API_KEY", "test")
os.environ.setdefault("BINANCE_API_SECRET", "test")

from httpx import ASGITransport, AsyncClient  # noqa: E402

from api.server import create_app  # noqa: E402
from common.config import Settings  # noqa: E402
from common.events import EventBus  # noqa: E402
from common.types import ChildOrder, Kline, Position, Tick  # noqa: E402
from engine.core.engine import Engine, EngineStatus  # noqa: E402
from engine.risk.circuit_breaker import (  # noqa: E402
    Breach,
    BreakerScope,
    BreakerSeverity,
)
from engine.strategies.strategy_base import StrategyBase  # noqa: E402
from gateways.gateway_interface import GatewayInterface, SymbolFilters  # noqa: E402


class _StubStrategy(StrategyBase):
    name = "stub"
    display_label = "stub"
    description = "stub"

    def symbols(self) -> list[str]:
        return ["BTCUSDT"]

    def on_tick(self, features):
        return []


class _MockGateway(GatewayInterface):
    def __init__(self) -> None:
        self.cancel_all_calls = 0

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def subscribe_market_data(self, *a, **kw) -> None: ...
    async def subscribe_user_data(self, *a, **kw) -> None: ...

    async def place_order(self, order: ChildOrder) -> ChildOrder:
        return order

    async def cancel_order(self, symbol: str, client_order_id: str) -> None:
        return

    async def cancel_all_open_orders(self) -> None:
        self.cancel_all_calls += 1

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


def _engine() -> tuple[Engine, _MockGateway]:
    gateway = _MockGateway()
    settings = Settings(
        binance_api_key="x",
        binance_api_secret="y",
        symbols=["BTCUSDT"],
        strategy="stub",
    )
    engine = Engine(
        settings=settings,
        bus=EventBus(),
        gateway=gateway,
        strategies=[_StubStrategy()],
    )
    return engine, gateway


@pytest.mark.asyncio
async def test_operator_halt_trips_breaker_and_flattens() -> None:
    engine, gateway = _engine()
    engine.flatten = AsyncMock()  # type: ignore[method-assign]
    engine._state.status = EngineStatus.RUNNING

    await engine.operator_halt(detail="test halt", flatten=True, pause=True)

    assert engine.risk.kill_switch is True
    codes = {s.code for s in engine.risk.breaker.active()}
    assert "operator_halt" in codes
    assert gateway.cancel_all_calls == 1
    engine.flatten.assert_awaited_once()  # type: ignore[attr-defined]
    assert engine._state.status.value == "paused"


@pytest.mark.asyncio
async def test_maybe_flatten_is_idempotent_while_latched() -> None:
    engine, gateway = _engine()
    flatten_calls = 0

    async def _flatten() -> None:
        nonlocal flatten_calls
        flatten_calls += 1

    engine.flatten = _flatten  # type: ignore[method-assign]
    engine._breaker.trip(
        Breach(
            code="max_drawdown",
            scope=BreakerScope.ENGINE,
            severity=BreakerSeverity.MAJOR,
        )
    )

    await engine._maybe_flatten_for_breaker()
    await engine._maybe_flatten_for_breaker()

    assert flatten_calls == 1


@pytest.mark.asyncio
async def test_apply_breaker_rearm_side_effects_reanchors_max_drawdown() -> None:
    engine, _ = _engine()
    engine._portfolio.seed_cash(1000.0)
    engine._portfolio.update_cash(800.0)
    engine._pnl.update()
    engine._breaker.trip(
        Breach(
            code="max_drawdown",
            scope=BreakerScope.ENGINE,
            severity=BreakerSeverity.MAJOR,
        )
    )
    before = {s.code for s in engine._breaker.active()}
    engine._breaker.rearm(code="max_drawdown")
    cleared = before - {s.code for s in engine._breaker.active()}
    engine.apply_breaker_rearm_side_effects(cleared)
    tick = Tick(symbol="BTCUSDT", bid=99.0, ask=101.0)
    engine._risk.monitor_tick(tick, positions=[])
    assert not engine.risk.kill_switch


@pytest.mark.asyncio
async def test_trip_breakers_api() -> None:
    engine, gateway = _engine()
    engine.flatten = AsyncMock()  # type: ignore[method-assign]
    bus = EventBus()
    app = create_app(engine, bus)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/control/breakers/trip",
            json={"detail": "api test", "flatten": True, "pause": True},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert any(b["code"] == "operator_halt" for b in body["active"])
    assert gateway.cancel_all_calls == 1
    engine.flatten.assert_awaited_once()  # type: ignore[attr-defined]
