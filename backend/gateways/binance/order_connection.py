"""Order placement + user-data stream.

REST is used for placing/cancelling orders (Binance does not yet expose
a websocket order entry on Futures Testnet). The user-data stream
delivers ACCOUNT_UPDATE and ORDER_TRADE_UPDATE events which we translate
into `Fill` and `ChildOrder` updates for the engine.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from common.enums import OrderStatus, OrderType, Side
from common.types import ChildOrder, Fill

from ..gateway_interface import FillCallback, OrderUpdateCallback
from .rest_client import BinanceRestClient

logger = logging.getLogger(__name__)

# Binance expires listenKey after 60 minutes; we ping at 30 to be safe.
_LISTEN_KEY_KEEPALIVE_SEC = 30 * 60


class OrderConnection:
    """REST + user-data stream for order management."""

    def __init__(self, rest: BinanceRestClient, ws_base: str) -> None:
        self._rest = rest
        self._ws_base = ws_base.rstrip("/")
        self._listen_key: str | None = None
        self._on_fill: FillCallback | None = None
        self._on_order: OrderUpdateCallback | None = None
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []

    async def start(self, on_fill: FillCallback, on_order: OrderUpdateCallback) -> None:
        self._on_fill = on_fill
        self._on_order = on_order
        self._stop.clear()
        self._listen_key = await self._rest.listen_key()
        self._tasks = [
            asyncio.create_task(self._user_data_loop(), name="binance-user-ws"),
            asyncio.create_task(self._keepalive_loop(), name="binance-listenkey-keepalive"),
        ]

    async def stop(self) -> None:
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._tasks.clear()

    # --- Order management ---

    async def place_order(self, order: ChildOrder) -> ChildOrder:
        """Send a new order to the exchange.

        We always pass `newClientOrderId` so we can correlate the user-data
        stream's ORDER_TRADE_UPDATE back to our internal `ChildOrder.id`.
        """
        params: dict[str, Any] = {
            "symbol": order.symbol,
            "side": order.side.value.upper(),
            "type": order.order_type.value,
            "quantity": _fmt_qty(order.qty),
            "newClientOrderId": order.id,
        }
        if order.order_type is OrderType.LIMIT:
            if order.price is None:
                raise ValueError(f"LIMIT order {order.id} missing price")
            params["price"] = _fmt_price(order.price)
            # GTX = post-only; we use GTC so child orders cross when needed.
            params["timeInForce"] = "GTC"

        response = await self._rest.new_order(**params)
        order.venue_order_id = str(response.get("orderId", ""))
        order.status = _map_status(response.get("status", "NEW"))
        return order

    async def cancel_order(self, symbol: str, client_order_id: str) -> None:
        try:
            await self._rest.cancel_order(symbol=symbol, origClientOrderId=client_order_id)
        except Exception:  # noqa: BLE001
            # -2011 = unknown order. Treat as benign because the order may
            # have just been filled or already cancelled.
            logger.exception("cancel failed for %s/%s", symbol, client_order_id)

    # --- User-data stream ---

    async def _keepalive_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.sleep(_LISTEN_KEY_KEEPALIVE_SEC)
                if self._stop.is_set():
                    return
                await self._rest.keepalive_listen_key()
                logger.debug("listenKey keepalive ok")
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("listenKey keepalive failed; refreshing")
                try:
                    self._listen_key = await self._rest.listen_key()
                except Exception:  # noqa: BLE001
                    logger.exception("listenKey refresh failed")

    async def _user_data_loop(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            if self._listen_key is None:
                await asyncio.sleep(1.0)
                continue
            url = f"{self._ws_base}/ws/{self._listen_key}"
            try:
                logger.info("user_ws connecting")
                async with websockets.connect(url, ping_interval=15, ping_timeout=20) as ws:
                    backoff = 1.0
                    await self._read_loop(ws)
            except (ConnectionClosed, OSError) as exc:
                logger.warning("user_ws disconnected: %s; retry in %.1fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _read_loop(self, ws: websockets.WebSocketClientProtocol) -> None:
        async for raw in ws:
            if self._stop.is_set():
                return
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue

            try:
                await self._dispatch(event)
            except Exception:  # noqa: BLE001
                logger.exception("user_ws handler raised")

    async def _dispatch(self, event: dict) -> None:
        et = event.get("e")
        if et == "ORDER_TRADE_UPDATE":
            await self._handle_order_trade_update(event["o"])
        # ACCOUNT_UPDATE and other events aren't needed by the engine; the
        # PositionTracker rebuilds state from fills.

    async def _handle_order_trade_update(self, order: dict) -> None:
        # Fields documented at
        # https://binance-docs.github.io/apidocs/futures/en/#event-order-update
        client_id = str(order.get("c", ""))
        if not client_id:
            return

        status = _map_status(order.get("X", "NEW"))
        last_filled = float(order.get("l", 0.0))   # qty of THIS execution
        last_price = float(order.get("L", 0.0))    # price of THIS execution
        cum_filled = float(order.get("z", 0.0))    # cumulative filled qty
        avg_price = float(order.get("ap", 0.0))    # cumulative avg fill price

        update = ChildOrder(
            id=client_id,
            parent_id="",  # OMS resolves parent by client_id; payload doesn't carry it
            symbol=str(order.get("s", "")),
            side=Side(order.get("S", "BUY").lower()),
            qty=float(order.get("q", 0.0)),
            price=_safe_float(order.get("p")),
            order_type=OrderType(order.get("o", "LIMIT")),
            status=status,
            filled_qty=cum_filled,
            avg_fill_price=avg_price,
            venue_order_id=str(order.get("i", "")),
        )
        if self._on_order is not None:
            await self._on_order(update)

        if last_filled > 0 and self._on_fill is not None:
            fill = Fill(
                child_id=client_id,
                parent_id=None,
                symbol=update.symbol,
                side=update.side,
                qty=last_filled,
                price=last_price,
                fee=float(order.get("n", 0.0)),
                fee_asset=str(order.get("N", "")),
            )
            await self._on_fill(fill)


def _safe_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _fmt_qty(qty: float) -> str:
    # Binance rejects scientific notation; use plain fixed-point with up
    # to 8 decimals (engine should already round to symbol step size).
    return f"{qty:.8f}".rstrip("0").rstrip(".") or "0"


def _fmt_price(price: float) -> str:
    return f"{price:.8f}".rstrip("0").rstrip(".") or "0"


def _map_status(raw: str) -> OrderStatus:
    mapping = {
        "NEW": OrderStatus.ACK,
        "PARTIALLY_FILLED": OrderStatus.PARTIAL,
        "FILLED": OrderStatus.FILLED,
        "CANCELED": OrderStatus.CANCELLED,
        "REJECTED": OrderStatus.REJECTED,
        "EXPIRED": OrderStatus.CANCELLED,
    }
    return mapping.get(raw.upper(), OrderStatus.ACK)
