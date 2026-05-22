"""Backtest datasets, download, and offline strategy runs."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

logger = logging.getLogger(__name__)

from analytics.backtest.runner import (
    BacktestResult,
    list_saved_results,
    load_saved_result,
    run_backtest,
)
from analytics.data_loader import download_klines
from analytics.kline_store import (
    backend_data_root,
    list_run_ids_with_bars,
    load_manifest,
)
from common.config import Settings, get_settings

from ..schemas import (
    BacktestDatasetDTO,
    BacktestDownloadRequestDTO,
    BacktestFillDTO,
    BacktestMetricsDTO,
    BacktestResultDTO,
    BacktestResultSummaryDTO,
    BacktestRunRequestDTO,
    BacktestRunSessionDTO,
)

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


def _persist_base(settings: Settings) -> Path:
    base = Path(settings.persist_dir)
    if not base.is_absolute():
        base = backend_data_root().parent / base
    return base


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _result_to_dto(result: BacktestResult) -> BacktestResultDTO:
    return BacktestResultDTO(
        run_id=result.run_id,
        strategy=result.strategy,
        dataset=result.dataset,
        bar_count=result.bar_count,
        symbols=result.symbols,
        metrics=BacktestMetricsDTO(
            total_return_pct=result.metrics.total_return_pct,
            max_drawdown_pct=result.metrics.max_drawdown_pct,
            trade_count=result.metrics.trade_count,
            win_rate=result.metrics.win_rate,
            realized_pnl=result.metrics.realized_pnl,
            final_equity=result.metrics.final_equity,
        ),
        equity_curve=result.equity_curve,
        fills=[
            BacktestFillDTO(
                symbol=f.symbol,
                side=f.side,
                qty=f.qty,
                price=f.price,
                ts=f.ts,
                reason=f.reason,
                pnl=f.pnl,
                action=f.action,
            )
            for f in result.fills
        ],
        notes=result.notes,
    )


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


@router.post("/download")
async def download_dataset(
    body: BacktestDownloadRequestDTO,
    settings: Settings = Depends(get_settings),
) -> dict:
    logger.info(
        "backtest download requested symbols=%s interval=%s days=%d",
        body.symbols,
        body.interval,
        body.days,
    )
    try:
        results = await download_klines(
            body.symbols,
            interval=body.interval,
            days=body.days,
            settings=settings,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("backtest download failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "ok": True,
        "downloaded": [
            {"symbol": r.symbol, "interval": r.interval, "rows": r.rows, "path": r.path}
            for r in results
        ],
    }


@router.post("/run", response_model=BacktestResultDTO)
async def run_backtest_endpoint(
    body: BacktestRunRequestDTO,
    settings: Settings = Depends(get_settings),
) -> BacktestResultDTO:
    merged = settings.model_copy(
        update={"strategy": body.strategy, **(body.settings_overrides or {})},
    )
    logger.info(
        "backtest run requested strategy=%s dataset=%s",
        body.strategy,
        body.dataset,
    )
    try:
        result = run_backtest(
            merged,
            dataset=body.dataset,
            start=_parse_dt(body.start),
            end=_parse_dt(body.end),
            persist_dir=_persist_base(settings),
        )
    except ValueError as exc:
        logger.warning("backtest run rejected: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("backtest run failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _result_to_dto(result)


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
