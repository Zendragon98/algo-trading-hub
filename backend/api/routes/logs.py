"""GET /api/logs.

Returns full session history from the active run's ``logs.jsonl`` when
persistence is enabled, merged with any lines not yet flushed to disk.
Falls back to an in-memory ring buffer when no archive exists.
The dashboard streams new lines over /ws; call this on hydrate/refresh.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends

from engine.core.engine import Engine

from ..dependencies import get_engine
from ..schemas import LogDTO

router = APIRouter(prefix="/api", tags=["logs"])

# Tail buffer for lines not yet on disk and when PERSIST_ENABLED=false.
_BUFFER: deque[LogDTO] = deque(maxlen=10_000)


def _fmt(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%H:%M:%S")


def _dedupe_key(dto: LogDTO) -> tuple[str, str]:
    # Message + logger — archive and in-memory tail can format ts differently.
    return (dto.msg, dto.logger or "")


def _payload_to_dto(ts: float, payload: dict) -> LogDTO:
    return LogDTO(
        ts=_fmt(ts),
        level=payload.get("level", "info"),  # type: ignore[arg-type]
        msg=payload.get("msg", "") or payload.get("message", ""),
        logger=payload.get("logger"),
    )


def _read_archive_logs(run_dir: Path) -> list[LogDTO]:
    path = run_dir / "logs.jsonl"
    if not path.is_file():
        return []
    rows: list[LogDTO] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") != "log":
                continue
            data = rec.get("data")
            if not isinstance(data, dict):
                continue
            ts = rec.get("ts")
            if not isinstance(ts, (int, float)):
                continue
            rows.append(_payload_to_dto(float(ts), data))
    return rows


def merge_session_logs(
    archive_dir: Path | None,
    buffer: Iterable[LogDTO],
    *,
    limit: int = 0,
) -> list[LogDTO]:
    """Chronological merge (oldest first), returned newest-first for the UI."""
    seen: set[tuple[str, str, str]] = set()
    merged: list[LogDTO] = []

    if archive_dir is not None:
        for dto in _read_archive_logs(archive_dir):
            key = _dedupe_key(dto)
            if key in seen:
                continue
            seen.add(key)
            merged.append(dto)

    for dto in buffer:
        key = _dedupe_key(dto)
        if key in seen:
            continue
        seen.add(key)
        merged.append(dto)

    merged.reverse()
    if limit > 0:
        return merged[:limit]
    return merged


@router.get("/logs", response_model=list[LogDTO])
def logs(engine: Engine = Depends(get_engine), limit: int = 0) -> list[LogDTO]:
    """Full session history by default (``limit=0``). Pass ``limit`` to cap newest N."""
    return merge_session_logs(engine.event_archive_dir, _BUFFER, limit=limit)


def buffer() -> deque[LogDTO]:
    """Expose the buffer so api.server can feed it from the bus task."""
    return _BUFFER
