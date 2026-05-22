"""Filesystem job queue for analytics work outside the trading process.

Jobs live under ``<jobs_dir>/pending|running|done|failed``. The trading API
enqueues JSON job specs; ``analytics.worker_main`` executes them in a
separate process so backtests and kline downloads do not block the engine
event loop.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from analytics.kline_store import backend_data_root

logger = logging.getLogger(__name__)

JobType = Literal["backtest_run", "kline_download", "report_build", "mm_universe_scan"]
JobStatus = Literal["pending", "running", "done", "failed"]


@dataclass(slots=True)
class JobRecord:
    id: str
    type: JobType
    payload: dict[str, Any]
    status: JobStatus = "pending"
    progress: float = 0.0
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(tz=UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(tz=UTC).isoformat())


def resolve_jobs_dir(raw: str | Path | None = None) -> Path:
    if raw is None:
        path = backend_data_root() / "jobs"
    else:
        path = Path(raw)
        if not path.is_absolute():
            path = backend_data_root().parent / path
    for sub in ("pending", "running", "done", "failed"):
        (path / sub).mkdir(parents=True, exist_ok=True)
    return path


def _status_path(jobs_dir: Path, job_id: str, status: JobStatus) -> Path:
    return jobs_dir / status / f"{job_id}.json"


def _find_job_path(jobs_dir: Path, job_id: str) -> Path | None:
    for status in ("pending", "running", "done", "failed"):
        path = _status_path(jobs_dir, job_id, status)  # type: ignore[arg-type]
        if path.is_file():
            return path
    return None


def enqueue_job(
    job_type: JobType,
    payload: dict[str, Any],
    *,
    jobs_dir: Path | None = None,
) -> JobRecord:
    root = resolve_jobs_dir(jobs_dir)
    job_id = str(uuid.uuid4())
    record = JobRecord(id=job_id, type=job_type, payload=payload)
    path = _status_path(root, job_id, "pending")
    path.write_text(json.dumps(asdict(record), indent=2), encoding="utf-8")
    logger.info("enqueued analytics job %s type=%s", job_id, job_type)
    return record


def load_job(jobs_dir: Path, job_id: str) -> JobRecord | None:
    path = _find_job_path(jobs_dir, job_id)
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return JobRecord(**data)
    except (OSError, json.JSONDecodeError, TypeError):
        logger.exception("failed to load job %s", job_id)
        return None


def _write_job(jobs_dir: Path, record: JobRecord, status: JobStatus) -> None:
    record.status = status
    record.updated_at = datetime.now(tz=UTC).isoformat()
    dest = _status_path(jobs_dir, record.id, status)
    dest.write_text(json.dumps(asdict(record), indent=2), encoding="utf-8")


def list_jobs(jobs_dir: Path, *, limit: int = 50) -> list[JobRecord]:
    rows: list[tuple[float, JobRecord]] = []
    for status in ("running", "pending", "done", "failed"):
        folder = jobs_dir / status
        if not folder.is_dir():
            continue
        for path in folder.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                rec = JobRecord(**data)
                rows.append((path.stat().st_mtime, rec))
            except (OSError, json.JSONDecodeError, TypeError):
                continue
    rows.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in rows[:limit]]


def claim_next_job(jobs_dir: Path) -> tuple[JobRecord, Path] | None:
    pending = jobs_dir / "pending"
    if not pending.is_dir():
        return None
    candidates = sorted(pending.glob("*.json"), key=lambda p: p.stat().st_mtime)
    for src in candidates:
        try:
            data = json.loads(src.read_text(encoding="utf-8"))
            record = JobRecord(**data)
        except (OSError, json.JSONDecodeError, TypeError):
            logger.warning("skipping corrupt pending job %s", src.name)
            continue
        running = _status_path(jobs_dir, record.id, "running")
        try:
            os.replace(src, running)
        except OSError:
            continue
        record.status = "running"
        record.updated_at = datetime.now(tz=UTC).isoformat()
        running.write_text(json.dumps(asdict(record), indent=2), encoding="utf-8")
        return record, running
    return None


def finish_job(
    jobs_dir: Path,
    record: JobRecord,
    *,
    running_path: Path,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    record.result = result
    record.error = error
    record.progress = 1.0 if error is None else record.progress
    status: JobStatus = "failed" if error else "done"
    _write_job(jobs_dir, record, status)
    try:
        running_path.unlink(missing_ok=True)
    except OSError:
        logger.exception("failed to remove running job file %s", running_path)


def execute_job(record: JobRecord) -> dict[str, Any]:
    """Run one job synchronously (used by the worker process)."""
    if record.type == "backtest_run":
        return _run_backtest_job(record.payload)
    if record.type == "kline_download":
        return _run_download_job(record.payload)
    if record.type == "report_build":
        return _run_report_job(record.payload)
    if record.type == "mm_universe_scan":
        return _run_mm_universe_scan_job(record.payload)
    raise ValueError(f"unknown job type: {record.type}")


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _run_backtest_job(payload: dict[str, Any]) -> dict[str, Any]:
    from analytics.backtest.runner import run_backtest
    from common.config import get_settings

    settings = get_settings()
    merged = settings.model_copy(
        update={
            "strategy": payload.get("strategy", settings.strategy),
            **(payload.get("settings_overrides") or {}),
        },
    )
    persist = payload.get("persist_dir")
    persist_dir = None
    if persist:
        p = Path(persist)
        if not p.is_absolute():
            p = backend_data_root().parent / p
        persist_dir = p
    result = run_backtest(
        merged,
        dataset=str(payload.get("dataset", "library")),
        start=_parse_dt(payload.get("start")),
        end=_parse_dt(payload.get("end")),
        persist_dir=persist_dir,
    )
    return {
        "run_id": result.run_id,
        "strategy": result.strategy,
        "dataset": result.dataset,
        "bar_count": result.bar_count,
        "total_return_pct": result.metrics.total_return_pct,
        "trade_count": result.metrics.trade_count,
    }


def _run_download_job(payload: dict[str, Any]) -> dict[str, Any]:
    import asyncio

    from analytics.data_loader import download_klines
    from common.config import get_settings

    settings = get_settings()
    results = asyncio.run(
        download_klines(
            list(payload.get("symbols") or []),
            interval=str(payload.get("interval", "1m")),
            days=int(payload.get("days", 7)),
            settings=settings,
        ),
    )
    return {
        "ok": True,
        "downloaded": [
            {"symbol": r.symbol, "interval": r.interval, "rows": r.rows, "path": r.path}
            for r in results
        ],
    }


def _run_mm_universe_scan_job(payload: dict[str, Any]) -> dict[str, Any]:
    import asyncio

    from analytics.mm_universe_scanner import scan_mm_universe, write_mm_universe_report
    from common.config import get_settings

    settings = get_settings()
    overrides = payload.get("settings_overrides") or {}
    if overrides:
        settings = settings.model_copy(update=overrides)
    report = asyncio.run(
        scan_mm_universe(
            settings,
            sample=bool(payload.get("sample", True)),
        ),
    )
    path = write_mm_universe_report(report)
    top = [
        {
            "symbol": r.symbol,
            "score": r.score,
            "eligible": r.eligible,
            "median_spread_bps": r.median_spread_bps,
            "spread_cv": r.spread_cv,
            "reject_reason": r.reject_reason,
        }
        for r in sorted(report.rankings, key=lambda x: x.score, reverse=True)[:30]
    ]
    result: dict[str, Any] = {
        "path": str(path),
        "recommended": report.recommended,
        "candidates_scanned": report.candidates_scanned,
        "top_rankings": top,
    }
    if report.thresholds is not None:
        result["thresholds"] = asdict(report.thresholds)
    return result


def _run_report_job(payload: dict[str, Any]) -> dict[str, Any]:
    from analytics.daily_report import build_report

    run_dir = Path(str(payload["run_dir"]))
    if not run_dir.is_absolute():
        run_dir = backend_data_root().parent / run_dir
    report = build_report(run_dir)
    return {
        "run_dir": report.run_dir,
        "trade_count": report.trade_count,
        "realized_pnl": report.realized_pnl,
        "avg_slippage_bps": report.avg_slippage_bps,
        "breaker_events": report.breaker_events,
        "reconcile_mismatches": report.reconcile_mismatches,
        "notes": report.notes,
    }


def poll_interval_sec() -> float:
    return 0.25
