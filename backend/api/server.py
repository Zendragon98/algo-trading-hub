"""FastAPI application factory.

Builds an app that owns the Engine + EventBus lifetime. Designed to be
mounted by `backend/main.py` which runs Uvicorn programmatically; we
expose `create_app(engine, bus)` for tests too.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from common.config import Settings, get_settings
from common.enums import EventType
from common.events import EventBus

from .routes import control, execution, klines, logs, orders, positions, status, trades
from .schemas import LogDTO
from .ws import router as ws_router

logger = logging.getLogger(__name__)


def _fmt(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M:%S")


async def _log_buffer_pump(bus: EventBus) -> None:
    """Mirror LOG events into the in-memory ring buffer used by GET /api/logs."""
    async with bus.subscribe(types=[EventType.LOG]) as queue:
        while True:
            event = await queue.get()
            payload = event.payload
            logs.buffer().append(
                LogDTO(
                    ts=_fmt(event.ts),
                    level=payload.get("level", "info"),  # type: ignore[arg-type]
                    msg=payload.get("msg", ""),
                )
            )


def create_app(engine, bus: EventBus, settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Engine is started by `backend/main.py` *before* uvicorn boots so
        # that any startup failure surfaces immediately. The pump is the
        # only background task we own here.
        pump = asyncio.create_task(_log_buffer_pump(bus), name="log-buffer-pump")
        try:
            yield
        finally:
            pump.cancel()
            try:
                await pump
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    app = FastAPI(
        title="Algo Trading API",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        # Allow local dev frontends (Vite, Storybook, etc.) regardless of port.
        # This also covers cases where the browser reports `localhost` while the
        # API is addressed via `127.0.0.1` (or vice versa).
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.engine = engine
    app.state.bus = bus

    app.include_router(status.router)
    app.include_router(positions.router)
    app.include_router(trades.router)
    app.include_router(orders.router)
    app.include_router(execution.router)
    app.include_router(klines.router)
    app.include_router(logs.router)
    app.include_router(control.router)
    app.include_router(ws_router)

    return app
