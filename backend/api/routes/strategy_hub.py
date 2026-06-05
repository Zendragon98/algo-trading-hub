"""GET /api/strategy-hub — per-strategy analytics and attributed PnL."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, Query

from engine.core.engine import Engine

from ..dependencies import get_engine
from ..schemas import StrategyHubDTO, StrategyHubLogDTO, StrategyLegDTO, StrategyPnlDTO
from ..serializers import build_strategy_hub_dto

router = APIRouter(prefix="/api", tags=["strategy-hub"])


@router.get("/strategy-hub", response_model=StrategyHubDTO)
def strategy_hub(engine: Engine = Depends(get_engine)) -> StrategyHubDTO:
    return build_strategy_hub_dto(engine)


@router.get("/strategy-hub/log", response_model=StrategyHubLogDTO)
def strategy_hub_log(
    engine: Engine = Depends(get_engine),
    tail: int = Query(20, ge=1, le=200),
) -> StrategyHubLogDTO:
    run_dir = engine.event_archive_dir
    if run_dir is None:
        return StrategyHubLogDTO(lines=[], log_path=None)
    log_path = run_dir / "strategy_hub.jsonl"
    if not log_path.is_file():
        return StrategyHubLogDTO(lines=[], log_path=str(log_path))
    rows: list[dict[str, object]] = []
    with log_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return StrategyHubLogDTO(
        lines=rows[-tail:],
        log_path=str(log_path),
    )
