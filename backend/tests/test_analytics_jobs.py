"""Filesystem analytics job queue."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from analytics.jobs import (
    claim_next_job,
    enqueue_job,
    execute_job,
    finish_job,
    load_job,
    resolve_jobs_dir,
)


@pytest.fixture
def jobs_dir(tmp_path: Path) -> Path:
    return resolve_jobs_dir(tmp_path / "jobs")


def test_enqueue_and_worker_roundtrip(jobs_dir: Path) -> None:
    record = enqueue_job(
        "kline_download",
        {"symbols": ["BTCUSDT"], "interval": "1m", "days": 1},
        jobs_dir=jobs_dir,
    )
    claimed = claim_next_job(jobs_dir)
    assert claimed is not None
    job, running_path = claimed
    assert job.id == record.id
    assert job.status == "running"
    finish_job(jobs_dir, job, running_path=running_path, result={"ok": True})
    loaded = load_job(jobs_dir, record.id)
    assert loaded is not None
    assert loaded.status == "done"
    assert loaded.result == {"ok": True}


def test_failed_job_records_error(jobs_dir: Path) -> None:
    record = enqueue_job("backtest_run", {"strategy": "missing"}, jobs_dir=jobs_dir)
    claimed = claim_next_job(jobs_dir)
    assert claimed is not None
    job, running_path = claimed
    try:
        execute_job(job)
        pytest.fail("expected execute_job to raise")
    except Exception as exc:  # noqa: BLE001
        finish_job(jobs_dir, job, running_path=running_path, error=str(exc))
    loaded = load_job(jobs_dir, record.id)
    assert loaded is not None
    assert loaded.status == "failed"
    assert loaded.error


def test_pending_job_file_format(jobs_dir: Path) -> None:
    record = enqueue_job("report_build", {"run_dir": "data/runs/x"}, jobs_dir=jobs_dir)
    path = jobs_dir / "pending" / f"{record.id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["type"] == "report_build"
    assert data["status"] == "pending"
