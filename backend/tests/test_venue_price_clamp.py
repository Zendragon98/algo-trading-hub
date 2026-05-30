"""PERCENT_PRICE limit clamp and venue-aware reduce-only pretrade."""

from __future__ import annotations

import os

os.environ.setdefault("BINANCE_API_KEY", "test")
os.environ.setdefault("BINANCE_API_SECRET", "test")

from common.config import Settings  # noqa: E402
from common.enums import Side  # noqa: E402
from common.events import EventBus  # noqa: E402
from common.types import Kline, Position, Signal  # noqa: E402
from engine.portfolio.portfolio import Portfolio  # noqa: E402
from engine.position.position_tracker import PositionTracker  # noqa: E402
from engine.risk.circuit_breaker import CircuitBreaker  # noqa: E402
from engine.risk.limits import Limits  # noqa: E402
from engine.risk.pnl_tracker import PnLTracker  # noqa: E402
from engine.risk.pretrade_validator import PreTradeValidator  # noqa: E402
from engine.risk.risk_manager import RiskManager  # noqa: E402
from engine.risk.stop_loss import StopLossMonitor  # noqa: E402
from engine.risk.venue_sizing import clamp_limit_price  # noqa: E402
from gateways.gateway_interface import GatewayInterface, SymbolFilters  # noqa: E402


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
        return SymbolFilters(
            symbol=symbol,
            tick_size=0.01,
            price_pct_up=1.05,
            price_pct_down=0.95,
        )


def test_clamp_limit_price_sell_floor() -> None:
    filt = SymbolFilters(symbol="BTCUSDT", tick_size=0.01, price_pct_down=0.95)
    out = clamp_limit_price(70_000.0, Side.SELL, 100_000.0, filt)
    assert out == 95_000.0


def test_pretrade_vetoes_reduce_only_when_venue_flat() -> None:
    bus = EventBus()
    positions = PositionTracker(bus=bus)
    positions.seed([Position(symbol="BTCUSDT", qty=0.01)])
    positions.record_venue_snapshot([], flat_symbols=["BTCUSDT"])
    settings = Settings(binance_api_key="x", binance_api_secret="y")
    portfolio = Portfolio(bus=bus, position_tracker=positions, base_currency="USDT")
    portfolio.seed_cash(10_000.0)
    risk = RiskManager(
        settings=settings,
        portfolio=portfolio,
        pnl=PnLTracker(portfolio),
        stop_monitor=StopLossMonitor(limits=Limits.from_settings(settings)),
        breaker=CircuitBreaker(bus=bus),
    )
    v = PreTradeValidator(
        settings=settings,
        risk=risk,
        gateway=_Gw(),
        portfolio=portfolio,
        positions=positions,
        venue_qty_for=positions.venue_qty,
    )
    sig = Signal(
        symbol="BTCUSDT",
        side=Side.SELL,
        qty=0.01,
        reason="test_exit",
        reduce_only=True,
    )
    result = v.validate_single(sig, 100_000.0, tick_ts=None, spread_bps=1.0)
    assert not result.approved
    assert result.reason == "reduce_only_venue_flat"
