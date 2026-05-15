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
import math
from collections.abc import Awaitable, Callable
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from common.enums import OrderStatus, OrderType, Side
from common.types import ChildOrder, Fill, Position

from ..gateway_interface import FillCallback, OrderUpdateCallback
from .rest_client import BinanceRestClient

logger = logging.getLogger(__name__)

# Binance expires listenKey after 60 minutes; we ping at 30 to be safe.
_LISTEN_KEY_KEEPALIVE_SEC = 30 * 60


class OrderConnection:
    """REST + user-data stream for order management."""

    def __init__(
        self,
        rest: BinanceRestClient,
        ws_base: str,
        *,
        post_only_enabled: bool = False,
    ) -> None:
        self._rest = rest
        self._ws_base = ws_base.rstrip("/")
        self._post_only_enabled = post_only_enabled
        self._listen_key: str | None = None
        self._on_fill: FillCallback | None = None
        self._on_order: OrderUpdateCallback | None = None
        self._on_account: Callable[[dict], Awaitable[None]] | None = None
        self._on_ws_connected: Callable[[], Awaitable[None]] | None = None
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        # symbol -> (step_size, tick_size)
        self._filters: dict[str, tuple[Decimal | None, Decimal | None]] = {}

    def load_exchange_info(self, info: dict) -> None:
        """Cache symbol lot/tick sizes from Binance exchangeInfo."""
        symbols = info.get("symbols") or []
        out: dict[str, tuple[Decimal | None, Decimal | None]] = {}
        for sym in symbols:
            symbol = str(sym.get("symbol", "")).upper()
            if not symbol:
                continue
            step: Decimal | None = None
            tick: Decimal | None = None
            for f in sym.get("filters", []) or []:
                ft = f.get("filterType")
                if ft in ("LOT_SIZE", "MARKET_LOT_SIZE"):
                    ss = f.get("stepSize")
                    if ss is not None:
                        step = Decimal(str(ss))
                elif ft == "PRICE_FILTER":
                    ts = f.get("tickSize")
                    if ts is not None:
                        tick = Decimal(str(ts))
            out[symbol] = (step, tick)
        self._filters = out

    def _quantize_qty(self, symbol: str, qty: float) -> str:
        step, _tick = self._filters.get(symbol.upper(), (None, None))
        if step is None or step <= 0:
            return _fmt_qty(qty)
        q = Decimal(str(qty))
        # floor to a multiple of stepSize
        n = (q / step).to_integral_value(rounding=ROUND_FLOOR)
        adj = n * step
        if adj <= 0:
            return "0"
        # avoid scientific notation
        return format(adj.normalize(), "f").rstrip("0").rstrip(".") or "0"

    def _quantize_price(self, symbol: str, price: float, side: Side) -> str:
        _step, tick = self._filters.get(symbol.upper(), (None, None))
        if tick is None or tick <= 0:
            return _fmt_price(price)
        p = Decimal(str(price))
        # buy: floor to tick; sell: ceil to tick
        rounding = ROUND_FLOOR if side is Side.BUY else ROUND_CEILING
        n = (p / tick).to_integral_value(rounding=rounding)
        adj = n * tick
        return format(adj.normalize(), "f").rstrip("0").rstrip(".") or "0"

    async def start(
        self,
        on_fill: FillCallback,
        on_order: OrderUpdateCallback,
        on_account=None,
        *,
        on_ws_connected: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._on_fill = on_fill
        self._on_order = on_order
        self._on_account = on_account
        self._on_ws_connected = on_ws_connected
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
        qty_str = self._quantize_qty(order.symbol, order.qty)
        if qty_str == "0":
            raise ValueError(f"order {order.id} qty rounds to 0 (symbol={order.symbol} qty={order.qty})")
        params: dict[str, Any] = {
            "symbol": order.symbol,
            "side": order.side.value.upper(),
            "type": order.order_type.value,
            "quantity": qty_str,
            "newClientOrderId": order.id,
        }
        if order.order_type is OrderType.LIMIT:
            if order.price is None:
                raise ValueError(f"LIMIT order {order.id} missing price")
            params["price"] = self._quantize_price(order.symbol, order.price, order.side)
            params["timeInForce"] = (
                "GTX" if self._post_only_enabled and not order.reduce_only else "GTC"
            )
        if order.reduce_only:
            # Binance Futures expects the literal string "true". Reduce-only
            # orders are also exempt from MIN_NOTIONAL, which is how a tiny
            # SL/TP on a sub-$50 position can still close out cleanly.
            params["reduceOnly"] = "true"

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

    def _user_ws_url(self) -> str:
        """Binance USD-M user streams use the ``/private/ws/<listenKey>`` route."""
        base = self._ws_base.rstrip("/")
        if base.endswith("/private"):
            return f"{base}/ws/{self._listen_key}"
        return f"{base}/private/ws/{self._listen_key}"

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
            url = self._user_ws_url()
            try:
                logger.info("user_ws connecting %s", url)
                async with websockets.connect(
                    url, ping_interval=15, ping_timeout=20, open_timeout=30,
                ) as ws:
                    backoff = 1.0
                    logger.info("user_ws connected")
                    if self._on_ws_connected is not None:
                        await self._on_ws_connected()
                    await self._read_loop(ws)
            except (ConnectionClosed, OSError, TimeoutError) as exc:
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
        elif et == "ACCOUNT_UPDATE":
            await self._handle_account_update(event.get("a") or {})

    async def _handle_account_update(self, payload: dict) -> None:
        if self._on_account is None:
            return

        balances = payload.get("B") or []
        positions = payload.get("P") or []

        # Wallet balance is realized PnL inclusive. For USDT-M futures,
        # `wb` is the wallet balance for the asset.
        wallet_by_asset: dict[str, float] = {}
        for b in balances:
            asset = str(b.get("a", "")).upper()
            if not asset:
                continue
            try:
                wallet_by_asset[asset] = float(b.get("wb", 0.0))
            except (TypeError, ValueError):
                continue

        out_positions: list[Position] = []
        for p in positions:
            symbol = str(p.get("s", "")).upper()
            if not symbol:
                continue
            try:
                qty = float(p.get("pa", 0.0))
            except (TypeError, ValueError):
                qty = 0.0
            # IMPORTANT: keep zero-qty rows. ACCOUNT_UPDATE only ships the
            # positions that *changed*, so ``pa=0`` means the symbol was
            # just closed (ADL, manual flatten, opposite fill). Filtering
            # it here would leave a stale long/short on the local tracker
            # forever; ``PositionTracker.apply_exchange_positions`` knows
            # to pop those rows when qty==0.
            out_positions.append(
                Position(
                    symbol=symbol,
                    qty=qty,
                    avg_entry_price=float(p.get("ep", 0.0) or 0.0),
                    mark_price=float(p.get("mp", 0.0) or 0.0),
                    realized_pnl=0.0,
                    exchange_unrealized_pnl=float(p.get("up", 0.0) or 0.0),
                )
            )

        await self._on_account({"wallet_by_asset": wallet_by_asset, "positions": out_positions})

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
            trade_id = str(order.get("t", "")) or None
            realized_pnl = _safe_float(order.get("rp"))
            fill = Fill(
                child_id=client_id,
                parent_id=None,
                symbol=update.symbol,
                side=update.side,
                qty=last_filled,
                price=last_price,
                fee=float(order.get("n", 0.0)),
                fee_asset=str(order.get("N", "")),
                trade_id=trade_id,
                realized_pnl=realized_pnl,
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
        "EXPIRED": OrderStatus.EXPIRED,
    }
    return mapping.get(raw.upper(), OrderStatus.ACK)
