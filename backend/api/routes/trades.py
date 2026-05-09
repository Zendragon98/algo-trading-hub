"""GET /api/trades."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from engine.core.engine import Engine

from ..dependencies import get_engine
from ..schemas import TradeDTO
from ..serializers import trade_to_dto

router = APIRouter(prefix="/api", tags=["trades"])


@router.get("/trades", response_model=list[TradeDTO])
def trades(engine: Engine = Depends(get_engine), limit: int = 40) -> list[TradeDTO]:
    return [trade_to_dto(t) for t in engine.snapshot().trades[:limit]]
