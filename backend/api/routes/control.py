"""POST /api/control/{start|pause|resume|stop|shutdown|flatten|strategy}, PATCH /api/control/risk,
GET/POST circuit-breaker controls."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel

from engine.core.engine import Engine
from engine.risk.circuit_breaker import BreakerStatus

from ..dependencies import get_engine
from ..schemas import (
    BreakerListDTO,
    BreakerRearmDTO,
    BreakerStatusDTO,
    BreakerTripDTO,
    RiskUpdateDTO,
    StatusDTO,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


class _StrategyToggleBody(BaseModel):
    """Body of ``POST /api/control/strategy``."""

    name: str

router = APIRouter(prefix="/api/control", tags=["control"])


def _status(engine: Engine) -> StatusDTO:
    snap = engine.snapshot()
    return StatusDTO(
        status=snap.status.value,
        uptime_sec=snap.uptime_sec,
        paper_mode=not engine.settings.is_live,
    )


def _breaker_dto(status: BreakerStatus) -> BreakerStatusDTO:
    return BreakerStatusDTO(**status.to_dict())


async def _run_or_500(action: str, fn: Callable[[], Awaitable[T]]) -> T:
    """Surface engine errors to the dashboard with the underlying message.

    Without this, FastAPI returns an opaque ``500 Internal Server Error`` and
    the operator has to dig through logs to see what actually broke.
    """
    try:
        return await fn()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 -- propagate as JSON detail
        logger.exception("control %s failed", action)
        raise HTTPException(
            status_code=500,
            detail=f"{action} failed: {type(exc).__name__}: {exc}",
        ) from exc


@router.post("/start", response_model=StatusDTO)
async def start(engine: Engine = Depends(get_engine)) -> StatusDTO:
    async def _go() -> None:
        if engine.status.value == "stopped":
            await engine.start()
        else:
            await engine.resume()

    await _run_or_500("start", _go)
    return _status(engine)


@router.post("/pause", response_model=StatusDTO)
async def pause(engine: Engine = Depends(get_engine)) -> StatusDTO:
    await _run_or_500("pause", engine.pause)
    return _status(engine)


@router.post("/resume", response_model=StatusDTO)
async def resume(engine: Engine = Depends(get_engine)) -> StatusDTO:
    await _run_or_500("resume", engine.resume)
    return _status(engine)


@router.post("/stop", response_model=StatusDTO)
async def stop(engine: Engine = Depends(get_engine)) -> StatusDTO:
    await _run_or_500("stop", engine.stop)
    return _status(engine)


@router.post("/shutdown", response_model=StatusDTO)
async def shutdown(
    background_tasks: BackgroundTasks,
    request: Request,
    engine: Engine = Depends(get_engine),
) -> StatusDTO:
    """Stop the engine and exit the Python process (same path as SIGINT).

    Used by the dashboard Kill control when the API is embedded in
    ``backend/main.py``. Unit tests that mount ``create_app`` without
    ``request_shutdown`` receive HTTP 501.
    """
    fn = getattr(request.app.state, "request_shutdown", None)
    if fn is None:
        raise HTTPException(
            status_code=501,
            detail="shutdown is not wired for this server instance",
        )

    async def _after_response() -> None:
        logger.info("shutdown requested via API")
        fn()

    background_tasks.add_task(_after_response)
    return _status(engine)


@router.post("/flatten", response_model=StatusDTO)
async def flatten(engine: Engine = Depends(get_engine)) -> StatusDTO:
    await _run_or_500("flatten", engine._flatten_and_wait_for_flat)
    return _status(engine)


@router.patch("/risk", response_model=StatusDTO)
async def update_risk(
    body: RiskUpdateDTO,
    engine: Engine = Depends(get_engine),
) -> StatusDTO:
    engine.risk.update_max_risk_pct(body.max_risk_pct)
    return _status(engine)


@router.post("/strategy", response_model=StatusDTO)
async def set_strategy(
    body: _StrategyToggleBody,
    engine: Engine = Depends(get_engine),
) -> StatusDTO:
    """Hot-swap the active strategy or enable multi-strategy netting.

    ``body.name`` must match a strategy registered at engine boot, or
    ``"all"`` to run every strategy with internal position netting.
    """
    try:
        engine.set_active_strategy(body.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _status(engine)


@router.get("/breakers", response_model=BreakerListDTO)
async def list_breakers(engine: Engine = Depends(get_engine)) -> BreakerListDTO:
    """Active + recent circuit-breaker breaches."""
    breaker = engine.risk.breaker
    return BreakerListDTO(
        active=[_breaker_dto(s) for s in breaker.active()],
        history=[_breaker_dto(s) for s in breaker.history()],
    )


@router.post("/breakers/rearm", response_model=BreakerListDTO)
async def rearm_breakers(
    body: BreakerRearmDTO,
    engine: Engine = Depends(get_engine),
) -> BreakerListDTO:
    """Operator-driven re-arm for latched MAJOR breaches.

    Body fields are optional:
        - ``code`` filters to one breach code (e.g. ``max_drawdown``)
        - ``target`` filters to one symbol / parent id
        - both omitted -> clears every active breach

    When a latched breach is cleared, the engine resets any dependent risk
    baseline (loss streak, daily anchor, session / HWM drawdown, execution
    history) so the same condition does not immediately re-latch on the next
    heartbeat.
    """
    breaker = engine.risk.breaker
    before = {s.code for s in breaker.active()}
    breaker.rearm(code=body.code, target=body.target)
    cleared = before - {s.code for s in breaker.active()}
    if cleared:
        engine.apply_breaker_rearm_side_effects(cleared)
    return BreakerListDTO(
        active=[_breaker_dto(s) for s in breaker.active()],
        history=[_breaker_dto(s) for s in breaker.history()],
    )


@router.post("/breakers/trip", response_model=BreakerListDTO)
async def trip_breakers(
    body: BreakerTripDTO,
    engine: Engine = Depends(get_engine),
) -> BreakerListDTO:
    """Operator trading halt: latch ``operator_halt`` and optionally flatten."""
    await _run_or_500(
        "breakers/trip",
        lambda: engine.operator_halt(
            detail=body.detail,
            flatten=body.flatten,
            pause=body.pause,
        ),
    )
    breaker = engine.risk.breaker
    return BreakerListDTO(
        active=[_breaker_dto(s) for s in breaker.active()],
        history=[_breaker_dto(s) for s in breaker.history()],
    )
