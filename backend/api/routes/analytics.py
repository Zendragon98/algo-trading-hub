"""Analytics endpoints (MM universe scan, rankings)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from analytics.jobs import enqueue_job, resolve_jobs_dir
from analytics.mm_universe_scanner import load_mm_universe_report
from common.config import Settings, get_settings

from ..schemas import (
    BacktestJobAcceptedDTO,
    MmUniverseRankingDTO,
    MmUniverseScanReportDTO,
    MmUniverseScanRequestDTO,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def _jobs_dir(request: Request, settings: Settings):
    raw = getattr(request.app.state, "analytics_jobs_dir", None)
    if raw is not None:
        from pathlib import Path

        return Path(raw)
    return resolve_jobs_dir(settings.analytics_jobs_dir)


def _ensure_worker(settings: Settings) -> None:
    if not settings.analytics_worker_enabled:
        raise HTTPException(
            status_code=503,
            detail="analytics worker disabled; set ANALYTICS_WORKER_ENABLED=true",
        )


@router.get("/mm-universe", response_model=MmUniverseScanReportDTO | None)
async def get_mm_universe_report() -> MmUniverseScanReportDTO | None:
    report = load_mm_universe_report()
    if report is None:
        return None
    return MmUniverseScanReportDTO(
        generated_at=report.generated_at,
        recommended=report.recommended,
        candidates_scanned=report.candidates_scanned,
        sample_rounds=report.sample_rounds,
        rankings=[
            MmUniverseRankingDTO(
                symbol=r.symbol,
                quote_volume_24h=r.quote_volume_24h,
                last_price=r.last_price,
                median_spread_bps=r.median_spread_bps,
                spread_cv=r.spread_cv,
                mid_vol_bps=r.mid_vol_bps,
                edge_bps=r.edge_bps,
                score=r.score,
                eligible=r.eligible,
                reject_reason=r.reject_reason,
            )
            for r in sorted(report.rankings, key=lambda x: x.score, reverse=True)
        ],
    )


@router.post("/mm-universe/scan", status_code=202, response_model=BacktestJobAcceptedDTO)
async def enqueue_mm_universe_scan(
    body: MmUniverseScanRequestDTO,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> BacktestJobAcceptedDTO:
    _ensure_worker(settings)
    logger.info("mm universe scan enqueued sample=%s", body.sample)
    record = enqueue_job(
        "mm_universe_scan",
        {
            "sample": body.sample,
            "settings_overrides": body.settings_overrides,
        },
        jobs_dir=_jobs_dir(request, settings),
    )
    return BacktestJobAcceptedDTO(job_id=record.id, status=record.status)
