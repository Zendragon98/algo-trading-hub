"""GET /api/positions."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from engine.core.engine import Engine

from ..dependencies import get_engine
from ..schemas import PositionDTO
from ..serializers import position_to_dto

router = APIRouter(prefix="/api", tags=["positions"])


@router.get("/positions", response_model=list[PositionDTO])
def positions(engine: Engine = Depends(get_engine)) -> list[PositionDTO]:
    return [position_to_dto(p) for p in engine.snapshot().positions]
