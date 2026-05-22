"""Structured logging.

Sets up a single root logger plus an EventBus sink so every log line
above INFO also lands on the dashboard's live log panel. Optionally
also tees every record to a rotating file under the per-run archive
folder so a session can be reviewed after the engine has stopped.
Importing this module is idempotent; call `configure_logging()` once
at process start.
"""

from __future__ import annotations

import logging
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .enums import EventType, LogLevel

if TYPE_CHECKING:
    from .events import EventBus
    from .types import Signal


_CONFIGURED = False
_PENDING_BUS: list[dict[str, Any]] = []

_LOG_LEVEL_NAMES = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}


def resolve_log_level(name: str) -> int:
    """Map ``Settings.log_level`` / ``LOG_LEVEL`` to a ``logging`` constant."""
    key = (name or "info").strip().lower()
    try:
        return _LOG_LEVEL_NAMES[key]
    except KeyError as exc:
        allowed = ", ".join(sorted({"debug", "info", "warning", "error", "critical"}))
        raise ValueError(f"log_level must be one of: {allowed}") from exc


def apply_log_level(level: int) -> None:
    """Update root + handler thresholds without re-running ``configure_logging``."""
    root = logging.getLogger()
    root.setLevel(level)
    for handler in root.handlers:
        handler.setLevel(level)


class _BusHandler(logging.Handler):
    """Logging handler that mirrors records onto the EventBus.

    The bus is in-process, so we publish synchronously by scheduling
    `bus.publish` onto the running event loop. Records emitted before
    the loop runs are queued and flushed via ``flush_pending_bus_logs``.
    """

    def __init__(self, bus: "EventBus", *, level: int = logging.INFO) -> None:
        super().__init__(level=level)
        self._bus = bus

    def emit(self, record: logging.LogRecord) -> None:
        import asyncio

        from .events import Event

        explicit = getattr(record, "_dashboard_level", None)
        if explicit is not None:
            level_value = str(explicit)
        else:
            level_value = _RECORD_TO_LEVEL.get(record.levelno, LogLevel.INFO).value

        payload = {
            "level": level_value,
            "msg": record.getMessage(),
            "logger": record.name,
        }

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _PENDING_BUS.append(payload)
            return

        event = Event(type=EventType.LOG, payload=payload)
        loop.create_task(self._bus.publish(event))


async def flush_pending_bus_logs(bus: "EventBus") -> None:
    """Publish log lines that were emitted before the asyncio loop started."""
    if not _PENDING_BUS:
        return
    from .events import Event

    pending = list(_PENDING_BUS)
    _PENDING_BUS.clear()
    now = time.time()
    for payload in pending:
        await bus.publish(Event(type=EventType.LOG, payload=payload, ts=now))


_RECORD_TO_LEVEL = {
    logging.DEBUG: LogLevel.DEBUG,
    logging.INFO: LogLevel.INFO,
    logging.WARNING: LogLevel.WARN,
    logging.ERROR: LogLevel.ERROR,
    logging.CRITICAL: LogLevel.ERROR,
}


def configure_logging(
    bus: "EventBus | None" = None,
    level: int = logging.INFO,
    log_file: Path | str | None = None,
    log_file_max_bytes: int = 10_000_000,
    log_file_backup_count: int = 5,
) -> None:
    """Configure the root logger.

    Args:
        bus: optional EventBus. When provided, records at ``level`` or
            above are forwarded as `LOG` events for the dashboard.
        level: root logger level (``logging.DEBUG``, ``logging.INFO``, …).
            Defaults to INFO. Set via ``LOG_LEVEL=debug`` in settings.
        log_file: optional path to a file that should also receive every
            log record (full ISO timestamps, no colour). Rotates on size.
        log_file_max_bytes: rotation threshold per file.
        log_file_backup_count: number of rotated backups to keep.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    # Windows consoles often default to cp1252; reconfigure so breaker
    # detail strings and other UTF-8 log lines do not raise on emit.
    console_stream = sys.stdout
    if hasattr(console_stream, "reconfigure"):
        try:
            console_stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError) as exc:
            logging.getLogger(__name__).debug(
                "console UTF-8 reconfigure skipped: %s", exc,
            )
    console = logging.StreamHandler(console_stream)
    console.setLevel(level)
    console.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-5s %(name)s :: %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(console)

    if log_file is not None:
        # Full timestamps + module + line for the on-disk archive so the
        # log can be grepped/diffed across runs without losing context.
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            filename=str(path),
            maxBytes=log_file_max_bytes,
            backupCount=log_file_backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s.%(msecs)03d %(levelname)-5s %(name)s:%(lineno)d :: %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
        root.addHandler(file_handler)

    if bus is not None:
        root.addHandler(_BusHandler(bus, level=level))

    # Quiet noisy third parties.
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    _CONFIGURED = True


def reset_logging_for_tests() -> None:
    """Allow tests to reconfigure logging more than once per process."""
    global _CONFIGURED
    _CONFIGURED = False
    _PENDING_BUS.clear()


def signal_log(logger: logging.Logger, msg: str) -> None:
    """Emit a LOG-stream-only `signal`-level message.

    Used by strategies to highlight signal generation on the dashboard
    in a colour distinct from regular INFO traffic. Falls back to a
    normal info log if no bus handler is attached.
    """
    # Reuse the standard logger so file/line metadata is preserved, but
    # tag the record so the bus handler can map it to LogLevel.SIGNAL.
    logger.info(msg, extra={"_dashboard_level": LogLevel.SIGNAL.value})


def signal_log_emit(
    logger: logging.Logger,
    headline: str,
    *,
    reason: str = "",
) -> None:
    """Emit a signal-level log with optional strategy ``reason`` appended.

    Strategies attach human-readable context on ``Signal.reason``; this
    helper keeps LIVE LOG, the on-disk archive, and ``logs.jsonl`` aligned.
    """
    msg = headline if not reason else f"{headline} | {reason}"
    signal_log(logger, msg)


def group_signal_log(
    logger: logging.Logger,
    group_id: str,
    headline: str,
    legs: list["Signal"],
) -> None:
    """SIG-level log for pair/group dispatch with leg context."""
    if not legs:
        signal_log_emit(logger, f"group {group_id} {headline}")
        return
    reason = " | ".join(f"{leg.symbol}:{leg.reason}" for leg in legs[:4])
    signal_log_emit(logger, f"group {group_id} {headline}", reason=reason)
