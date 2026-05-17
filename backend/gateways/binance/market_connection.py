"""Public market-data WebSocket consumer.

Binance allows **at most 1024 streams per combined WebSocket**. Beyond that,
subscriptions fail or stall — bookTicker never arrives and startup falls back
to hundreds of REST ``/depth`` calls (slow).

We shard large universes across multiple sockets:
    - Shard 0: ``!ticker@arr`` only (isolates the heavy all-market fan-out)
    - Further shards: symbol chunks (bookTicker, aggTrade, depth) without ``!ticker@arr``.

Each shard uses a dedicated ingest queue so the socket reader returns quickly
and WebSocket keepalive pings are not starved by slow MD handlers.
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
    MarketReconnectCallback,
    QuoteVolume24hCallback,
    TickCallback,
    TradeCallback,
)

logger = logging.getLogger(__name__)

# Binance Futures combined stream hard limit (streams count, not URL chars).
_MAX_STREAMS_PER_CONNECTION = 1024
# Many proxies / load balancers reject very long GET URLs; stay conservative.
_MAX_STREAM_URL_CHARS = 3800
# Yield the event loop between ticker-array batches so shard pings are answered.
_TICKER_ARR_DISPATCH_CHUNK = 16
# Sentinel to stop the per-shard processor when the reader exits.
_QUEUE_STOP = object()


def _stream_parts_for_symbols(symbols: list[str]) -> list[str]:
    return [
        f"{sym}@{kind}"
        for sym in symbols
        for kind in ("bookTicker", "aggTrade", "depth@100ms")
    ]


def _joined_stream_url_len(parts: list[str]) -> int:
    return len("/".join(parts))


def _shard_symbols_for_streams(symbols: list[str]) -> list[tuple[list[str], bool]]:
    """Split ``symbols`` into chunks that fit under stream-count and URL limits.

    Each symbol adds three streams (bookTicker, aggTrade, depth). ``!ticker@arr``
    lives on its own socket so a 1 Hz all-market array cannot starve depth pings.

    Returns list of ``(symbol_chunk, include_ticker_arr)``.
    """
    if not symbols:
        return []

    sym_cap = _MAX_STREAMS_PER_CONNECTION // 3
    chunks: list[tuple[list[str], bool]] = [([], True)]
    chunk: list[str] = []
    chunk_parts: list[str] = []
    for sym in symbols:
        sym_parts = _stream_parts_for_symbols([sym])
        next_parts = chunk_parts + sym_parts
        over_streams = len(next_parts) > sym_cap
        over_url = _joined_stream_url_len(next_parts) > _MAX_STREAM_URL_CHARS
        if chunk and (over_streams or over_url):
            chunks.append((chunk, False))
            chunk = []
            chunk_parts = []
            next_parts = sym_parts
        chunk.append(sym)
        chunk_parts = next_parts
    if chunk:
        chunks.append((chunk, False))
    return chunks


class MarketConnection:
    """Handles the public Futures market-data stream (possibly sharded)."""

    def __init__(
        self,
        ws_base: str,
        *,
        ping_interval: float = 20.0,
        ping_timeout: float = 180.0,
        shard_queue_size: int = 4096,
    ) -> None:
        self._ws_base = ws_base.rstrip("/")
        self._ping_interval = max(1.0, float(ping_interval))
        self._ping_timeout = max(self._ping_interval + 1.0, float(ping_timeout))
        self._shard_queue_size = max(256, int(shard_queue_size))
        self._symbols: list[str] = []
        self._on_tick: TickCallback | None = None
        self._on_depth: DepthCallback | None = None
        self._on_trade: TradeCallback | None = None
        self._on_quote_vol: QuoteVolume24hCallback | None = None
        self._on_reconnect: MarketReconnectCallback | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._child_tasks: set[asyncio.Task[None]] = set()
        self._stop = asyncio.Event()
        self._shard_had_session: dict[int, bool] = {}
        self._ticker_arr_tasks: set[asyncio.Task[None]] = set()

    def _spawn(self, coro, *, name: str | None = None) -> asyncio.Task[None]:
        """Track shard reader/processor tasks so ``stop()`` can await them."""

        task: asyncio.Task[None] = asyncio.create_task(coro, name=name)
        self._child_tasks.add(task)
        task.add_done_callback(self._child_tasks.discard)
        return task

    async def start(
        self,
        symbols: list[str],
        on_tick: TickCallback,
        on_depth: DepthCallback,
        on_trade: TradeCallback,
        *,
        on_quote_volume_24h: QuoteVolume24hCallback | None = None,
        on_reconnect: MarketReconnectCallback | None = None,
    ) -> None:
        # Idempotent: a retried engine.start() must not leave orphan shard tasks
        # (duplicate "shard N connecting" / parallel reconnect resyncs).
        await self.stop()

        self._symbols = [s.lower() for s in symbols]
        self._wanted = set(self._symbols)
        self._on_tick = on_tick
        self._on_depth = on_depth
        self._on_trade = on_trade
        self._on_quote_vol = on_quote_volume_24h
        self._on_reconnect = on_reconnect
        self._stop.clear()
        self._shard_had_session.clear()

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
        for task in list(self._child_tasks):
            task.cancel()
        for task in list(self._child_tasks):
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._child_tasks.clear()
        for task in list(self._ticker_arr_tasks):
            task.cancel()
        for task in list(self._ticker_arr_tasks):
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._ticker_arr_tasks.clear()

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
        stream_parts.extend(_stream_parts_for_symbols(symbols))
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
            ingest: asyncio.Queue[str | object] = asyncio.Queue(
                maxsize=self._shard_queue_size,
            )
            processor = self._spawn(
                self._process_shard_queue(shard_id, ingest),
                name=f"binance-market-ws-{shard_id}-processor",
            )
            try:
                logger.info(
                    "market_ws shard %d connecting (%d streams, %d symbols%s)",
                    shard_id,
                    n_streams,
                    len(symbols),
                    ", !ticker@arr" if include_ticker_arr else "",
                )
                async with websockets.connect(
                    url,
                    ping_interval=self._ping_interval,
                    ping_timeout=self._ping_timeout,
                    close_timeout=10,
                    max_size=2**22,
                ) as ws:
                    backoff = 1.0
                    if self._shard_had_session.get(shard_id) and self._on_reconnect is not None:
                        shard_syms = [s.upper() for s in symbols]
                        self._spawn(
                            self._run_reconnect_handler(shard_id, shard_syms),
                            name=f"binance-market-ws-{shard_id}-resync",
                        )
                    self._shard_had_session[shard_id] = True
                    reader = self._spawn(
                        self._read_into_queue(ws, ingest),
                        name=f"binance-market-ws-{shard_id}-reader",
                    )
                    try:
                        await reader
                    finally:
                        ingest.put_nowait(_QUEUE_STOP)
                        await processor
            except (ConnectionClosed, OSError) as exc:
                logger.warning(
                    "market_ws shard %d disconnected: %s; retry in %.1fs",
                    shard_id,
                    exc,
                    backoff,
                )
                if not processor.done():
                    processor.cancel()
                    try:
                        await processor
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _read_into_queue(
        self,
        ws: websockets.WebSocketClientProtocol,
        ingest: asyncio.Queue[str | object],
    ) -> None:
        """Drain the socket into ``ingest`` so keepalive pings stay timely."""
        async for raw in ws:
            if self._stop.is_set():
                return
            await ingest.put(raw)

    async def _process_shard_queue(
        self,
        shard_id: int,
        ingest: asyncio.Queue[str | object],
    ) -> None:
        while True:
            item = await ingest.get()
            try:
                if item is _QUEUE_STOP:
                    return
                if not isinstance(item, str):
                    continue
                try:
                    message = json.loads(item)
                except json.JSONDecodeError:
                    logger.debug("market_ws non-json frame")
                    continue

                stream: str | None = message.get("stream")
                data = message.get("data")
                if stream == "!ticker@arr" and isinstance(data, list):
                    self._schedule_ticker_arr(shard_id, data)
                    continue

                if stream is None or data is None or not isinstance(data, dict):
                    continue

                try:
                    await self._dispatch(stream, data)
                except Exception:  # noqa: BLE001 -- never let a handler kill the socket
                    logger.exception("market_ws handler raised")
            finally:
                ingest.task_done()

    def _schedule_ticker_arr(self, shard_id: int, rows: list[dict]) -> None:
        task = asyncio.create_task(
            self._dispatch_ticker_arr(shard_id, rows),
            name=f"binance-market-ws-{shard_id}-ticker-arr",
        )
        self._ticker_arr_tasks.add(task)
        task.add_done_callback(self._ticker_arr_tasks.discard)

    async def _run_reconnect_handler(self, shard_id: int, symbols: list[str]) -> None:
        if self._on_reconnect is None:
            return
        try:
            await self._on_reconnect(symbols)
        except Exception:  # noqa: BLE001
            logger.exception(
                "market_ws shard %d reconnect handler raised",
                shard_id,
            )

    async def _dispatch_ticker_arr(self, shard_id: int, rows: list[dict]) -> None:
        """Fan out 24h quote volume from the all-markets ticker array."""
        if self._on_quote_vol is None:
            return
        tasks: list[Awaitable[None]] = []
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
            tasks.append(self._on_quote_vol(sym.upper(), qv))
        if not tasks:
            return
        try:
            for i in range(0, len(tasks), _TICKER_ARR_DISPATCH_CHUNK):
                await asyncio.gather(*tasks[i : i + _TICKER_ARR_DISPATCH_CHUNK])
                await asyncio.sleep(0)
        except Exception:  # noqa: BLE001
            logger.exception("market_ws shard %d ticker@arr handler raised", shard_id)

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
    pu_raw = data.get("pu")
    prev_final: int | None
    if pu_raw is None:
        prev_final = None
    else:
        try:
            prev_final = int(pu_raw)
        except (TypeError, ValueError):
            prev_final = None
    return DepthDiff(
        symbol=symbol,
        bids=[(float(p), float(q)) for p, q in data.get("b", [])],
        asks=[(float(p), float(q)) for p, q in data.get("a", [])],
        first_update_id=int(data.get("U", 0)),
        final_update_id=int(data.get("u", 0)),
        prev_final_update_id=prev_final,
    )


# Re-exported so type checkers see them in __all__-like usage.
TickHandler = Callable[[Tick], Awaitable[None]]
