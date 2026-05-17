"""Bounded book-resync worker pool."""

from __future__ import annotations

import asyncio

import pytest

from engine.core.engine import Engine


@pytest.mark.asyncio
async def test_run_book_resync_workers_caps_concurrency() -> None:
    engine = Engine.__new__(Engine)
    in_flight = 0
    peak = 0
    lock = asyncio.Lock()

    async def worker(sym: str) -> None:
        nonlocal in_flight, peak
        async with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        await asyncio.sleep(0.02)
        async with lock:
            in_flight -= 1

    symbols = [f"S{i}" for i in range(12)]
    failures = await engine._run_book_resync_workers(
        symbols,
        concurrency=3,
        worker=worker,
    )
    assert failures == 0
    assert peak <= 3
