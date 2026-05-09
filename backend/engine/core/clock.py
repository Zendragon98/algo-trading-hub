"""Engine heartbeat.

Drives periodic work that doesn't fit naturally onto the WS callbacks:
    - mark-to-market the portfolio (~1Hz)
    - sweep stop-loss / take-profit brackets against latest ticks
    - run strategies on the latest feature snapshot

Frequency is conservative on purpose; strategies that need higher
frequency can subscribe directly to `EventType.TICK` instead.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class Clock:
    def __init__(self, interval_sec: float, tick: Callable[[], Awaitable[None]]) -> None:
        self._interval = interval_sec
        self._tick = tick
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="engine-clock")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception:  # noqa: BLE001 -- never let a slow tick kill the loop
                logger.exception("clock tick raised")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                continue
