"""Session log API merges archive + in-memory tail."""

from __future__ import annotations

import json
from pathlib import Path

from api.routes.logs import LogDTO, buffer, merge_session_logs


def test_merge_session_logs_reads_archive_and_buffer(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    lines = [
        {"ts": 100.0, "type": "log", "data": {"level": "info", "msg": "boot", "logger": "main"}},
        {"ts": 101.0, "type": "log", "data": {"level": "warn", "msg": "warm", "logger": "eng"}},
    ]
    (run_dir / "logs.jsonl").write_text(
        "\n".join(json.dumps(row) for row in lines) + "\n",
        encoding="utf-8",
    )

    buf = buffer()
    buf.clear()
    buf.append(
        LogDTO(ts="00:01:45", level="info", msg="warm", logger="eng"),
    )
    buf.append(
        LogDTO(ts="00:01:50", level="error", msg="live tail", logger="eng"),
    )

    rows = merge_session_logs(run_dir, buf)
    assert [r.msg for r in rows] == ["live tail", "warm", "boot"]
    assert rows[0].level == "error"


def test_merge_session_logs_limit_newest(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "logs.jsonl").write_text(
        json.dumps({"ts": 1.0, "type": "log", "data": {"level": "info", "msg": "a"}}) + "\n"
        + json.dumps({"ts": 2.0, "type": "log", "data": {"level": "info", "msg": "b"}}) + "\n",
        encoding="utf-8",
    )
    rows = merge_session_logs(run_dir, [], limit=1)
    assert len(rows) == 1
    assert rows[0].msg == "b"
