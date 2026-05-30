"""POST /api/control/{start|pause|resume|stop|shutdown|flatten|strategy}, PATCH /api/control/risk,
GET/POST circuit-breaker controls."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel

from common.enums import EngineStatus
from engine.core.engine import Engine
from engine.risk.circuit_breaker import BreakerStatus

from ..breaker_patch import (
    breaker_registry_dtos,
    build_breaker_enabled_settings_patch,
)
from ..dependencies import get_engine
from ..schemas import (
    BreakerDefinitionDTO,
    BreakerEnabledPatchDTO,
    BreakerListDTO,
    BreakerRearmDTO,
    BreakerStatusDTO,
    BreakerTripDTO,
    RiskUpdateDTO,
    StatusDTO,
)
from ..serializers import build_status_dto

logger = logging.getLogger(__name__)

T = TypeVar("T")


class _StrategyToggleBody(BaseModel):
    """Body of ``POST /api/control/strategy``."""

    name: str

router = APIRouter(prefix="/api/control", tags=["control"])


def _status(engine: Engine) -> StatusDTO:
    return build_status_dto(engine)


def _breaker_dto(status: BreakerStatus) -> BreakerStatusDTO:
    return BreakerStatusDTO(**status.to_dict())


def _breaker_list(engine: Engine) -> BreakerListDTO:
    breaker = engine.risk.breaker
    return BreakerListDTO(
        active=[_breaker_dto(s) for s in breaker.active()],
        history=[_breaker_dto(s) for s in breaker.history()],
        registry=[BreakerDefinitionDTO(**row) for row in breaker_registry_dtos()],
        enabled=dict(engine.settings.breaker_enabled),
    )


def _log_control_ok(action: str, engine: Engine) -> None:
    logger.info("control %s ok status=%s", action, engine.status.value)


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
        if engine.status is EngineStatus.STARTING:
            return
        if engine.status is EngineStatus.STOPPED:
            await engine.start()
        else:
            await engine.resume()

    await _run_or_500("start", _go)
    _log_control_ok("start", engine)
    return _status(engine)


@router.post("/pause", response_model=StatusDTO)
async def pause(engine: Engine = Depends(get_engine)) -> StatusDTO:
    await _run_or_500("pause", engine.pause)
    _log_control_ok("pause", engine)
    return _status(engine)


@router.post("/resume", response_model=StatusDTO)
async def resume(engine: Engine = Depends(get_engine)) -> StatusDTO:
    await _run_or_500("resume", engine.resume)
    _log_control_ok("resume", engine)
    return _status(engine)


@router.post("/stop", response_model=StatusDTO)
async def stop(engine: Engine = Depends(get_engine)) -> StatusDTO:
    await _run_or_500("stop", engine.stop)
    _log_control_ok("stop", engine)
    return _status(engine)


@router.post("/kill", response_model=StatusDTO)
async def kill(engine: Engine = Depends(get_engine)) -> StatusDTO:
    """Emergency stop: flatten, stop the engine, keep the API process running.

    Dashboard **E-Stop** uses this so operators can press **Start** again without
    restarting the server VM or ``python main.py``. For process exit use
    ``POST /api/control/shutdown`` (not exposed in the default UI).
    """

    async def _go() -> None:
        await engine.stop(force_flatten=True)

    await _run_or_500("kill", _go)
    _log_control_ok("kill", engine)
    return _status(engine)


@router.post("/shutdown", response_model=StatusDTO)
async def shutdown(
    background_tasks: BackgroundTasks,
    request: Request,
    engine: Engine = Depends(get_engine),
) -> StatusDTO:
    """Flatten, cancel all orders, stop the engine, then exit the process.

    Used by the dashboard **Kill** control when the API is embedded in
    ``backend/main.py``. Unwind runs *before* the HTTP response so the operator
    sees errors if flatten fails; process exit is scheduled after the response.
    Unit tests that mount ``create_app`` without ``request_shutdown`` receive
    HTTP 501.
    """
    fn = getattr(request.app.state, "request_shutdown", None)
    if fn is None:
        raise HTTPException(
            status_code=501,
            detail="shutdown is not wired for this server instance",
        )

    async def _kill() -> None:
        if engine.status is not EngineStatus.STOPPED:
            await engine.stop(force_flatten=True)

    await _run_or_500("shutdown", _kill)
    _log_control_ok("shutdown", engine)

    async def _after_response() -> None:
        logger.info("shutdown requested via API (process exit scheduled)")
        fn()

    background_tasks.add_task(_after_response)
    return _status(engine)


@router.post("/flatten", response_model=StatusDTO)
async def flatten(engine: Engine = Depends(get_engine)) -> StatusDTO:
    await _run_or_500("flatten", engine._flatten_and_wait_for_flat)
    _log_control_ok("flatten", engine)
    return _status(engine)


@router.patch("/risk", response_model=StatusDTO)
async def update_risk(
    body: RiskUpdateDTO,
    engine: Engine = Depends(get_engine),
) -> StatusDTO:
    engine.risk.update_max_risk_pct(body.max_risk_pct)
    logger.info("control risk updated max_risk_pct=%.4f", body.max_risk_pct)
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
        changed = engine.set_active_strategy(body.name)
    except ValueError as exc:
        msg = str(exc)
        if "strategy swap rate limited" in msg:
            raise HTTPException(status_code=429, detail=msg) from exc
        raise HTTPException(status_code=400, detail=msg) from exc
    if changed and engine.status is EngineStatus.RUNNING:
        refreshed = await _run_or_500("strategy/market", engine.refresh_market_universe)
        if refreshed and not engine.rest_heavily_throttled():
            await _run_or_500("strategy/sync", engine.sync_trading_book_from_rest)
        elif refreshed:
            logger.info(
                "strategy swap: skipping REST sync (rate-limited %.0fs)",
                engine.rest_backoff_remaining_sec(),
            )
        else:
            logger.info("strategy swap: market universe unchanged, skipping REST sync")
    logger.info("control strategy set name=%s changed=%s", body.name, changed)
    return _status(engine)


@router.get("/breakers", response_model=BreakerListDTO)
async def list_breakers(engine: Engine = Depends(get_engine)) -> BreakerListDTO:
    """Active + recent circuit-breaker breaches, registry, and enable flags."""
    return _breaker_list(engine)


@router.patch("/breakers/enabled", response_model=BreakerListDTO)
async def patch_breaker_enabled(
    body: BreakerEnabledPatchDTO,
    engine: Engine = Depends(get_engine),
) -> BreakerListDTO:
    """Enable or disable individual circuit-breaker codes at runtime."""
    if body.patch:
        patch_body = body.patch
    elif body.code is not None and body.enabled is not None:
        patch_body = {body.code: body.enabled}
    else:
        raise HTTPException(
            status_code=400,
            detail="Provide patch={code: bool, ...} or code + enabled",
        )
    try:
        settings_patch = build_breaker_enabled_settings_patch(
            engine.settings,
            patch_body,
            confirm_live_disable=body.confirm_live_disable,
            confirm_token=body.confirm_token,
        )
        engine.apply_settings_patch(settings_patch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info("control breakers enabled patch=%s", patch_body)
    return _breaker_list(engine)


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
    logger.info(
        "control breakers rearm code=%s target=%s cleared=%s",
        body.code,
        body.target,
        sorted(cleared) if cleared else "none",
    )
    return _breaker_list(engine)


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
    logger.info(
        "control breakers trip flatten=%s pause=%s detail=%s",
        body.flatten,
        body.pause,
        body.detail or "-",
    )
    return _breaker_list(engine)
