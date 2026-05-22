"""GET /api/logs.

The dashboard primarily streams logs over /ws, but a REST endpoint is
useful for the initial hydrate so the panel isn't empty on first load.
We keep an in-memory ring buffer fed by the EventBus.
"""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request

from common.enums import EventType
from common.events import EventBus

from ..dependencies import get_bus
from ..schemas import LogDTO

router = APIRouter(prefix="/api", tags=["logs"])

_BUFFER: deque[LogDTO] = deque(maxlen=300)
_BUFFER_TASK: asyncio.Task | None = None


def _fmt(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M:%S")


async def _populate_from_bus(bus: EventBus) -> None:
    async with bus.subscribe(types=[EventType.LOG]) as queue:
        while True:
            event = await queue.get()
            payload = event.payload
            _BUFFER.append(
                LogDTO(
                    ts=_fmt(event.ts),
                    level=payload.get("level", "info"),  # type: ignore[arg-type]
                    msg=payload.get("msg", "") or payload.get("message", ""),
                    logger=payload.get("logger"),
                )
            )


@router.on_event("startup")
async def _start(request: Request | None = None) -> None:
    # FastAPI on_event runs in the app lifespan; we wire the buffer task
    # in api.server.lifespan instead so it has direct access to the bus.
    return


@router.get("/logs", response_model=list[LogDTO])
def logs(_bus: EventBus = Depends(get_bus), limit: int = 60) -> list[LogDTO]:
    # Newest first; the dashboard prepends events from the WS feed.
    return list(reversed(list(_BUFFER)))[:limit]


def buffer() -> deque[LogDTO]:
    """Expose the buffer so api.server can feed it from the bus task."""
    return _BUFFER
