"""Public market-data WebSocket consumer.

Maintains a single combined-stream subscription per process. For each
symbol we subscribe to:
    - <sym>@bookTicker  (top-of-book ticks, ~10/sec under load)
    - <sym>@aggTrade    (aggregated trades, used by the trade tape)
    - <sym>@depth@100ms (L2 diffs, applied to the local order book)

The connection auto-reconnects with exponential backoff. Subscribers
are notified via callbacks supplied at `start()` time.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

import websockets
from websockets.exceptions import ConnectionClosed

from common.enums import Side
from common.types import TapeTrade, Tick

from ..gateway_interface import DepthCallback, DepthDiff, TickCallback, TradeCallback

logger = logging.getLogger(__name__)


class MarketConnection:
    """Handles the public Futures market-data stream."""

    def __init__(self, ws_base: str) -> None:
        self._ws_base = ws_base.rstrip("/")
        self._symbols: list[str] = []
        self._on_tick: TickCallback | None = None
        self._on_depth: DepthCallback | None = None
        self._on_trade: TradeCallback | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def start(
        self,
        symbols: list[str],
        on_tick: TickCallback,
        on_depth: DepthCallback,
        on_trade: TradeCallback,
    ) -> None:
        self._symbols = [s.lower() for s in symbols]
        self._on_tick = on_tick
        self._on_depth = on_depth
        self._on_trade = on_trade
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="binance-market-ws")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    async def _run(self) -> None:
        # Combined stream lets us multiplex many symbols on one socket and
        # avoids hitting the 5/sec subscribe rate limit.
        streams = "/".join(
            f"{sym}@{kind}"
            for sym in self._symbols
            for kind in ("bookTicker", "aggTrade", "depth@100ms")
        )
        url = f"{self._ws_base}/stream?streams={streams}"

        backoff = 1.0
        while not self._stop.is_set():
            try:
                logger.info("market_ws connecting (%d streams)", len(self._symbols) * 3)
                async with websockets.connect(url, ping_interval=15, ping_timeout=20) as ws:
                    backoff = 1.0
                    await self._read_loop(ws)
            except (ConnectionClosed, OSError) as exc:
                logger.warning("market_ws disconnected: %s; retry in %.1fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _read_loop(self, ws: websockets.WebSocketClientProtocol) -> None:
        async for raw in ws:
            if self._stop.is_set():
                return
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                logger.debug("market_ws non-json frame")
                continue

            stream: str | None = message.get("stream")
            data: dict | None = message.get("data")
            if stream is None or data is None:
                continue

            try:
                await self._dispatch(stream, data)
            except Exception:  # noqa: BLE001 -- never let a handler kill the socket
                logger.exception("market_ws handler raised")

    async def _dispatch(self, stream: str, data: dict) -> None:
        # Stream names look like "btcusdt@bookTicker". Split once on '@'.
        symbol_part, _, kind = stream.partition("@")
        symbol = symbol_part.upper()

        if kind.startswith("bookTicker") and self._on_tick is not None:
            await self._on_tick(_parse_book_ticker(symbol, data))
        elif kind.startswith("aggTrade") and self._on_trade is not None:
            await self._on_trade(_parse_agg_trade(symbol, data))
        elif kind.startswith("depth") and self._on_depth is not None:
            await self._on_depth(_parse_depth_diff(symbol, data))


def _parse_book_ticker(symbol: str, data: dict) -> Tick:
    # bookTicker payload uses single-letter keys: b/B/a/A for bid/qty/ask/qty.
    return Tick(
        symbol=symbol,
        bid=float(data["b"]),
        ask=float(data["a"]),
        ts=float(data.get("E", 0)) / 1000.0 or None,  # event time in ms
    )


def _parse_agg_trade(symbol: str, data: dict) -> TapeTrade:
    # `m` is true when the buyer is the maker -> the taker is the seller -> sell-init.
    aggressor = Side.SELL if data.get("m", False) else Side.BUY
    return TapeTrade(
        symbol=symbol,
        price=float(data["p"]),
        qty=float(data["q"]),
        aggressor=aggressor,
        ts=float(data.get("T", data.get("E", 0))) / 1000.0,
    )


def _parse_depth_diff(symbol: str, data: dict) -> DepthDiff:
    return DepthDiff(
        symbol=symbol,
        bids=[(float(p), float(q)) for p, q in data.get("b", [])],
        asks=[(float(p), float(q)) for p, q in data.get("a", [])],
        first_update_id=int(data.get("U", 0)),
        final_update_id=int(data.get("u", 0)),
    )


# Re-exported so type checkers see them in __all__-like usage.
TickHandler = Callable[[Tick], Awaitable[None]]
