"""GET /api/status, GET /api/equity, GET /api/state.

Status is the lightest poll target; state is the full hydrate used once
on dashboard mount. Live updates flow via /ws.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from engine.core.engine import Engine

from ..dependencies import get_engine
from ..schemas import EquityDTO, StateDTO, StatusDTO
from ..serializers import build_status_dto, snapshot_to_state_dto

router = APIRouter(prefix="/api", tags=["status"])


@router.get("/status", response_model=StatusDTO)
def status(engine: Engine = Depends(get_engine)) -> StatusDTO:
    return build_status_dto(engine)


@router.get("/equity", response_model=EquityDTO)
def equity(engine: Engine = Depends(get_engine)) -> EquityDTO:
    snap = engine.snapshot()
    return EquityDTO(equity=snap.equity_curve, last_ts=snap.last_tick_ts)


@router.get("/state", response_model=StateDTO)
def state(engine: Engine = Depends(get_engine)) -> StateDTO:
    return snapshot_to_state_dto(engine, engine.snapshot())
