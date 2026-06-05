"""Tests for per-strategy PnL report."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from analytics.strategy_pnl_report import analyze_run


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n",
        encoding="utf-8",
    )


def test_strategy_pnl_splits_netted_contributions(tmp_path: Path) -> None:
    run_dir = tmp_path / "2026-test-run"
    run_dir.mkdir()

    _write_jsonl(
        run_dir / "executions.jsonl",
        [
            {
                "ts": 1.0,
                "type": "execution_report",
                "data": {
                    "parent_id": "P-abc123",
                    "strategy_name": "__netted__",
                    "strategy_contributions": {
                        "flow_momentum": 0.01,
                        "sma_crossover": 0.005,
                    },
                },
            },
        ],
    )
    _write_jsonl(
        run_dir / "fills.jsonl",
        [
            {
                "ts": 2.0,
                "type": "fill",
                "data": {
                    "parent_id": "P-abc123",
                    "action": "close",
                    "pnl": -100.0,
                },
            },
        ],
    )

    report = analyze_run(run_dir)
    assert report is not None
    flow = report.strategies["flow_momentum"]
    sma = report.strategies["sma_crossover"]
    assert flow.realized_pnl_usd == pytest.approx(-66.666666, rel=1e-4)
    assert sma.realized_pnl_usd == pytest.approx(-33.333333, rel=1e-4)


def test_strategy_pnl_tags_mm_quotes(tmp_path: Path) -> None:
    run_dir = tmp_path / "mm-run"
    run_dir.mkdir()
    _write_jsonl(
        run_dir / "fills.jsonl",
        [
            {
                "ts": 1.0,
                "type": "fill",
                "data": {
                    "parent_id": "Q-xyz",
                    "action": "close",
                    "pnl": 2.5,
                },
            },
        ],
    )
    report = analyze_run(run_dir)
    assert report is not None
    assert report.strategies["market_making_v2"].realized_pnl_usd == pytest.approx(2.5)
