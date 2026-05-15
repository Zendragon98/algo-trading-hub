"""GET /api/reports/latest — session summary from run archive."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends

from common.config import Settings, get_settings

from ..dependencies import get_engine
from ..schemas import DailyReportDTO
from analytics.daily_report import build_report, find_latest_run
from engine.core.engine import Engine

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/latest", response_model=DailyReportDTO)
async def latest_report(
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_settings),
) -> DailyReportDTO:
    base = Path(settings.persist_dir)
    if not base.is_absolute():
        base = Path(__file__).resolve().parents[2] / base
    run_dir = find_latest_run(base)
    if run_dir is None:
        return DailyReportDTO(run_dir="", notes=["no_runs"])
    report = build_report(run_dir)
    return DailyReportDTO(
        run_dir=report.run_dir,
        trade_count=report.trade_count,
        realized_pnl=report.realized_pnl,
        avg_slippage_bps=report.avg_slippage_bps,
        breaker_events=report.breaker_events,
        reconcile_mismatches=report.reconcile_mismatches,
        notes=report.notes,
    )
