"""Shared FastAPI dependencies.

The Engine + EventBus are stored on `app.state` at startup and accessed
from route handlers via these helpers. Centralising the lookup keeps
route handlers free of `request.app.state.engine` boilerplate.
"""

from __future__ import annotations

from fastapi import Request

from common.events import EventBus
from engine.core.engine import Engine


def get_engine(request: Request) -> Engine:
    return request.app.state.engine  # type: ignore[no-any-return]


def get_bus(request: Request) -> EventBus:
    return request.app.state.bus  # type: ignore[no-any-return]
