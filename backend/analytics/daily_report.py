"""Session report from run archive JSONL + WAL."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from common.enums import EventType

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DailyReport:
    run_dir: str
    trade_count: int = 0
    realized_pnl: float = 0.0
    avg_slippage_bps: float = 0.0
    breaker_events: int = 0
    reconcile_mismatches: int = 0
    notes: list[str] = field(default_factory=list)


def build_report(run_dir: Path) -> DailyReport:
    report = DailyReport(run_dir=str(run_dir))
    if not run_dir.is_dir():
        report.notes.append("run_dir_missing")
        return report

    fills_path = run_dir / "fills.jsonl"
    if fills_path.is_file():
        for line in fills_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                data = rec.get("data") or {}
                report.trade_count += 1
                rp = data.get("realized_pnl")
                if rp is not None:
                    report.realized_pnl += float(rp)
            except (json.JSONDecodeError, TypeError, ValueError):
                continue

    exec_path = run_dir / "executions.jsonl"
    slips: list[float] = []
    if exec_path.is_file():
        for line in exec_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                data = rec.get("data") or {}
                slip = data.get("fee_adjusted_slippage_bps", data.get("slippage_bps"))
                if slip is not None:
                    slips.append(float(slip))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
    if slips:
        report.avg_slippage_bps = sum(slips) / len(slips)

    wal = run_dir / "events.wal.jsonl"
    if wal.is_file():
        for line in wal.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                if rec.get("type") == EventType.BREAKER.value:
                    report.breaker_events += 1
                payload = rec.get("data") or {}
                if payload.get("kind") == "order_reconcile":
                    if not payload.get("ok", True):
                        report.reconcile_mismatches += 1
            except json.JSONDecodeError:
                continue

    return report


def find_latest_run(persist_base: Path) -> Path | None:
    if not persist_base.is_dir():
        return None
    dirs = [p for p in persist_base.iterdir() if p.is_dir()]
    if not dirs:
        return None
    return max(dirs, key=lambda p: p.stat().st_mtime)
