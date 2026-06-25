"""Interactive Brokers gateway connector scaffold.

Purpose
-------
This file documents the adapter shape needed for a non-Binance venue without
changing engine code. It is registered behind ``VENUE=ibkr`` so factory wiring
and settings can be tested, but trading methods intentionally raise
`NotImplementedError` until the IB API calls are implemented. Bringing IBKR
online is mechanical:

    1. ``pip install ib_async`` (or ``ib_insync``, the older name).
    2. Replace each `NotImplementedError` body with the matching IB call.
    3. Run TWS or IB Gateway locally — port 7497 for paper, 7496 for live.

Mapping cheatsheet (engine concept -> IB API)
---------------------------------------------
* ``connect`` / ``disconnect``      -> ``IB().connectAsync(host, port, clientId)`` / ``disconnect()``
* market data ``Tick``              -> ``reqMktData(contract)`` + the ``pendingTickersEvent``
* market data ``DepthDiff``         -> ``reqMktDepth(contract)`` + the ``updateMktDepthEvent``
                                       (IBKR L2 requires a Level II data subscription)
* trade tape ``TapeTrade``          -> ``reqTickByTickData(contract, "AllLast")``;
                                       NB: IBKR does *not* send an aggressor flag.
                                       Infer side by comparing the tick price to the
                                       prevailing bid/ask snapshot.
* user data ``Fill``                -> ``execDetailsEvent`` / ``commissionReportEvent``
* user data ``ChildOrder`` updates  -> ``orderStatusEvent``
* ``place_order``                   -> ``placeOrder(contract, ib_order)`` then `await trade.fillEvent`
* ``cancel_order``                  -> ``cancelOrder(ib_order)`` (look up by clientOrderId)
* ``fetch_positions``               -> ``positions()`` then translate via the symbol map
* ``fetch_balance``                 -> ``accountValues(account=settings.ibkr_account)`` ->
                                       pick ``"NetLiquidation"`` in `settings.base_currency`
* ``book_snapshot``                 -> snapshot via `reqMktDepth`; build the dict the
                                       engine expects with ``{"lastUpdateId": <seq>,
                                       "bids": [[p, q], ...], "asks": [...]}``.

The engine treats every gateway as a black box, so as long as the
returned `Tick` / `DepthDiff` / `Fill` / `ChildOrder` carry the right
fields, no other backend module needs to know IBKR exists.
"""

from __future__ import annotations

import logging

from common.config import Settings
from common.types import ChildOrder, Kline, Position

from ..gateway_interface import (
    DepthCallback,
    FillCallback,
    GatewayInterface,
    OrderUpdateCallback,
    TickCallback,
    TradeCallback,
)

logger = logging.getLogger(__name__)


_NOT_IMPLEMENTED_MSG = (
    "IBKRGateway is a connector scaffold. Install `ib_async` and fill in the body "
    "of this method (see the module-level mapping cheatsheet)."
)


class IBKRGateway(GatewayInterface):
    """Connector scaffold for Interactive Brokers TWS / IB Gateway.

    Holds the configured connection coordinates so a real implementation
    can pick them up unchanged. The actual `ib_async` client is
    deliberately not constructed here; that's left for whoever wires
    the real methods so this file imports zero IB-specific symbols.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._host = settings.ibkr_host
        self._port = settings.ibkr_port
        self._client_id = settings.ibkr_client_id
        self._account = settings.ibkr_account
        logger.info(
            "ibkr gateway configured: host=%s port=%d client_id=%d account=%s",
            self._host,
            self._port,
            self._client_id,
            self._account or "<default>",
        )

    # --- Lifecycle ---

    async def connect(self) -> None:
        # IBKR note: ib = IB(); await ib.connectAsync(
        #     self._host, self._port, clientId=self._client_id
        # )
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    async def disconnect(self) -> None:
        # IBKR note: self._ib.disconnect()
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    # --- Market data ---

    async def subscribe_market_data(
        self,
        symbols: list[str],
        on_tick: TickCallback,
        on_depth: DepthCallback,
        on_trade: TradeCallback,
        *,
        on_quote_volume_24h=None,
        on_reconnect=None,
    ) -> None:
        # IBKR note: build IB Contract objects from `symbols`, call reqMktData /
        # reqMktDepth / reqTickByTickData, attach ib.pendingTickersEvent,
        # ib.updateMktDepthEvent, ib.tickByTickAllLastEvent handlers and
        # translate each callback into the engine-native Tick / DepthDiff /
        # TapeTrade dataclasses before forwarding.
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    # --- User data ---

    async def subscribe_user_data(
        self,
        on_fill: FillCallback,
        on_order_update: OrderUpdateCallback,
        on_account_update=None,
    ) -> None:
        # IBKR note: ib.execDetailsEvent += <translate to Fill> and forward
        # via on_fill; ib.orderStatusEvent += <translate to ChildOrder>
        # and forward via on_order_update.
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    # --- Order management ---

    async def place_order(self, order: ChildOrder) -> ChildOrder:
        # IBKR note: build the IB Order (LMT vs MKT, qty, action), look up the
        # IB Contract for `order.symbol`, call ib.placeOrder, populate
        # `order.venue_order_id` from the trade.order.permId / orderId.
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    async def cancel_order(self, symbol: str, client_order_id: str) -> None:
        # IBKR note: locate the open IB Trade by clientOrderId in self._open_trades
        # and call ib.cancelOrder(trade.order).
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    # --- Account ---

    async def fetch_positions(self) -> list[Position]:
        # IBKR note: for p in ib.positions(): yield Position(symbol=..., qty=p.position, ...)
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    async def fetch_balance(self) -> float:
        # IBKR note: pick the NetLiquidation row from ib.accountValues(...) in
        # settings.base_currency and cast to float.
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    # --- Reference data ---

    async def book_snapshot(self, symbol: str, depth: int = 100) -> dict:
        # IBKR note: take a one-shot snapshot via reqMktDepth and shape the
        # response into {"lastUpdateId": <seq>, "bids": [[p, q], ...],
        # "asks": [...]} so the engine's incremental loop can consume it
        # without caring it came from IBKR.
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    async def klines(self, symbol: str, interval: str, limit: int = 200) -> list[Kline]:
        # IBKR note: ib.reqHistoricalData(contract, endDateTime="", durationStr=...,
        # barSizeSetting=<map interval>, whatToShow="MIDPOINT") then translate
        # each bar into a Kline (date -> epoch seconds, open/high/low/close
        # straight, volume in base units).
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)
