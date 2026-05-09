"""GET /api/execution.

Surfaces the per-parent execution-quality reports + portfolio aggregate
for the dashboard's Execution Quality panel.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from engine.core.engine import Engine

from ..dependencies import get_engine
from ..schemas import ExecutionStatsDTO
from ..serializers import execution_stats_dto

router = APIRouter(prefix="/api", tags=["execution"])


@router.get("/execution", response_model=ExecutionStatsDTO)
def execution(engine: Engine = Depends(get_engine)) -> ExecutionStatsDTO:
    return execution_stats_dto(engine)
