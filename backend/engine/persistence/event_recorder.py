"""Stream the EventBus to disk as JSONL, one file per event type.

A single asyncio task subscribes to the bus and demultiplexes events
into separate `.jsonl` files inside the per-run archive folder. Each
line is a self-contained JSON object:

    {"ts": <epoch>, "type": "fill", "data": {...}}

This is the same shape the WebSocket emits, so the same downstream
parsers can replay an offline session.

Files are opened lazily on first event and closed on `stop()`. Writes
are batched (line-buffered + periodic flush) so we never hit fsync per
event, but a SIGINT crash loses at most `flush_every_sec` worth of data.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import IO

from common.enums import EventType
from common.events import Event, EventBus

logger = logging.getLogger(__name__)


# Default mapping of event type -> on-disk filename. TICK is intentionally
# absent from `_DEFAULT_TYPES` because it's a firehose; opt in via the
# `record_ticks=True` constructor flag (and the `PERSIST_RECORD_TICKS`
# env var that backs it).
_FILENAMES: dict[EventType, str] = {
    EventType.TICK: "ticks.jsonl",
    EventType.FILL: "fills.jsonl",
    EventType.ORDER_UPDATE: "orders.jsonl",
    EventType.PARENT_UPDATE: "parents.jsonl",
    EventType.EXECUTION_REPORT: "executions.jsonl",
    EventType.POSITION: "positions.jsonl",
    EventType.EQUITY: "equity.jsonl",
    EventType.LOG: "logs.jsonl",
    EventType.STATUS: "status.jsonl",
    EventType.BREAKER: "breakers.jsonl",
}

_DEFAULT_TYPES: tuple[EventType, ...] = (
    EventType.FILL,
    EventType.ORDER_UPDATE,
    EventType.PARENT_UPDATE,
    EventType.EXECUTION_REPORT,
    EventType.POSITION,
    EventType.EQUITY,
    EventType.LOG,
    EventType.STATUS,
    EventType.BREAKER,
)


@dataclass(slots=True)
class RecorderConfig:
    run_dir: Path
    record_ticks: bool = False
    flush_every_sec: float = 1.0


class EventRecorder:
    """Bus subscriber that mirrors every event into per-type JSONL files."""

    def __init__(self, bus: EventBus, config: RecorderConfig) -> None:
        self._bus = bus
        self._cfg = config
        self._task: asyncio.Task[None] | None = None
        self._files: dict[EventType, IO[str]] = {}
        self._subscribed = asyncio.Event()

        types = list(_DEFAULT_TYPES)
        if config.record_ticks:
            types.append(EventType.TICK)
        self._types: tuple[EventType, ...] = tuple(types)

    # --- Lifecycle ---

    async def start(self) -> None:
        if self._task is not None:
            return
        self._cfg.run_dir.mkdir(parents=True, exist_ok=True)
        self._write_manifest()
        self._task = asyncio.create_task(self._run(), name="event-recorder")
        # Block the caller until the bus subscription is in place; otherwise
        # the very first publish() right after start() can race the
        # recorder and end up dropped.
        await self._subscribed.wait()
        logger.info("event recorder writing to %s", self._cfg.run_dir)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        self._task = None
        self._close_all()

    @property
    def run_dir(self) -> Path:
        return self._cfg.run_dir

    # --- Internal ---

    async def _run(self) -> None:
        try:
            async with self._bus.subscribe(types=self._types) as queue:
                self._subscribed.set()
                last_flush = asyncio.get_event_loop().time()
                while True:
                    event = await queue.get()
                    self._write_event(event)
                    now = asyncio.get_event_loop().time()
                    if now - last_flush >= self._cfg.flush_every_sec:
                        self._flush_all()
                        last_flush = now
        except asyncio.CancelledError:
            self._flush_all()
            raise
        except Exception:  # noqa: BLE001
            logger.exception("event recorder crashed")
            self._flush_all()
        finally:
            # Unblock callers waiting on start() even on early failure.
            self._subscribed.set()

    def _file_for(self, event_type: EventType) -> IO[str] | None:
        existing = self._files.get(event_type)
        if existing is not None:
            return existing
        filename = _FILENAMES.get(event_type)
        if filename is None:
            return None
        path = self._cfg.run_dir / filename
        # Append rather than truncate so a recorder restart inside the
        # same run (e.g. test) doesn't blow away earlier data.
        handle = path.open("a", encoding="utf-8")
        self._files[event_type] = handle
        return handle

    def _write_event(self, event: Event) -> None:
        handle = self._file_for(event.type)
        if handle is None:
            return
        record = {
            "ts": event.ts,
            "type": event.type.value,
            "data": event.payload,
        }
        try:
            handle.write(json.dumps(record, default=_json_default) + "\n")
        except (OSError, TypeError, ValueError):
            logger.exception("failed to persist event %s", event.type.value)

    def _flush_all(self) -> None:
        for handle in self._files.values():
            try:
                handle.flush()
            except OSError:
                logger.exception("flush failed")

    def _close_all(self) -> None:
        for handle in self._files.values():
            try:
                handle.flush()
                handle.close()
            except OSError:
                logger.exception("close failed")
        self._files.clear()

    def _write_manifest(self) -> None:
        """Drop a tiny manifest.json so a run folder is self-describing."""
        manifest = {
            "started_at": datetime.now(tz=timezone.utc).isoformat(),
            "record_ticks": self._cfg.record_ticks,
            "streams": [_FILENAMES[t] for t in self._types if t in _FILENAMES],
        }
        try:
            (self._cfg.run_dir / "manifest.json").write_text(
                json.dumps(manifest, indent=2), encoding="utf-8"
            )
        except OSError:
            logger.exception("failed to write run manifest")


def make_run_dir(base: Path | str) -> Path:
    """Create a timestamped subfolder under `base` for the current run.

    Run id is UTC ISO-ish but filesystem-safe: ``2026-05-09T13-30-15Z``.
    Returned path is created on disk so callers can write into it
    immediately.
    """
    stamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    path = Path(base) / stamp
    path.mkdir(parents=True, exist_ok=True)
    return path


def _json_default(value: object) -> object:
    """Best-effort JSON serialiser for stray non-primitive payloads."""
    if hasattr(value, "isoformat"):
        return value.isoformat()  # datetime / date
    if isinstance(value, (set, frozenset)):
        return list(value)
    return str(value)


__all__ = ["EventRecorder", "RecorderConfig", "make_run_dir"]


def types_recorded(record_ticks: bool) -> Iterable[EventType]:
    """Public helper used by tests + docs to describe what gets persisted."""
    yield from _DEFAULT_TYPES
    if record_ticks:
        yield EventType.TICK
