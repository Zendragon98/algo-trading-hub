"""PreTradeValidator gates."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("BINANCE_API_KEY", "test")
os.environ.setdefault("BINANCE_API_SECRET", "test")

from common.config import Settings  # noqa: E402
from common.enums import Side  # noqa: E402
from common.events import EventBus  # noqa: E402
from common.types import Signal  # noqa: E402
from engine.portfolio.portfolio import Portfolio  # noqa: E402
from engine.position.position_tracker import PositionTracker  # noqa: E402
from engine.risk.circuit_breaker import CircuitBreaker  # noqa: E402
from engine.risk.pnl_tracker import PnLTracker  # noqa: E402
from engine.risk.pretrade_validator import PreTradeValidator  # noqa: E402
from engine.risk.risk_manager import RiskManager  # noqa: E402
from engine.risk.stop_loss import StopLossMonitor  # noqa: E402
from engine.risk.limits import Limits  # noqa: E402
from gateways.gateway_interface import GatewayInterface, SymbolFilters  # noqa: E402
from common.types import Kline, Position  # noqa: E402


class _Gw(GatewayInterface):
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def subscribe_market_data(self, *a, **kw) -> None: ...
    async def subscribe_user_data(self, *a, **kw) -> None: ...
    async def place_order(self, order): ...
    async def cancel_order(self, symbol: str, client_order_id: str) -> None: ...
    async def fetch_positions(self) -> list[Position]:
        return []
    async def fetch_balance(self) -> float:
        return 10_000.0
    async def book_snapshot(self, symbol: str, depth: int = 100) -> dict:
        return {"lastUpdateId": 0, "bids": [], "asks": []}
    async def klines(self, symbol: str, interval: str, limit: int = 200) -> list[Kline]:
        return []

    def get_symbol_filters(self, symbol: str) -> SymbolFilters | None:
        return SymbolFilters(symbol=symbol, min_qty=0.001, step_size=0.001)


def _validator() -> PreTradeValidator:
    settings = Settings(
        binance_api_key="x",
        binance_api_secret="y",
        max_risk_pct=1.0,
        max_gross_notional=1_000_000.0,
        max_order_notional_usd=100.0,
        signal_dedup_ttl_sec=5.0,
    )
    bus = EventBus()
    positions = PositionTracker(bus=bus)
    portfolio = Portfolio(bus=bus, position_tracker=positions, base_currency="USDT")
    portfolio.seed_cash(10_000.0)
    pnl = PnLTracker(portfolio)
    risk = RiskManager(
        settings=settings,
        portfolio=portfolio,
        pnl=pnl,
        stop_monitor=StopLossMonitor(limits=Limits.from_settings(settings)),
        breaker=CircuitBreaker(bus=bus),
    )
    return PreTradeValidator(
        settings=settings,
        risk=risk,
        gateway=_Gw(),
        portfolio=portfolio,
        positions=positions,
    )


def test_fat_finger_notional_veto() -> None:
    v = _validator()
    sig = Signal(symbol="BTCUSDT", side=Side.BUY, qty=2.0, reason="test")
    result = v.validate_single(sig, mid=1000.0, tick_ts=None, spread_bps=5.0)
    assert not result.approved
    assert "fat_finger" in result.reason


def test_signal_dedup_blocks_repeat() -> None:
    v = _validator()
    sig = Signal(symbol="ETHUSDT", side=Side.BUY, qty=0.01, reason="entry")
    first = v.validate_single(sig, mid=100.0, tick_ts=None, spread_bps=5.0)
    assert first.approved
    second = v.validate_single(sig, mid=100.0, tick_ts=None, spread_bps=5.0)
    assert not second.approved
    assert second.reason == "signal_dedup"
