"""Run-directory, journal, recorder, and WAL recovery wiring for ``main.py``.

``JOURNAL_ENABLED`` can create a run archive even when ``PERSIST_ENABLED`` or
``LOG_FILE_ENABLED`` are off, because WAL recovery needs a durable location.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from common.config import Settings
from common.events import EventBus
from engine.persistence.event_recorder import EventRecorder, RecorderConfig, make_run_dir
from engine.persistence.journal import EventJournal, find_previous_wal
from engine.persistence.market_capture import MarketBarCapturer

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RunBootstrap:
    run_dir: Path | None
    log_file: Path | None
    journal: EventJournal | None
    recorder: EventRecorder | None
    market_capturer: MarketBarCapturer | None
    recovery_wal: Path | None


def resolve_run_dir(settings: Settings, backend_root: Path) -> Path | None:
    """Create a timestamped run folder when any persistence feature needs it."""
    if not (
        settings.persist_enabled
        or settings.log_file_enabled
        or settings.journal_enabled
    ):
        return None
    base = Path(settings.persist_dir)
    if not base.is_absolute():
        base = backend_root / base
    return make_run_dir(base)


def resolve_recovery_wal(
    settings: Settings,
    backend_root: Path,
    *,
    run_dir: Path | None,
) -> Path | None:
    if not settings.recover_on_start or run_dir is None:
        return None
    persist_base = Path(settings.persist_dir)
    if not persist_base.is_absolute():
        persist_base = backend_root / persist_base
    wal = find_previous_wal(persist_base, exclude_dir=run_dir)
    if wal is not None:
        logger.info("WAL recovery source: %s", wal)
    return wal


def open_journal(
    settings: Settings,
    bus: EventBus,
    run_dir: Path | None,
) -> EventJournal | None:
    if not settings.journal_enabled or run_dir is None:
        return None
    journal = EventJournal(run_dir=run_dir, run_id=run_dir.name)
    journal.open(datetime.now(tz=UTC).isoformat())
    bus.attach_journal(journal)
    return journal


async def start_recorder(
    settings: Settings,
    bus: EventBus,
    run_dir: Path | None,
) -> EventRecorder | None:
    if not settings.persist_enabled or run_dir is None:
        return None
    recorder = EventRecorder(
        bus=bus,
        config=RecorderConfig(
            run_dir=run_dir,
            record_ticks=settings.persist_record_ticks,
        ),
    )
    await recorder.start()
    return recorder


async def bootstrap_run(
    settings: Settings,
    bus: EventBus,
    backend_root: Path,
) -> RunBootstrap:
    """Resolve run dir, journal, recorder, and optional WAL recovery path."""
    run_dir = resolve_run_dir(settings, backend_root)
    if run_dir is not None:
        logger.info("run directory: %s", run_dir)
    log_file = (
        run_dir / "app.log"
        if (run_dir is not None and settings.log_file_enabled)
        else None
    )
    if log_file is not None:
        logger.info("app log file: %s", log_file)
    journal = open_journal(settings, bus, run_dir)
    if journal is not None:
        logger.info("event journal enabled")
        await bus.start_journal_writer(flush_every_sec=1.0)
    recorder = await start_recorder(settings, bus, run_dir)
    if recorder is not None:
        logger.info("event recorder enabled (dir=%s)", recorder.run_dir)
    recovery_wal = resolve_recovery_wal(settings, backend_root, run_dir=run_dir)
    return RunBootstrap(
        run_dir=run_dir,
        log_file=log_file,
        journal=journal,
        recorder=recorder,
        market_capturer=None,
        recovery_wal=recovery_wal,
    )


async def shutdown_bootstrap(bootstrap: RunBootstrap, *, bus: EventBus | None = None) -> None:
    if bootstrap.market_capturer is not None:
        try:
            bootstrap.market_capturer.flush()
        except Exception:  # noqa: BLE001
            logger.exception("market capture flush failed during shutdown")
    if bootstrap.recorder is not None:
        await bootstrap.recorder.stop()
    if bus is not None:
        await bus.stop_journal_writer()
    if bootstrap.journal is not None:
        bootstrap.journal.close()
