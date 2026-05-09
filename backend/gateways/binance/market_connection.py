"""Public market-data WebSocket consumer.

Binance allows **at most 1024 streams per combined WebSocket**. Beyond that,
subscriptions fail or stall — bookTicker never arrives and startup falls back
to hundreds of REST ``/depth`` calls (slow).

We shard large universes across multiple sockets:
    - Shard 0: ``!ticker@arr`` + first chunk of symbols (bookTicker, aggTrade, depth)
    - Further shards: remaining symbol chunks only (no duplicate ``!ticker@arr``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable

import websockets
from websockets.exceptions import ConnectionClosed

from common.enums import Side
from common.types import TapeTrade, Tick

from ..gateway_interface import (
    DepthCallback,
    DepthDiff,
    QuoteVolume24hCallback,
    TickCallback,
    TradeCallback,
)

logger = logging.getLogger(__name__)

# Binance Futures combined stream hard limit (streams count, not URL chars).
_MAX_STREAMS_PER_CONNECTION = 1024


def _shard_symbols_for_streams(symbols: list[str]) -> list[tuple[list[str], bool]]:
    """Split ``symbols`` into chunks that fit under the 1024-stream cap.

    Each symbol adds three streams (bookTicker, aggTrade, depth). The first
    chunk also carries ``!ticker@arr`` (+1 stream).

    Returns list of ``(symbol_chunk, include_ticker_arr)``.
    """
    if not symbols:
        return []

    first_cap = (_MAX_STREAMS_PER_CONNECTION - 1) // 3
    rest_cap = _MAX_STREAMS_PER_CONNECTION // 3

    chunks: list[tuple[list[str], bool]] = []
    pos = 0
    # First shard: reserve one slot for !ticker@arr
    take = min(len(symbols), first_cap)
    chunks.append((symbols[pos : pos + take], True))
    pos += take

    while pos < len(symbols):
        take = min(rest_cap, len(symbols) - pos)
        chunks.append((symbols[pos : pos + take], False))
        pos += take

    return chunks


class MarketConnection:
    """Handles the public Futures market-data stream (possibly sharded)."""

    def __init__(self, ws_base: str) -> None:
        self._ws_base = ws_base.rstrip("/")
        self._symbols: list[str] = []
        self._on_tick: TickCallback | None = None
        self._on_depth: DepthCallback | None = None
        self._on_trade: TradeCallback | None = None
        self._on_quote_vol: QuoteVolume24hCallback | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._stop = asyncio.Event()

    async def start(
        self,
        symbols: list[str],
        on_tick: TickCallback,
        on_depth: DepthCallback,
        on_trade: TradeCallback,
        *,
        on_quote_volume_24h: QuoteVolume24hCallback | None = None,
    ) -> None:
        self._symbols = [s.lower() for s in symbols]
        self._wanted = set(self._symbols)
        self._on_tick = on_tick
        self._on_depth = on_depth
        self._on_trade = on_trade
        self._on_quote_vol = on_quote_volume_24h
        self._stop.clear()

        shards = _shard_symbols_for_streams(self._symbols)
        self._tasks = []
        for idx, (chunk, include_arr) in enumerate(shards):
            self._tasks.append(
                asyncio.create_task(
                    self._run_shard(shard_id=idx, symbols=chunk, include_ticker_arr=include_arr),
                    name=f"binance-market-ws-{idx}",
                ),
            )
        total_streams = sum(
            (1 if inc else 0) + len(ch) * 3 for ch, inc in shards
        )
        logger.info(
            "market_ws starting %d shard(s), ~%d total streams, %d symbols",
            len(shards),
            total_streams,
            len(self._symbols),
        )

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

    async def _run_shard(
        self,
        *,
        shard_id: int,
        symbols: list[str],
        include_ticker_arr: bool,
    ) -> None:
        stream_parts: list[str] = []
        if include_ticker_arr:
            stream_parts.append("!ticker@arr")
        stream_parts.extend(
            f"{sym}@{kind}"
            for sym in symbols
            for kind in ("bookTicker", "aggTrade", "depth@100ms")
        )
        n_streams = len(stream_parts)
        if n_streams > _MAX_STREAMS_PER_CONNECTION:
            logger.error(
                "market_ws shard %d: %d streams > limit %d — logic bug",
                shard_id,
                n_streams,
                _MAX_STREAMS_PER_CONNECTION,
            )
            return

        streams = "/".join(stream_parts)
        url = f"{self._ws_base}/stream?streams={streams}"

        backoff = 1.0
        while not self._stop.is_set():
            try:
                logger.info(
                    "market_ws shard %d connecting (%d streams, %d symbols%s)",
                    shard_id,
                    n_streams,
                    len(symbols),
                    ", !ticker@arr" if include_ticker_arr else "",
                )
                async with websockets.connect(url, ping_interval=15, ping_timeout=20) as ws:
                    backoff = 1.0
                    await self._read_loop(ws)
            except (ConnectionClosed, OSError) as exc:
                logger.warning(
                    "market_ws shard %d disconnected: %s; retry in %.1fs",
                    shard_id,
                    exc,
                    backoff,
                )
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
            data = message.get("data")
            if stream == "!ticker@arr" and isinstance(data, list):
                try:
                    await self._dispatch_ticker_arr(data)
                except Exception:  # noqa: BLE001
                    logger.exception("market_ws ticker@arr handler raised")
                continue

            if stream is None or data is None or not isinstance(data, dict):
                continue

            try:
                await self._dispatch(stream, data)
            except Exception:  # noqa: BLE001 -- never let a handler kill the socket
                logger.exception("market_ws handler raised")

    async def _dispatch_ticker_arr(self, rows: list[dict]) -> None:
        """Fan out 24h quote volume from the all-markets ticker array."""
        if self._on_quote_vol is None:
            return
        for row in rows:
            sym = str(row.get("s", "")).lower()
            if sym not in self._wanted:
                continue
            q_raw = row.get("q")
            if q_raw is None:
                continue
            try:
                qv = float(q_raw)
            except (TypeError, ValueError):
                continue
            await self._on_quote_vol(sym.upper(), qv)

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
    e_raw = data.get("E", 0)
    try:
        e_ms = float(e_raw) if e_raw is not None else 0.0
    except (TypeError, ValueError):
        e_ms = 0.0
    # Avoid `0.0 or None` (falsy) dropping ts; missing E -> receive time.
    ts = (e_ms / 1000.0) if e_ms > 0 else time.time()
    return Tick(symbol=symbol, bid=float(data["b"]), ask=float(data["a"]), ts=ts)


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
