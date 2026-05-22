"""Backtest datasets, download, and offline strategy runs (async job queue)."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from analytics.backtest.runner import list_saved_results, load_saved_result
from analytics.jobs import (
    enqueue_job,
    list_jobs,
    load_job,
    resolve_jobs_dir,
)
from analytics.kline_store import (
    backend_data_root,
    list_run_ids_with_bars,
    load_manifest,
)
from common.config import Settings, get_settings

from ..schemas import (
    AnalyticsJobDTO,
    BacktestDatasetDTO,
    BacktestDownloadRequestDTO,
    BacktestFillDTO,
    BacktestJobAcceptedDTO,
    BacktestMetricsDTO,
    BacktestResultDTO,
    BacktestResultSummaryDTO,
    BacktestRunRequestDTO,
    BacktestRunSessionDTO,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


def _persist_base(settings: Settings) -> Path:
    base = Path(settings.persist_dir)
    if not base.is_absolute():
        base = backend_data_root().parent / base
    return base


def _jobs_dir(request: Request, settings: Settings) -> Path:
    raw = getattr(request.app.state, "analytics_jobs_dir", None)
    if raw is not None:
        return Path(raw)
    return resolve_jobs_dir(settings.analytics_jobs_dir)


def _job_to_dto(record) -> AnalyticsJobDTO:
    return AnalyticsJobDTO(
        id=record.id,
        type=record.type,
        status=record.status,
        progress=record.progress,
        result=record.result,
        error=record.error,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _ensure_worker(settings: Settings) -> None:
    if not settings.analytics_worker_enabled:
        raise HTTPException(
            status_code=503,
            detail="analytics worker disabled; set ANALYTICS_WORKER_ENABLED=true",
        )
    if (settings.analytics_worker_mode or "embedded").strip().lower() == "disabled":
        raise HTTPException(status_code=503, detail="analytics worker mode is disabled")


@router.get("/datasets", response_model=list[BacktestDatasetDTO])
async def list_datasets(settings: Settings = Depends(get_settings)) -> list[BacktestDatasetDTO]:
    entries = load_manifest()
    return [
        BacktestDatasetDTO(
            symbol=e.symbol,
            interval=e.interval,
            source=e.source,
            rows=e.rows,
            start=e.start,
            end=e.end,
            path=e.path,
            run_ids=e.run_ids,
            updated_at=e.updated_at,
        )
        for e in entries
    ]


@router.get("/sessions", response_model=list[BacktestRunSessionDTO])
async def list_capture_sessions(
    settings: Settings = Depends(get_settings),
) -> list[BacktestRunSessionDTO]:
    base = _persist_base(settings)
    ids = list_run_ids_with_bars(base)
    return [BacktestRunSessionDTO(run_id=rid, label=rid) for rid in ids]


@router.get("/jobs", response_model=list[AnalyticsJobDTO])
async def list_analytics_jobs(
    request: Request,
    settings: Settings = Depends(get_settings),
    limit: int = 50,
) -> list[AnalyticsJobDTO]:
    jobs = list_jobs(_jobs_dir(request, settings), limit=limit)
    return [_job_to_dto(j) for j in jobs]


@router.get("/jobs/{job_id}", response_model=AnalyticsJobDTO)
async def get_analytics_job(
    job_id: str,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> AnalyticsJobDTO:
    record = load_job(_jobs_dir(request, settings), job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="job not found")
    return _job_to_dto(record)


@router.post("/download", status_code=202, response_model=BacktestJobAcceptedDTO)
async def download_dataset(
    body: BacktestDownloadRequestDTO,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> BacktestJobAcceptedDTO:
    _ensure_worker(settings)
    logger.info(
        "backtest download enqueued symbols=%s interval=%s days=%d",
        body.symbols,
        body.interval,
        body.days,
    )
    record = enqueue_job(
        "kline_download",
        {
            "symbols": body.symbols,
            "interval": body.interval,
            "days": body.days,
        },
        jobs_dir=_jobs_dir(request, settings),
    )
    return BacktestJobAcceptedDTO(job_id=record.id, status=record.status)


@router.post("/run", status_code=202, response_model=BacktestJobAcceptedDTO)
async def run_backtest_endpoint(
    body: BacktestRunRequestDTO,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> BacktestJobAcceptedDTO:
    _ensure_worker(settings)
    logger.info(
        "backtest run enqueued strategy=%s dataset=%s",
        body.strategy,
        body.dataset,
    )
    record = enqueue_job(
        "backtest_run",
        {
            "strategy": body.strategy,
            "dataset": body.dataset,
            "start": body.start,
            "end": body.end,
            "settings_overrides": body.settings_overrides,
            "persist_dir": str(_persist_base(settings)),
        },
        jobs_dir=_jobs_dir(request, settings),
    )
    return BacktestJobAcceptedDTO(job_id=record.id, status=record.status)


@router.get("/runs", response_model=list[BacktestResultSummaryDTO])
async def list_backtest_runs() -> list[BacktestResultSummaryDTO]:
    rows = list_saved_results()
    return [
        BacktestResultSummaryDTO(
            run_id=r["run_id"],
            strategy=r.get("strategy", ""),
            dataset=r.get("dataset", ""),
            bar_count=int(r.get("bar_count", 0)),
            total_return_pct=float(r.get("total_return_pct", 0.0)),
            saved_at=r.get("saved_at"),
        )
        for r in rows
    ]


@router.get("/runs/{run_id}", response_model=BacktestResultDTO)
async def get_backtest_run(run_id: str) -> BacktestResultDTO:
    data = load_saved_result(run_id)
    if data is None:
        raise HTTPException(status_code=404, detail="backtest run not found")
    return BacktestResultDTO(
        run_id=data["run_id"],
        strategy=data.get("strategy", ""),
        dataset=data.get("dataset", ""),
        bar_count=int(data.get("bar_count", 0)),
        symbols=list(data.get("symbols", [])),
        metrics=BacktestMetricsDTO(**data.get("metrics", {})),
        equity_curve=list(data.get("equity_curve", [])),
        fills=[BacktestFillDTO(**f) for f in data.get("fills", [])],
        notes=list(data.get("notes", [])),
    )
