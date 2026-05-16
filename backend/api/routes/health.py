"""GET /health and GET /ready — process and engine readiness."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from common.enums import EngineStatus

from ..dependencies import get_engine
from engine.core.engine import Engine

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(engine: Engine = Depends(get_engine)) -> dict[str, object]:
    now = time.time()
    running = engine.status is EngineStatus.RUNNING
    tick_fresh = (
        engine.snapshot().last_tick_ts > 0
        and (now - engine.snapshot().last_tick_ts) < 60.0
    )
    user_fresh = (
        engine.oms.last_venue_truth_ts > 0
        and (now - engine.oms.last_venue_truth_ts) < 120.0
    )
    ok = running and tick_fresh and user_fresh
    return {
        "ready": ok,
        "engine": engine.status.value,
        "tick_fresh": tick_fresh,
        "user_data_fresh": user_fresh,
    }
