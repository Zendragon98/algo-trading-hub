"""In-process publish/subscribe bus.

The EventBus is the only cross-module coupling allowed inside the engine.
Producers (gateway, market_data, OMS, portfolio, risk) push typed events;
consumers (api/ws.py, performance tracker, log sinks) subscribe with a
predicate and drain at their own pace.

Implementation notes:
    - One asyncio.Queue per subscriber so a slow consumer cannot block
      producers or other consumers.
    - Bounded queues; if a consumer falls behind by more than `maxsize`,
      the oldest event is dropped and a warning is logged. Trading state
      is never lost because critical state lives in the engine; the bus
      only carries notifications.
    - Subscribers receive a context-managed iterator so cancellation and
      cleanup are explicit.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from time import time
from typing import Any

from .enums import EventType

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Event:
    """A single message on the bus.

    `payload` should always be JSON-serialisable so the WebSocket layer
    can forward it without a translation step.
    """

    type: EventType
    payload: dict[str, Any]
    ts: float = field(default_factory=time)


# A subscriber filter takes an Event and returns True iff it wants to receive it.
EventFilter = Callable[[Event], bool]


class EventBus:
    """Asyncio fan-out bus."""

    def __init__(self, queue_size: int = 1024) -> None:
        # Each subscriber gets its own queue so back-pressure is local.
        self._subscribers: list[tuple[asyncio.Queue[Event], EventFilter]] = []
        self._queue_size = queue_size
        self._lock = asyncio.Lock()

    async def publish(self, event: Event) -> None:
        """Push `event` to every interested subscriber.

        Drops the oldest message on a full queue so producers are never
        blocked by a misbehaving consumer.
        """
        async with self._lock:
            subs = list(self._subscribers)
        for queue, filt in subs:
            if not filt(event):
                continue
            if queue.full():
                # Drop oldest to make room. We never block producers.
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                logger.warning(
                    "EventBus subscriber queue full; dropped oldest event "
                    "(type=%s)",
                    event.type.value,
                )
            queue.put_nowait(event)

    async def publish_many(self, events: Iterable[Event]) -> None:
        for ev in events:
            await self.publish(ev)

    def publish_nowait(self, event: Event) -> None:
        """Schedule a publish from a synchronous callsite.

        Designed for safety-critical notifiers (circuit breaker trips,
        risk vetoes) that fire from sync paths but need to fan-out onto
        the bus. If no event loop is running we silently drop the event;
        callers must already have logged the underlying state change.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.publish(event))

    @asynccontextmanager
    async def subscribe(
        self,
        types: Iterable[EventType] | None = None,
        predicate: EventFilter | None = None,
    ) -> AsyncIterator[asyncio.Queue[Event]]:
        """Subscribe to a subset of events.

        Args:
            types: only deliver events of these types. ``None`` = all types.
            predicate: extra arbitrary filter applied after the type check.

        Yields:
            An ``asyncio.Queue[Event]`` the caller drains via ``await q.get()``.
            On exit the subscription is removed automatically.
        """
        wanted = set(types) if types is not None else None

        def _filter(event: Event) -> bool:
            if wanted is not None and event.type not in wanted:
                return False
            return True if predicate is None else predicate(event)

        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=self._queue_size)
        async with self._lock:
            self._subscribers.append((queue, _filter))
        try:
            yield queue
        finally:
            async with self._lock:
                self._subscribers = [
                    (q, f) for (q, f) in self._subscribers if q is not queue
                ]

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
