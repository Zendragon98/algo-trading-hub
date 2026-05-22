"""Signal log helpers."""

from __future__ import annotations

import asyncio
import logging

import pytest

from common.enums import EventType
from common.events import EventBus
from common.logging import (
    apply_log_level,
    configure_logging,
    flush_pending_bus_logs,
    reset_logging_for_tests,
    resolve_log_level,
    signal_log_emit,
)


def test_resolve_log_level_accepts_debug() -> None:
    assert resolve_log_level("debug") == logging.DEBUG
    assert resolve_log_level("INFO") == logging.INFO


@pytest.mark.asyncio
async def test_flush_pending_bus_logs_after_loop_starts() -> None:
    reset_logging_for_tests()
    bus = EventBus()
    configure_logging(bus=bus, level=logging.INFO)
    logging.getLogger("test.early").info("before loop")
    received: list[str] = []

    async def _collect() -> None:
        async with bus.subscribe(types=[EventType.LOG]) as queue:
            await flush_pending_bus_logs(bus)
            while True:
                event = await queue.get()
                received.append(event.payload.get("msg", ""))
                if len(received) >= 1:
                    break

    await asyncio.wait_for(_collect(), timeout=2.0)
    assert any("before loop" in m for m in received)


def test_apply_log_level_changes_root() -> None:
    reset_logging_for_tests()
    configure_logging(level=logging.INFO)
    apply_log_level(logging.DEBUG)
    assert logging.getLogger().level == logging.DEBUG


def test_configure_logging_emits_debug_when_level_debug() -> None:
    reset_logging_for_tests()
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    configure_logging(level=logging.DEBUG)
    logger = logging.getLogger("test.debug")
    cap = _Capture()
    cap.setLevel(logging.DEBUG)
    logger.addHandler(cap)
    logger.debug("low-level probe")
    assert any("low-level probe" in r.getMessage() for r in records)


def test_signal_log_emit_appends_reason() -> None:
    reset_logging_for_tests()
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    configure_logging()
    logger = logging.getLogger("test.signal")
    cap = _Capture()
    logger.addHandler(cap)

    signal_log_emit(logger, "BLEND open -> BUY BTCUSDT", reason="blend_long score=0.4")
    assert len(records) == 1
    assert "BLEND open -> BUY BTCUSDT" in records[0].getMessage()
    assert "blend_long score=0.4" in records[0].getMessage()
    assert records[0].__dict__.get("_dashboard_level") == "signal"
