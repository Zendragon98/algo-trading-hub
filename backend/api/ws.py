"""WebSocket endpoint that streams engine events to the React console.

Each connected client gets its own EventBus subscription. Events are
forwarded as JSON messages of the shape:

    {"type": "<EventType>", "ts": <epoch>, "data": <payload>}

We never block on a slow client because the EventBus drops oldest on a
full subscriber queue.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from common.events import EventBus

from .dependencies import get_bus

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws")
async def stream(websocket: WebSocket) -> None:
    bus: EventBus = get_bus(websocket)  # type: ignore[arg-type]
    await websocket.accept()
    logger.info("ws client connected (total=%d)", bus.subscriber_count + 1)

    try:
        async with bus.subscribe() as queue:
            while True:
                event = await queue.get()
                await websocket.send_json(
                    {
                        "type": event.type.value,
                        "ts": event.ts,
                        "data": event.payload,
                    }
                )
    except WebSocketDisconnect:
        logger.info("ws client disconnected")
    except Exception:  # noqa: BLE001
        logger.exception("ws stream raised; closing")
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass
