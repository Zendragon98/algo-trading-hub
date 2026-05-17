"""Composes market + order + account connections behind GatewayInterface."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from common.config import Settings
from common.enums import OrderStatus, OrderType, Side
from common.types import ChildOrder, Kline, Position

from ..gateway_interface import (
    DepthCallback,
    FillCallback,
    GatewayInterface,
    MarketReconnectCallback,
    OrderUpdateCallback,
    QuoteVolume24hCallback,
    SymbolFilters,
    TickCallback,
    TradeCallback,
)
from .account_connection import AccountConnection
from .market_connection import MarketConnection
from .leverage_bracket_cache import (
    leverage_bracket_cache_path,
    load_leverage_bracket_cache,
    save_leverage_bracket_cache,
)
from .order_connection import OrderConnection
from .rest_client import BinanceRestClient, BinanceRestError

logger = logging.getLogger(__name__)


class BinanceGateway(GatewayInterface):
    """Concrete venue adapter for Binance USDT-M Futures Testnet."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._rest = BinanceRestClient(
            base_url=settings.binance_rest_base,
            api_key=settings.binance_api_key,
            api_secret=settings.binance_api_secret,
            min_interval_sec=max(0.0, settings.binance_rest_min_interval_ms / 1000.0),
            rest_429_default_backoff_sec=settings.binance_rest_429_default_backoff_sec,
            rest_pause_buffer_sec=settings.binance_rest_pause_buffer_sec,
        )
        self._market = MarketConnection(
            ws_base=settings.binance_ws_base,
            ping_interval=settings.market_ws_ping_interval_sec,
            ping_timeout=settings.market_ws_ping_timeout_sec,
            shard_queue_size=settings.market_ws_shard_queue_size,
        )
        self._orders = OrderConnection(
            rest=self._rest,
            ws_base=settings.binance_ws_base,
            post_only_enabled=settings.post_only_enabled,
        )
        self._account = AccountConnection(rest=self._rest, base_currency=settings.base_currency)
        self._symbol_filters: dict[str, SymbolFilters] = {}
        # Highest selectable leverage per symbol from GET /fapi/v1/leverageBracket (bracket 1).
        self._max_leverage_by_symbol: dict[str, int] = {}

    async def sync_clock(self) -> None:
        await self._rest.sync_server_time()

    def clock_skew_ms(self) -> float:
        return float(self._rest.time_offset_ms)

    async def connect(self) -> None:
        # Sanity check — also surfaces bad credentials early instead of mid-trade.
        try:
            ts = await self._rest.sync_server_time()
        except Exception:
            logger.exception("failed to reach Binance Futures Testnet REST")
            raise
        logger.info(
            "binance gateway ready (server_time=%d, offset_ms=%d)",
            ts,
            self._rest.time_offset_ms,
        )

        # Cache exchangeInfo once so the OMS can quantise qty/price and the
        # engine (VWAP, risk checks) can validate orders before sending.
        try:
            info = await self._rest.exchange_info()
            self._symbol_filters = _parse_symbol_filters(info)
            self._orders.load_exchange_info(info)
            logger.info("binance exchangeInfo loaded: %d symbols", len(self._symbol_filters))
        except Exception:
            logger.exception("failed to load binance exchangeInfo; orders may be rejected")

        cache_file = leverage_bracket_cache_path(self._settings)
        ttl = self._settings.leverage_bracket_cache_ttl_sec
        cached = load_leverage_bracket_cache(
            cache_file,
            self._settings.binance_rest_base,
            ttl,
        )
        if cached is not None:
            self._max_leverage_by_symbol = cached
            logger.info(
                "binance leverageBracket from cache: %d symbols (%s)",
                len(cached),
                cache_file,
            )
        else:
            try:
                rows = await self._rest.leverage_brackets()
                caps: dict[str, int] = {}
                for row in rows:
                    sym = str(row.get("symbol", "")).upper()
                    brackets = row.get("brackets") or []
                    cap = _max_initial_leverage_from_brackets(brackets)
                    if sym and cap is not None:
                        caps[sym] = cap
                self._max_leverage_by_symbol = caps
                logger.info("binance leverageBracket loaded from API: %d symbols", len(caps))
                save_leverage_bracket_cache(
                    cache_file,
                    self._settings.binance_rest_base,
                    caps,
                )
            except Exception:
                logger.exception("failed to load leverageBracket from API")
                stale = load_leverage_bracket_cache(
                    cache_file,
                    self._settings.binance_rest_base,
                    ttl,
                    ignore_ttl=True,
                )
                if stale is not None:
                    self._max_leverage_by_symbol = stale
                    logger.warning(
                        "using stale leverage bracket cache (%d symbols) after API failure",
                        len(stale),
                    )
                else:
                    self._max_leverage_by_symbol = {}

    def get_symbol_filters(self, symbol: str) -> SymbolFilters | None:
        return self._symbol_filters.get(symbol.upper())

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
        *,
        on_quote_volume_24h: QuoteVolume24hCallback | None = None,
        on_reconnect: MarketReconnectCallback | None = None,
    ) -> None:
        await self._market.start(
            symbols,
            on_tick,
            on_depth,
            on_trade,
            on_quote_volume_24h=on_quote_volume_24h,
            on_reconnect=on_reconnect,
        )

    async def subscribe_user_data(
        self,
        on_fill: FillCallback,
        on_order_update: OrderUpdateCallback,
        on_account_update=None,
        *,
        on_ws_connected: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        await self._orders.start(
            on_fill=on_fill,
            on_order=on_order_update,
            on_account=on_account_update,
            on_ws_connected=on_ws_connected,
        )

    async def place_order(self, order: ChildOrder) -> ChildOrder:
        return await self._orders.place_order(order)

    async def cancel_order(self, symbol: str, client_order_id: str) -> None:
        await self._orders.cancel_order(symbol, client_order_id)

    async def fetch_open_orders(self, symbol: str | None = None) -> list[ChildOrder]:
        rows = await self._rest.open_orders(symbol)
        return [_parse_open_order(row) for row in rows]

    async def fetch_order_by_client_id(self, symbol: str, client_order_id: str) -> ChildOrder | None:
        row = await self._rest.query_order(symbol, client_order_id)
        if row is None:
            return None
        return _parse_open_order(row)

    async def cancel_all_open_orders(self) -> None:
        await super().cancel_all_open_orders()

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        sym_u = symbol.upper()
        if sym_u not in self._symbol_filters:
            # Universe lists (and stale leverage caches) can mention symbols the
            # USDT-M ``exchangeInfo`` response does not serve — Binance then
            # returns ``code=-1121`` on POST ``/leverage``.
            logger.debug(
                "set_leverage skipped %s: not in exchangeInfo for this REST base",
                sym_u,
            )
            return
        requested = int(leverage)
        cap = self._max_leverage_by_symbol.get(sym_u)
        effective = min(requested, cap) if cap is not None else requested
        if cap is not None and effective < requested:
            logger.info(
                "clamping leverage for %s from %dx to %dx (venue max for symbol)",
                sym_u,
                requested,
                effective,
            )
        try:
            resp = await self._rest.set_leverage(sym_u, effective)
            logger.info(
                "leverage set %s=%sx (maxNotionalValue=%s)",
                sym_u,
                resp.get("leverage", effective),
                resp.get("maxNotionalValue", "?"),
            )
        except BinanceRestError as exc:
            if exc.code == -1121:
                logger.warning(
                    "set_leverage skipped %s: venue reports invalid futures symbol (-1121)",
                    sym_u,
                )
            else:
                logger.exception(
                    "set_leverage failed for %s (leverage=%dx)", sym_u, effective
                )
        except Exception:
            # Don't crash the engine on per-symbol leverage failures (e.g.
            # symbols with open positions reject the change). The engine
            # continues with whatever the venue currently has configured.
            logger.exception("set_leverage failed for %s (leverage=%dx)", sym_u, effective)

    async def fetch_positions(self) -> list[Position]:
        return await self._account.fetch_positions()

    async def fetch_balance(self) -> float:
        return await self._account.fetch_balance()

    async def fetch_balances(self) -> dict[str, float]:
        return await self._account.fetch_balances()

    async def fetch_balances_and_positions(self) -> tuple[dict[str, float], list[Position]]:
        return await self._account.fetch_balances_and_positions()

    async def fetch_24h_volumes(self, symbols: list[str]) -> dict[str, float]:
        """Pull 24h notional volume per symbol from ``/fapi/v1/ticker/24hr``.

        Single REST round-trip returns every symbol on the venue. We filter
        to the subscribed set and use ``quoteVolume`` (USDT or USDC notional
        traded in the last 24h) as the liquidity weight.
        """
        wanted = {s.upper() for s in symbols}
        rows = await self._rest.ticker_24hr()
        out: dict[str, float] = {}
        for row in rows:
            sym = str(row.get("symbol", "")).upper()
            if sym not in wanted:
                continue
            qv = row.get("quoteVolume")
            if qv is None:
                continue
            try:
                out[sym] = float(qv)
            except (TypeError, ValueError):
                continue
        return out

    async def book_snapshot(self, symbol: str, depth: int = 100) -> dict:
        # Binance returns {"lastUpdateId": int, "bids": [[p, q], ...], "asks": [...]}
        # which matches the GatewayInterface contract directly.
        return await self._rest.book_snapshot(symbol, limit=depth)

    async def klines(self, symbol: str, interval: str, limit: int = 200) -> list[Kline]:
        # Binance kline rows are heterogeneously typed (int timestamps mixed
        # with stringified floats); coerce here so callers always get plain
        # floats. Convert ms -> s to match the rest of the engine.
        rows = await self._rest.klines(symbol, interval=interval, limit=limit)
        out: list[Kline] = []
        for row in rows:
            out.append(
                Kline(
                    open_time=float(row[0]) / 1000.0,
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                    close_time=float(row[6]) / 1000.0,
                )
            )
        return out

    @property
    def rest(self) -> BinanceRestClient:
        """Exposed so the analytics CLI can reuse the signed REST client."""
        return self._rest


def _max_initial_leverage_from_brackets(brackets: list[Any]) -> int | None:
    """Return max selectable leverage from Binance `brackets` (tier 1 is highest)."""
    if not brackets:
        return None
    first = brackets[0]
    lev = first.get("initialLeverage")
    if lev is None:
        return None
    try:
        return int(lev)
    except (TypeError, ValueError):
        return None


def _parse_symbol_filters(info: dict[str, Any]) -> dict[str, SymbolFilters]:
    """Translate Binance ``exchangeInfo`` into a `SymbolFilters` map.

    Binance returns one entry per symbol with a ``filters`` array; the
    relevant entries here are ``LOT_SIZE`` / ``MARKET_LOT_SIZE`` (qty step
    and minimum), ``PRICE_FILTER`` (tick size), and ``MIN_NOTIONAL``
    (minimum order value in quote asset).
    """
    out: dict[str, SymbolFilters] = {}
    for sym in info.get("symbols") or []:
        symbol = str(sym.get("symbol", "")).upper()
        if not symbol:
            continue
        step_size: float | None = None
        tick_size: float | None = None
        min_qty: float | None = None
        max_qty_limit: float | None = None
        max_qty_market: float | None = None
        min_notional: float | None = None
        for f in sym.get("filters") or []:
            ft = f.get("filterType")
            if ft == "LOT_SIZE":
                step_size = _safe_float(f.get("stepSize")) or step_size
                mq = _safe_float(f.get("minQty"))
                if mq is not None:
                    min_qty = mq if min_qty is None else max(min_qty, mq)
                xq = _safe_float(f.get("maxQty"))
                if xq is not None:
                    max_qty_limit = xq
            elif ft == "MARKET_LOT_SIZE":
                step_size = _safe_float(f.get("stepSize")) or step_size
                mq = _safe_float(f.get("minQty"))
                if mq is not None:
                    min_qty = mq if min_qty is None else max(min_qty, mq)
                xq = _safe_float(f.get("maxQty"))
                if xq is not None:
                    max_qty_market = xq
            elif ft == "PRICE_FILTER":
                tick_size = _safe_float(f.get("tickSize")) or tick_size
            elif ft in ("MIN_NOTIONAL", "NOTIONAL"):
                # Binance uses different spellings across venues / versions:
                # - Futures: MIN_NOTIONAL + notional
                # - Some feeds: NOTIONAL + minNotional
                mn = (
                    _safe_float(f.get("notional"))
                    or _safe_float(f.get("minNotional"))
                    or _safe_float(f.get("minNotionalValue"))
                )
                min_notional = mn or min_notional
        caps = [c for c in (max_qty_limit, max_qty_market) if c is not None]
        max_qty = min(caps) if caps else None
        out[symbol] = SymbolFilters(
            symbol=symbol,
            step_size=step_size,
            tick_size=tick_size,
            min_qty=min_qty,
            max_qty=max_qty,
            max_qty_limit=max_qty_limit,
            max_qty_market=max_qty_market,
            min_notional=min_notional,
        )
    return out


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_open_order(row: dict[str, Any]) -> ChildOrder:
    client_id = str(row.get("clientOrderId", ""))
    return ChildOrder(
        id=client_id,
        parent_id="",
        symbol=str(row.get("symbol", "")),
        side=Side(str(row.get("side", "BUY")).lower()),
        qty=float(row.get("origQty", 0.0)),
        price=_safe_float(row.get("price")),
        order_type=OrderType(str(row.get("type", "LIMIT"))),
        status=_map_open_status(str(row.get("status", "NEW"))),
        filled_qty=float(row.get("executedQty", 0.0)),
        avg_fill_price=float(row.get("avgPrice", 0.0) or 0.0),
        venue_order_id=str(row.get("orderId", "")),
    )


def _map_open_status(raw: str) -> OrderStatus:
    mapping = {
        "NEW": OrderStatus.ACK,
        "PARTIALLY_FILLED": OrderStatus.PARTIAL,
        "FILLED": OrderStatus.FILLED,
        "CANCELED": OrderStatus.CANCELLED,
        "REJECTED": OrderStatus.REJECTED,
        "EXPIRED": OrderStatus.EXPIRED,
    }
    return mapping.get(raw.upper(), OrderStatus.ACK)
