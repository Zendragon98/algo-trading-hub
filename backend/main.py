"""Backend entrypoint: runs the engine + FastAPI in one process.

Both the engine and uvicorn share the asyncio event loop so the API can
read engine state without any locks or IPC. We start the engine first
(failing fast on bad credentials) then hand control to uvicorn.

Usage:
    python main.py                      # default host/port from .env
    python main.py --no-engine          # serve API with the engine paused
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal as os_signal
from contextlib import suppress
from pathlib import Path

import uvicorn

from api.server import create_app
from common.config import get_settings
from common.enums import TradingMode
from common.events import EventBus
from common.logging import configure_logging
from engine.core.engine import Engine
from engine.persistence.event_recorder import EventRecorder, RecorderConfig, make_run_dir
from engine.strategies.pairs_trading import PairsTradingStrategy
from gateways.factory import create_gateway

logger = logging.getLogger(__name__)


def _log_mode_banner(settings) -> None:
    """Print a hard-to-miss banner so LIVE mode is always confirmed in the log."""
    if settings.trading_mode is TradingMode.LIVE:
        logger.warning("=" * 64)
        logger.warning(
            "TRADING_MODE=LIVE  venue=%s  — REAL MONEY. synthetic impact disabled.",
            settings.venue,
        )
        logger.warning("=" * 64)
    else:
        logger.info(
            "TRADING_MODE=paper  venue=%s  — synthetic impact enabled (testnet/demo).",
            settings.venue,
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ALPHA-7 trading backend")
    parser.add_argument(
        "--no-engine",
        action="store_true",
        help="Boot the API but leave the engine stopped (manual /control/start)",
    )
    return parser.parse_args()


async def _run() -> None:
    args = _parse_args()
    settings = get_settings()
    bus = EventBus()

    # Per-run archive: timestamped folder under PERSIST_DIR (relative paths
    # are resolved against backend/ so every launcher writes to the same
    # spot regardless of cwd). app.log + JSONL streams live here.
    run_dir: Path | None = None
    if settings.persist_enabled or settings.log_file_enabled:
        base = Path(settings.persist_dir)
        if not base.is_absolute():
            base = Path(__file__).resolve().parent / base
        run_dir = make_run_dir(base)

    log_file = (
        run_dir / "app.log"
        if (run_dir is not None and settings.log_file_enabled)
        else None
    )
    configure_logging(
        bus=bus,
        log_file=log_file,
        log_file_max_bytes=settings.log_file_max_bytes,
        log_file_backup_count=settings.log_file_backup_count,
    )
    logger.info("config loaded from %s", settings.env_path)
    _log_mode_banner(settings)
    if run_dir is not None:
        logger.info("run archive: %s", run_dir)

    recorder: EventRecorder | None = None
    if settings.persist_enabled and run_dir is not None:
        recorder = EventRecorder(
            bus=bus,
            config=RecorderConfig(
                run_dir=run_dir,
                record_ticks=settings.persist_record_ticks,
            ),
        )
        await recorder.start()

    gateway = create_gateway(settings)
    strategies = [PairsTradingStrategy(settings)]
    engine = Engine(settings=settings, bus=bus, gateway=gateway, strategies=strategies)

    if not args.no_engine:
        try:
            await engine.start()
        except Exception:
            logger.exception("engine failed to start; exiting")
            if recorder is not None:
                await recorder.stop()
            return

    app = create_app(engine, bus, settings)

    config = uvicorn.Config(
        app=app,
        host=settings.api_host,
        port=settings.api_port,
        log_level="info",
        # Disable uvicorn's signal handlers; we install our own to ensure
        # the engine stops cleanly before the server tears down.
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # type: ignore[assignment]

    stop_event = asyncio.Event()

    def _request_shutdown() -> None:
        logger.info("shutdown signal received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(os_signal, sig_name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, _request_shutdown)
        except NotImplementedError:
            # Windows: only SIGINT works under add_signal_handler. The
            # KeyboardInterrupt fallback below handles Ctrl+C anyway.
            pass

    server_task = asyncio.create_task(server.serve(), name="uvicorn")
    try:
        done, _pending = await asyncio.wait(
            [server_task, asyncio.create_task(stop_event.wait(), name="shutdown-watch")],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in done:
            with suppress(asyncio.CancelledError):
                await task
    except KeyboardInterrupt:
        pass
    finally:
        server.should_exit = True
        with suppress(asyncio.CancelledError, Exception):
            await server_task
        await engine.stop()
        if recorder is not None:
            await recorder.stop()


def main() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
