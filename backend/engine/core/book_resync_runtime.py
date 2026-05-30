"""L2 book resync (gap, reconnect, bulk) — keeps orchestration out of ``engine.py``."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from common.enums import EngineStatus, EventType
from common.events import Event

if TYPE_CHECKING:
    from .engine import Engine, StartupProgress

logger = logging.getLogger(__name__)

# Only strategy hot-swaps block the 1 Hz evaluation loop. Reconnect/gap resyncs
# run on live WS-fed books; blocking every strategy on a flaky REST snapshot
# caused multi-hour trading freezes in production.
_STRATEGY_BLOCKING_REASONS = frozenset({"strategy_swap"})


def schedule_gap_resync(engine: Engine) -> None:
    task = engine._gap_resync_task
    if task is not None and not task.done():
        return
    engine._gap_resync_task = asyncio.create_task(
        drain_gap_resync(engine),
        name="engine-gap-resync",
    )


async def drain_gap_resync(engine: Engine) -> None:
    await asyncio.sleep(0)
    async with engine._gap_resync_lock:
        if not engine._gap_resync_pending:
            return
        batch = sorted(engine._gap_resync_pending)
        engine._gap_resync_pending.clear()
    concurrency = book_resync_concurrency(engine, "gap")

    async def _one(symbol: str) -> None:
        if symbol in engine._bulk_resync_symbols:
            return
        engine._resnapshot_inflight.add(symbol)
        try:
            await snapshot_book_timed(engine, symbol, reason="gap")
        finally:
            engine._resnapshot_inflight.discard(symbol)

    await run_book_resync_workers(
        engine,
        batch,
        concurrency=concurrency,
        worker=_one,
    )


async def on_market_ws_reconnect(engine: Engine, symbols: list[str]) -> None:
    """REST-resync L2 books after a public market WebSocket reconnect."""
    engine._reconnect_resync_pending.update(s.upper() for s in symbols)
    task = engine._reconnect_resync_debounce_task
    if task is not None and not task.done():
        return
    engine._reconnect_resync_debounce_task = asyncio.create_task(
        flush_reconnect_resync(engine),
        name="engine-market-ws-reconnect-resync",
    )


async def flush_reconnect_resync(engine: Engine) -> None:
    delay = max(
        0.0,
        float(getattr(engine._settings, "market_ws_reconnect_resync_delay_sec", 3.0)),
    )
    if delay > 0:
        await asyncio.sleep(delay)
    async with engine._reconnect_resync_lock:
        while engine._reconnect_resync_pending:
            batch = sorted(engine._reconnect_resync_pending)
            engine._reconnect_resync_pending.clear()
            await resync_symbol_books(engine, batch, reason="reconnect")


def book_resync_concurrency(engine: Engine, reason: str) -> int:
    if reason == "reconnect":
        return max(
            1,
            int(getattr(engine._settings, "book_resync_reconnect_concurrency", 3)),
        )
    return max(1, int(getattr(engine._settings, "book_resync_concurrency", 8)))


def book_resync_symbol_timeout_sec(engine: Engine) -> float:
    return max(1.0, float(getattr(engine._settings, "book_resync_symbol_timeout_sec", 45.0)))


async def snapshot_book_timed(engine: Engine, symbol: str, *, reason: str) -> bool:
    """REST snapshot with timeout. Returns True on success."""
    timeout = book_resync_symbol_timeout_sec(engine)
    try:
        await asyncio.wait_for(engine._snapshot_book(symbol), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(
            "%s book resync: snapshot timed out after %.0fs for %s",
            reason,
            timeout,
            symbol,
        )
        return False
    except Exception:  # noqa: BLE001
        logger.exception("%s book resync: snapshot failed for %s", reason, symbol)
        return False
    return True


async def run_book_resync_workers(
    engine: Engine,
    symbols: list[str],
    *,
    concurrency: int,
    worker: Callable[[str], Awaitable[None]],
) -> int:
    """Run ``worker(symbol)`` with a fixed pool size (no N-task gather storms)."""
    del engine
    if not symbols:
        return 0
    limit = min(max(1, concurrency), len(symbols))
    sym_iter = iter(symbols)
    iter_lock = asyncio.Lock()
    failures = 0
    fail_lock = asyncio.Lock()

    async def _runner() -> None:
        nonlocal failures
        while True:
            async with iter_lock:
                try:
                    sym = next(sym_iter)
                except StopIteration:
                    return
            try:
                await worker(sym)
            except Exception:  # noqa: BLE001
                logger.exception("book resync worker failed for %s", sym)
                async with fail_lock:
                    failures += 1
            await asyncio.sleep(0)

    await asyncio.gather(*(_runner() for _ in range(limit)))
    return failures


async def cancel_reconnect_resync_task(engine: Engine) -> None:
    task = engine._reconnect_resync_debounce_task
    if task is None:
        return
    engine._reconnect_resync_debounce_task = None
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:  # noqa: BLE001
        logger.exception("reconnect resync task shutdown raised")


async def _clear_book_resync_if_current(engine: Engine, token: int) -> None:
    if engine._book_resync is None:
        return
    if engine._book_resync_token != token:
        return
    engine._book_resync = None
    await publish_book_resync(engine, clear=True)


async def resync_symbol_books(
    engine: Engine,
    symbols: list[str],
    *,
    reason: str,
    invalidate: bool = True,
    publish_startup: bool = False,
) -> None:
    if not symbols:
        return
    async with engine._book_resync_serial_lock:
        await _resync_symbol_books_locked(
            engine,
            symbols,
            reason=reason,
            invalidate=invalidate,
            publish_startup=publish_startup,
        )


async def _resync_symbol_books_locked(
    engine: Engine,
    symbols: list[str],
    *,
    reason: str,
    invalidate: bool,
    publish_startup: bool,
) -> None:
    logger.info("%s: resyncing %d symbol L2 book(s)", reason, len(symbols))
    normalized = [s.upper() for s in symbols]
    if invalidate:
        engine._md_quality.invalidate(normalized)
        for sym in normalized:
            engine._books.get(sym).invalidate()

    engine._bulk_resync_symbols.update(normalized)
    total = len(normalized)
    done = 0
    done_lock = asyncio.Lock()
    show_progress = publish_startup or engine._state.status is EngineStatus.RUNNING
    block_strategies = show_progress and reason in _STRATEGY_BLOCKING_REASONS
    token = 0

    if block_strategies:
        from .engine import StartupProgress

        engine._book_resync_token += 1
        token = engine._book_resync_token
        engine._book_resync = StartupProgress(
            phase="books",
            label="Resyncing L2 order books after strategy change…",
            done=0,
            total=total,
        )
        logger.info("book resync started (%s): %d symbols", reason, total)
        await publish_book_resync(engine)
    elif show_progress and reason != "startup":
        logger.info("book resync started (%s): %d symbols (non-blocking)", reason, total)

    failures = 0
    fail_lock = asyncio.Lock()

    async def _one(symbol: str) -> None:
        nonlocal done, failures
        async with done_lock:
            in_flight = done
        if publish_startup:
            await engine._set_startup(
                "books",
                "Syncing L2 order books…",
                done=in_flight,
                total=total,
                symbol=symbol,
            )
        ok = await snapshot_book_timed(engine, symbol, reason=reason)
        if not ok:
            async with fail_lock:
                failures += 1
        await asyncio.sleep(0)
        async with done_lock:
            done += 1
            completed = done
        if publish_startup:
            await engine._set_startup(
                "books",
                "Syncing L2 order books…",
                done=completed,
                total=total,
                symbol=symbol,
            )
        elif block_strategies and engine._book_resync is not None and (
            completed % 8 == 0 or completed == total
        ):
            await publish_book_resync(
                engine, done=completed, total=total, symbol=symbol,
            )

    try:
        await run_book_resync_workers(
            engine,
            normalized,
            concurrency=book_resync_concurrency(engine, reason),
            worker=_one,
        )
        if failures:
            logger.warning(
                "%s book resync: %d/%d snapshots failed",
                reason,
                failures,
                len(normalized),
            )
    finally:
        engine._bulk_resync_symbols.difference_update(normalized)
        if block_strategies:
            await _clear_book_resync_if_current(engine, token)
        elif show_progress and reason != "startup":
            logger.info("book resync complete (%s)", reason)


async def publish_book_resync(
    engine: Engine,
    *,
    done: int | None = None,
    total: int | None = None,
    symbol: str | None = None,
    clear: bool = False,
) -> None:
    if clear:
        logger.info("book resync complete")
        await engine._bus.publish(
            Event(type=EventType.STATUS, payload={"kind": "book_resync", "clear": True}),
        )
        return
    br = engine._book_resync
    if br is None:
        return
    if done is not None:
        br.done = done
    if total is not None:
        br.total = total
    if symbol is not None:
        br.symbol = symbol
    await engine._bus.publish(
        Event(
            type=EventType.STATUS,
            payload={
                "kind": "book_resync",
                "phase": br.phase,
                "label": br.label,
                "done": br.done,
                "total": br.total,
                "symbol": br.symbol,
            },
        ),
    )
