"""POST /api/control/{start|pause|resume|stop|flatten}, PATCH /api/control/risk."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from engine.core.engine import Engine

from ..dependencies import get_engine
from ..schemas import RiskUpdateDTO, StatusDTO

router = APIRouter(prefix="/api/control", tags=["control"])


def _status(engine: Engine) -> StatusDTO:
    snap = engine.snapshot()
    return StatusDTO(status=snap.status.value, uptime_sec=snap.uptime_sec)


@router.post("/start", response_model=StatusDTO)
async def start(engine: Engine = Depends(get_engine)) -> StatusDTO:
    if engine.status.value == "stopped":
        await engine.start()
    else:
        await engine.resume()
    return _status(engine)


@router.post("/pause", response_model=StatusDTO)
async def pause(engine: Engine = Depends(get_engine)) -> StatusDTO:
    await engine.pause()
    return _status(engine)


@router.post("/resume", response_model=StatusDTO)
async def resume(engine: Engine = Depends(get_engine)) -> StatusDTO:
    await engine.resume()
    return _status(engine)


@router.post("/stop", response_model=StatusDTO)
async def stop(engine: Engine = Depends(get_engine)) -> StatusDTO:
    await engine.stop()
    return _status(engine)


@router.post("/flatten", response_model=StatusDTO)
async def flatten(engine: Engine = Depends(get_engine)) -> StatusDTO:
    await engine.flatten()
    return _status(engine)


@router.patch("/risk", response_model=StatusDTO)
async def update_risk(
    body: RiskUpdateDTO,
    engine: Engine = Depends(get_engine),
) -> StatusDTO:
    engine.risk.update_max_risk_pct(body.max_risk_pct)
    return _status(engine)
