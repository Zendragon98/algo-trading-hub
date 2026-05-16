"""Reconciler: position-qty mismatch trips MAJOR engine breach."""

from __future__ import annotations

import pytest

from common.events import EventBus
from common.types import ChildOrder, Kline, Position
from engine.core.reconciliation import Reconciler
from engine.portfolio.portfolio import Portfolio
from engine.position.position_tracker import PositionTracker
from engine.risk.circuit_breaker import BreakerScope, CircuitBreaker
from gateways.gateway_interface import GatewayInterface


class _MockGateway(GatewayInterface):
    def __init__(self, positions: list[Position]) -> None:
        self._positions = positions

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def subscribe_market_data(self, *a, **kw) -> None: ...
    async def subscribe_user_data(self, *a, **kw) -> None: ...
    async def place_order(self, order: ChildOrder) -> ChildOrder: return order
    async def cancel_order(self, symbol: str, client_order_id: str) -> None: ...

    async def fetch_positions(self) -> list[Position]:
        return self._positions

    async def fetch_balance(self) -> float:
        return 1000.0

    async def book_snapshot(self, symbol: str, depth: int = 100) -> dict:
        return {"lastUpdateId": 0, "bids": [], "asks": []}

    async def klines(self, symbol: str, interval: str, limit: int = 200) -> list[Kline]:
        return []


class _SpyGateway(_MockGateway):
    """Tracks REST reconcile calls."""

    def __init__(self, positions: list[Position]) -> None:
        super().__init__(positions)
        self.n_fetch_balances = 0
        self.n_fetch_positions = 0

    async def fetch_balances(self) -> dict[str, float]:
        self.n_fetch_balances += 1
        return {"USDT": 1000.0}

    async def fetch_positions(self) -> list[Position]:
        self.n_fetch_positions += 1
        return list(self._positions)


@pytest.mark.asyncio
async def test_authoritative_snap_callback_on_success() -> None:
    pos = Position(symbol="BTCUSDT", qty=0.5, avg_entry_price=100.0, mark_price=100.0)
    bus = EventBus()
    pt = PositionTracker(bus)
    pt.seed([pos])
    portfolio = Portfolio(bus, pt)
    portfolio.seed_cash(1000.0)
    breaker = CircuitBreaker()
    n = 0

    def bump() -> None:
        nonlocal n
        n += 1

    rec = Reconciler(
        gateway=_MockGateway([pos]),
        positions=pt, portfolio=portfolio, breaker=breaker,
        interval_sec=60.0, qty_tolerance=1e-6,
        on_authoritative_snap=bump,
    )
    await rec.reconcile_once()
    assert n == 1


@pytest.mark.asyncio
async def test_authoritative_snap_not_called_when_rest_skipped() -> None:
    pos = Position(symbol="BTCUSDT", qty=0.5, avg_entry_price=100.0, mark_price=100.0)
    bus = EventBus()
    pt = PositionTracker(bus)
    pt.seed([pos])
    portfolio = Portfolio(bus, pt)
    portfolio.seed_cash(1000.0)
    bumped: list[int] = []

    rec = Reconciler(
        gateway=_MockGateway([pos]),
        positions=pt, portfolio=portfolio, breaker=CircuitBreaker(),
        interval_sec=60.0, qty_tolerance=1e-6,
        skip_rest_poll=lambda: True,
        on_authoritative_snap=lambda: bumped.append(1),
    )
    await rec.reconcile_once()
    assert bumped == []


@pytest.mark.asyncio
async def test_reconcile_skips_rest_when_skip_poll_true() -> None:
    pos = Position(symbol="BTCUSDT", qty=0.5, avg_entry_price=100.0, mark_price=100.0)
    bus = EventBus()
    pt = PositionTracker(bus)
    pt.seed([pos])
    portfolio = Portfolio(bus, pt)
    portfolio.seed_cash(1000.0)
    gw = _SpyGateway([pos])
    rec = Reconciler(
        gateway=gw,
        positions=pt,
        portfolio=portfolio,
        breaker=CircuitBreaker(),
        interval_sec=60.0,
        qty_tolerance=1e-6,
        skip_rest_poll=lambda: True,
    )
    await rec.reconcile_once()
    assert gw.n_fetch_balances == 0
    assert gw.n_fetch_positions == 0


@pytest.mark.asyncio
async def test_no_breach_when_positions_match() -> None:
    pos = Position(symbol="BTCUSDT", qty=0.5, avg_entry_price=100.0, mark_price=100.0)
    bus = EventBus()
    pt = PositionTracker(bus)
    pt.seed([pos])
    portfolio = Portfolio(bus, pt)
    portfolio.seed_cash(1000.0)
    breaker = CircuitBreaker()
    rec = Reconciler(
        gateway=_MockGateway([pos]),
        positions=pt, portfolio=portfolio, breaker=breaker,
        interval_sec=60.0, qty_tolerance=1e-6,
    )
    await rec.reconcile_once()
    assert not breaker.is_blocked(BreakerScope.ENGINE)


@pytest.mark.asyncio
async def test_qty_mismatch_trips_major_and_heals_local() -> None:
    local = Position(symbol="BTCUSDT", qty=0.5, avg_entry_price=100.0, mark_price=100.0)
    remote = Position(symbol="BTCUSDT", qty=0.7, avg_entry_price=100.0, mark_price=100.0)
    bus = EventBus()
    pt = PositionTracker(bus)
    pt.seed([local])
    portfolio = Portfolio(bus, pt)
    portfolio.seed_cash(1000.0)
    breaker = CircuitBreaker()
    rec = Reconciler(
        gateway=_MockGateway([remote]),
        positions=pt, portfolio=portfolio, breaker=breaker,
        interval_sec=60.0, qty_tolerance=1e-6,
    )
    await rec.reconcile_once()
    assert breaker.is_blocked(BreakerScope.ENGINE)
    healed = pt.get("BTCUSDT")
    assert healed is not None
    assert abs(healed.qty - 0.7) < 1e-9


@pytest.mark.asyncio
async def test_extra_local_position_trips_major() -> None:
    local = Position(symbol="ETHUSDT", qty=0.5, avg_entry_price=100.0, mark_price=100.0)
    bus = EventBus()
    pt = PositionTracker(bus)
    pt.seed([local])
    portfolio = Portfolio(bus, pt)
    portfolio.seed_cash(1000.0)
    breaker = CircuitBreaker()
    rec = Reconciler(
        gateway=_MockGateway([]),  # venue says no positions
        positions=pt, portfolio=portfolio, breaker=breaker,
        interval_sec=60.0, qty_tolerance=1e-6,
    )
    await rec.reconcile_once()
    assert breaker.is_blocked(BreakerScope.ENGINE)
