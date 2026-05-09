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
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING

from .enums import EventType, LogLevel

if TYPE_CHECKING:
    from .events import EventBus


_CONFIGURED = False


class _BusHandler(logging.Handler):
    """Logging handler that mirrors records onto the EventBus.

    The bus is in-process, so we publish synchronously by scheduling
    `bus.publish` onto the running event loop. If the loop is not yet
    running (e.g. logs emitted during module import), we silently drop
    the mirror to the bus; stdout still receives the message.
    """

    def __init__(self, bus: "EventBus") -> None:
        super().__init__(level=logging.INFO)
        self._bus = bus

    def emit(self, record: logging.LogRecord) -> None:
        import asyncio

        from .events import Event

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        # `signal_log` stamps a custom dashboard level via `extra=`; honour
        # it so signal lines render in their own colour on the UI.
        explicit = getattr(record, "_dashboard_level", None)
        if explicit is not None:
            level_value = str(explicit)
        else:
            level_value = _RECORD_TO_LEVEL.get(record.levelno, LogLevel.INFO).value

        event = Event(
            type=EventType.LOG,
            payload={
                "level": level_value,
                "msg": record.getMessage(),
                "logger": record.name,
            },
        )
        loop.create_task(self._bus.publish(event))


_RECORD_TO_LEVEL = {
    logging.DEBUG: LogLevel.INFO,
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
        bus: optional EventBus. When provided, every record at INFO or
            above is also forwarded as a `LOG` event so the dashboard
            can render it.
        level: root logger level. Defaults to INFO.
        log_file: optional path to a file that should also receive every
            log record (full ISO timestamps, no colour). Rotates on size.
        log_file_max_bytes: rotation threshold per file.
        log_file_backup_count: number of rotated backups to keep.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    console = logging.StreamHandler(sys.stdout)
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
        file_handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s.%(msecs)03d %(levelname)-5s %(name)s:%(lineno)d :: %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
        root.addHandler(file_handler)

    if bus is not None:
        root.addHandler(_BusHandler(bus))

    # Quiet noisy third parties.
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    _CONFIGURED = True


def reset_logging_for_tests() -> None:
    """Allow tests to reconfigure logging more than once per process."""
    global _CONFIGURED
    _CONFIGURED = False


def signal_log(logger: logging.Logger, msg: str) -> None:
    """Emit a LOG-stream-only `signal`-level message.

    Used by strategies to highlight signal generation on the dashboard
    in a colour distinct from regular INFO traffic. Falls back to a
    normal info log if no bus handler is attached.
    """
    # Reuse the standard logger so file/line metadata is preserved, but
    # tag the record so the bus handler can map it to LogLevel.SIGNAL.
    logger.info(msg, extra={"_dashboard_level": LogLevel.SIGNAL.value})
