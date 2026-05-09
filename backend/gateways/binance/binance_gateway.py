"""Composes market + order + account connections behind GatewayInterface."""

from __future__ import annotations

import logging

from common.config import Settings
from common.types import ChildOrder, Position

from ..gateway_interface import (
    DepthCallback,
    FillCallback,
    GatewayInterface,
    OrderUpdateCallback,
    TickCallback,
    TradeCallback,
)
from .account_connection import AccountConnection
from .market_connection import MarketConnection
from .order_connection import OrderConnection
from .rest_client import BinanceRestClient

logger = logging.getLogger(__name__)


class BinanceGateway(GatewayInterface):
    """Concrete venue adapter for Binance USDT-M Futures Testnet."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._rest = BinanceRestClient(
            base_url=settings.binance_rest_base,
            api_key=settings.binance_api_key,
            api_secret=settings.binance_api_secret,
        )
        self._market = MarketConnection(ws_base=settings.binance_ws_base)
        self._orders = OrderConnection(rest=self._rest, ws_base=settings.binance_ws_base)
        self._account = AccountConnection(rest=self._rest, base_currency=settings.base_currency)

    async def connect(self) -> None:
        # Sanity check — also surfaces bad credentials early instead of mid-trade.
        try:
            ts = await self._rest.server_time()
        except Exception:
            logger.exception("failed to reach Binance Futures Testnet REST")
            raise
        logger.info("binance gateway ready (server_time=%d)", ts)

    async def disconnect(self) -> None:
        await self._market.stop()
        await self._orders.stop()
        await self._rest.close()

    async def subscribe_market_data(
        self,
        symbols: list[str],
        on_tick: TickCallback,
        on_depth: DepthCallback,
        on_trade: TradeCallback,
    ) -> None:
        await self._market.start(symbols, on_tick, on_depth, on_trade)

    async def subscribe_user_data(
        self,
        on_fill: FillCallback,
        on_order_update: OrderUpdateCallback,
    ) -> None:
        await self._orders.start(on_fill=on_fill, on_order=on_order_update)

    async def place_order(self, order: ChildOrder) -> ChildOrder:
        return await self._orders.place_order(order)

    async def cancel_order(self, symbol: str, client_order_id: str) -> None:
        await self._orders.cancel_order(symbol, client_order_id)

    async def fetch_positions(self) -> list[Position]:
        return await self._account.fetch_positions()

    async def fetch_balance(self) -> float:
        return await self._account.fetch_balance()

    @property
    def rest(self) -> BinanceRestClient:
        """Exposed so the analytics CLI can reuse the signed REST client."""
        return self._rest
