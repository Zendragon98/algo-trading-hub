"""Backtest API enqueues jobs instead of blocking the trading loop."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from analytics.jobs import load_job, resolve_jobs_dir
from api.server import create_app
from common.config import Settings
from common.events import EventBus


class _StubEngine:
    def __init__(self) -> None:
        self._state = type("S", (), {"status": "stopped"})()


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    bus = EventBus()
    jobs_dir = resolve_jobs_dir(tmp_path / "jobs")
    settings = Settings(
        analytics_worker_enabled=True,
        analytics_worker_mode="external",
        analytics_jobs_dir=str(jobs_dir),
    )
    app = create_app(_StubEngine(), bus, settings)  # type: ignore[arg-type]
    app.state.analytics_jobs_dir = jobs_dir
    return TestClient(app)


def test_run_backtest_returns_202_and_job(client: TestClient, tmp_path: Path) -> None:
    jobs_dir = resolve_jobs_dir(tmp_path / "jobs")
    resp = client.post(
        "/api/backtest/run",
        json={"strategy": "pairs_trading_usdt_usdc", "dataset": "library"},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert "job_id" in body
    job = load_job(jobs_dir, body["job_id"])
    assert job is not None
    assert job.type == "backtest_run"
    assert job.status == "pending"


def test_download_returns_202(client: TestClient, tmp_path: Path) -> None:
    jobs_dir = resolve_jobs_dir(tmp_path / "jobs")
    resp = client.post(
        "/api/backtest/download",
        json={"symbols": ["BTCUSDT"], "interval": "1m", "days": 1},
    )
    assert resp.status_code == 202
    job = load_job(jobs_dir, resp.json()["job_id"])
    assert job is not None
    assert job.type == "kline_download"
