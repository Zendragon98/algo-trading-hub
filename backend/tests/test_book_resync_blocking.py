"""Book resync must not freeze strategy evaluation on reconnect/gap."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.enums import EngineStatus
from engine.core import book_resync_runtime


def _mock_engine(*, running: bool = True) -> MagicMock:
    engine = MagicMock()
    engine._settings = MagicMock()
    engine._settings.book_resync_concurrency = 4
    engine._settings.book_resync_reconnect_concurrency = 2
    engine._settings.book_resync_symbol_timeout_sec = 5.0
    engine._state.status = EngineStatus.RUNNING if running else EngineStatus.STOPPED
    engine._md_quality = MagicMock()
    engine._books = MagicMock()
    engine._books.get.return_value = MagicMock()
    engine._bulk_resync_symbols = set()
    engine._book_resync_serial_lock = asyncio.Lock()
    engine._book_resync_token = 0
    engine._book_resync = None
    engine._snapshot_book = AsyncMock()
    engine._set_startup = AsyncMock()
    engine._bus = MagicMock()
    engine._bus.publish = AsyncMock()
    return engine


@pytest.mark.asyncio
async def test_reconnect_resync_does_not_set_book_resync_flag() -> None:
    engine = _mock_engine()
    await book_resync_runtime.resync_symbol_books(
        engine, ["BTCUSDT", "ETHUSDT"], reason="reconnect",
    )
    assert engine._book_resync is None
    assert engine._snapshot_book.await_count == 2


@pytest.mark.asyncio
async def test_strategy_swap_sets_and_clears_book_resync() -> None:
    engine = _mock_engine()
    await book_resync_runtime.resync_symbol_books(
        engine, ["BTCUSDT"], reason="strategy_swap",
    )
    assert engine._book_resync is None
    assert engine._snapshot_book.await_count == 1


@pytest.mark.asyncio
async def test_snapshot_timeout_does_not_leave_book_resync_set() -> None:
    engine = _mock_engine()

    async def slow(_symbol: str) -> None:
        await asyncio.sleep(10.0)

    engine._snapshot_book = slow
    engine._settings.book_resync_symbol_timeout_sec = 0.05

    await book_resync_runtime.resync_symbol_books(
        engine, ["BTCUSDT"], reason="strategy_swap",
    )
    assert engine._book_resync is None


@pytest.mark.asyncio
async def test_resync_serializes_under_lock() -> None:
    engine = _mock_engine()
    order: list[str] = []

    async def snap(symbol: str) -> None:
        order.append(f"start-{symbol}")
        await asyncio.sleep(0.02)
        order.append(f"end-{symbol}")

    engine._snapshot_book = snap

    await asyncio.gather(
        book_resync_runtime.resync_symbol_books(
            engine, ["A"], reason="reconnect",
        ),
        book_resync_runtime.resync_symbol_books(
            engine, ["B"], reason="reconnect",
        ),
    )
    assert order.index("end-A") < order.index("start-B") or order.index("end-B") < order.index("start-A")
