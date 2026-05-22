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
import sys
from contextlib import suppress
from pathlib import Path

if sys.platform != "win32":
    try:
        import uvloop

        uvloop.install()
    except ImportError:
        pass

import uvicorn

from analytics.jobs import resolve_jobs_dir
from analytics.worker_supervisor import AnalyticsWorkerSupervisor
from api.server import create_app
from common.config import get_settings, normalize_strategy_name
from common.enums import TradingMode
from common.events import EventBus
from common.logging import configure_logging, resolve_log_level
from common.universe_bootstrap import resolve_binance_auto_universe
from engine.core.engine import ALL_STRATEGIES_MODE, Engine
from engine.persistence.market_capture import create_capturer
from engine.persistence.run_bootstrap import bootstrap_run, shutdown_bootstrap
from engine.strategies.blended_signals import BlendedSignalsStrategy
from engine.strategies.market_making import MarketMakingStrategy
from engine.strategies.market_making_v2 import MarketMakingV2Strategy
from engine.strategies.pairs_trading import PairsTradingStrategy
from engine.strategies.sma_crossover import SmaCrossoverStrategy
from gateways.factory import create_gateway

logger = logging.getLogger(__name__)


def _log_mode_banner(settings) -> None:
    """Print a hard-to-miss banner so LIVE mode is always confirmed in the log."""
    if settings.trading_mode is TradingMode.LIVE:
        logger.warning("=" * 64)
        logger.warning(
            "TRADING_MODE=LIVE  venue=%s  — REAL MONEY.",
            settings.venue,
        )
        logger.warning("=" * 64)
    else:
        logger.info(
            "TRADING_MODE=paper  venue=%s  (testnet/demo).",
            settings.venue,
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Algo trading backend")
    parser.add_argument(
        "--no-engine",
        action="store_true",
        help="Boot the API but leave the engine stopped (manual /control/start)",
    )
    parser.add_argument(
        "--engine",
        action="store_true",
        help="Start the engine automatically on boot (overrides ENGINE_AUTOSTART)",
    )
    return parser.parse_args()


async def _run() -> None:
    args = _parse_args()
    settings = get_settings()
    bus = EventBus()

    settings = await resolve_binance_auto_universe(settings)

    backend_root = Path(__file__).resolve().parent
    bootstrap = await bootstrap_run(settings, bus, backend_root)

    configure_logging(
        bus=bus,
        level=resolve_log_level(settings.log_level),
        log_file=bootstrap.log_file,
        log_file_max_bytes=settings.log_file_max_bytes,
        log_file_backup_count=settings.log_file_backup_count,
    )
    logger.info("config loaded from %s", settings.env_path)
    logger.info("log level=%s (LOG_LEVEL)", settings.log_level.lower())
    _log_mode_banner(settings)
    if bootstrap.run_dir is not None:
        logger.info("run archive: %s", bootstrap.run_dir)

    gateway = create_gateway(settings)
    # Build every strategy up front so the dashboard can hot-swap between
    # them at runtime without a restart. ``settings.strategy`` is just the
    # boot default; the operator picks the active one via the toggle in
    # the Control panel.
    strategies = [
        PairsTradingStrategy(settings),
        SmaCrossoverStrategy(settings),
        BlendedSignalsStrategy(settings),
        MarketMakingStrategy(settings),
        MarketMakingV2Strategy(settings),
    ]
    known_names = {s.name for s in strategies} | {ALL_STRATEGIES_MODE}
    # Operators usually write the short alias ("pairs" / "sma" / "all") in .env;
    # canonicalise it to the strategy's class-level ``name`` so the engine
    # / API agree on the lookup key.
    raw_default = (settings.strategy or "pairs").strip().lower()
    boot_default = normalize_strategy_name(raw_default)
    if boot_default not in known_names:
        logger.warning(
            "settings.strategy=%r not in %s; falling back to %s",
            settings.strategy, sorted(known_names), strategies[0].name,
        )
        boot_default = strategies[0].name
    settings = settings.model_copy(update={"strategy": boot_default})

    engine = Engine(
        settings=settings,
        bus=bus,
        gateway=gateway,
        strategies=strategies,
        recovery_wal=bootstrap.recovery_wal,
        event_archive_dir=bootstrap.run_dir,
    )
    if bootstrap.run_dir is not None:
        capturer = create_capturer(
            settings,
            bootstrap.run_dir,
            engine._symbols,  # noqa: SLF001
            snapshot_fn=lambda sym: engine._features.snapshot(sym),  # noqa: SLF001
        )
        if capturer is not None:
            engine.attach_market_capturer(capturer)
            bootstrap.market_capturer = capturer

    # Wire live equity + liquidity weights into strategies so they can
    # stop-loss-budget each entry and (for pairs) anchor their consensus
    # reference to where capital is actually flowing. Done after engine
    # construction because the Portfolio + volume cache live inside the
    # Engine.
    def _position_qty_for(strat_name: str):
        def provider(symbol: str) -> float:
            if engine.is_multi_strategy_mode():  # noqa: SLF001
                return engine.strategy_ledger.qty(strat_name, symbol)  # noqa: SLF001
            pos = engine._positions.get(symbol)  # noqa: SLF001
            return pos.qty if pos is not None else 0.0

        return provider

    for strat in strategies:
        if isinstance(strat, PairsTradingStrategy):
            strat.attach_equity_provider(lambda: engine.portfolio.snapshot().equity)
            strat.attach_weight_provider(lambda: engine.volume_weights)
        if isinstance(strat, (SmaCrossoverStrategy, BlendedSignalsStrategy)):
            strat.attach_equity_provider(lambda: engine.portfolio.snapshot().equity)
            strat.attach_position_provider(_position_qty_for(strat.name))
        if isinstance(strat, (MarketMakingStrategy, MarketMakingV2Strategy)):
            strat.attach_equity_provider(lambda: engine.portfolio.snapshot().equity)
            strat.attach_position_provider(_position_qty_for(strat.name))

    autostart = bool(settings.engine_autostart)
    if args.engine:
        autostart = True
    if args.no_engine:
        autostart = False

    if autostart:
        try:
            await engine.start()
        except Exception:
            logger.exception("engine failed to start; exiting")
            await shutdown_bootstrap(bootstrap, bus=bus)
            return

    stop_event = asyncio.Event()

    def _request_shutdown() -> None:
        logger.info("shutdown signal received")
        stop_event.set()

    jobs_dir = resolve_jobs_dir(settings.analytics_jobs_dir)
    worker_supervisor = AnalyticsWorkerSupervisor()
    worker_supervisor.start(settings, jobs_dir=jobs_dir)

    app = create_app(engine, bus, settings, request_shutdown=_request_shutdown)
    app.state.analytics_jobs_dir = jobs_dir
    app.state.analytics_worker_supervisor = worker_supervisor

    config = uvicorn.Config(
        app=app,
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
        # Disable uvicorn's signal handlers; we install our own to ensure
        # the engine stops cleanly before the server tears down.
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # type: ignore[assignment]

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

    async def _serve() -> None:
        try:
            await server.serve()
        except SystemExit as exc:
            # uvicorn calls sys.exit(1) on bind failures; treat it as a normal
            # shutdown path so we can stop the engine cleanly.
            logger.error("uvicorn exited (%s)", exc)
            stop_event.set()

    server_task = asyncio.create_task(_serve(), name="uvicorn")
    try:
        done, _pending = await asyncio.wait(
            [server_task, asyncio.create_task(stop_event.wait(), name="shutdown-watch")],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in done:
            with suppress(asyncio.CancelledError):
                await task
    except KeyboardInterrupt:
        logger.info("shutdown: keyboard interrupt")
    finally:
        server.should_exit = True
        with suppress(asyncio.CancelledError, Exception):
            await server_task
        await engine.stop()
        worker_supervisor.stop()
        await shutdown_bootstrap(bootstrap, bus=bus)


def main() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("shutdown: keyboard interrupt (outer)")


if __name__ == "__main__":
    main()
