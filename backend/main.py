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

from api.server import create_app
from common.config import get_settings, normalize_strategy_name
from common.enums import TradingMode
from common.events import EventBus
from common.logging import configure_logging
from engine.core.engine import ALL_STRATEGIES_MODE, Engine
from engine.persistence.run_bootstrap import bootstrap_run, shutdown_bootstrap
from engine.strategies.market_making import MarketMakingStrategy
from engine.strategies.pairs_trading import PairsTradingStrategy
from engine.strategies.sma_crossover import SmaCrossoverStrategy
from gateways.binance.rest_client import BinanceRestClient
from gateways.binance.universe import discover_usdt_perps, discover_usdt_usdc_pairs
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

    # Auto-universe (Binance): discover tradable USDT/USDC perp legs at startup.
    # This keeps the .env minimal while ensuring the strategy always runs on
    # the available testnet listings. We also resolve SMA_SYMBOLS=AUTO into
    # the full set of USDT perpetuals so the multi-symbol SMA scanner has a
    # broad universe to spot crossovers in.
    if settings.venue == "binance":
        requested = [s.strip().upper() for s in settings.symbols]
        symbols_auto = (not requested) or (len(requested) == 1 and requested[0] == "AUTO")
        sma_requested = [s.strip().upper() for s in settings.sma_symbols] if settings.sma_symbols else []
        sma_auto = (
            not sma_requested
            or (len(sma_requested) == 1 and sma_requested[0] == "AUTO")
        )
        if symbols_auto or sma_auto:
            rest = BinanceRestClient(
                base_url=settings.binance_rest_base,
                api_key=settings.binance_api_key,
                api_secret=settings.binance_api_secret,
            )
            try:
                info = await rest.exchange_info()
                updates: dict[str, list[str]] = {}
                if symbols_auto:
                    discovered = discover_usdt_usdc_pairs(info)
                    updates["symbols"] = discovered
                    bases = sorted({s.replace("USDT", "").replace("USDC", "") for s in discovered})
                    logger.info(
                        "SYMBOLS=AUTO -> %d symbols across %d bases: %s",
                        len(discovered), len(bases),
                        ", ".join(bases) if len(bases) <= 20 else f"{', '.join(bases[:20])}, ...",
                    )
                if sma_auto:
                    sma_universe = discover_usdt_perps(info)
                    cap = int(settings.sma_max_symbols)
                    if cap > 0 and len(sma_universe) > cap:
                        vols = await rest.fetch_24h_volumes(sma_universe)
                        sma_universe = sorted(
                            sma_universe,
                            key=lambda s: vols.get(s, 0.0),
                            reverse=True,
                        )[:cap]
                        logger.info(
                            "SMA_SYMBOLS capped to top %d by 24h volume", cap,
                        )
                    updates["sma_symbols"] = sma_universe
                    logger.info(
                        "SMA_SYMBOLS=AUTO -> %d USDT perpetuals", len(sma_universe),
                    )
                if updates:
                    settings = settings.model_copy(update=updates)
            finally:
                await rest.close()

    backend_root = Path(__file__).resolve().parent
    bootstrap = await bootstrap_run(settings, bus, backend_root)

    configure_logging(
        bus=bus,
        log_file=bootstrap.log_file,
        log_file_max_bytes=settings.log_file_max_bytes,
        log_file_backup_count=settings.log_file_backup_count,
    )
    logger.info("config loaded from %s", settings.env_path)
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
        MarketMakingStrategy(settings),
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
    )

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
        if isinstance(strat, SmaCrossoverStrategy):
            strat.attach_equity_provider(lambda: engine.portfolio.snapshot().equity)
            strat.attach_position_provider(_position_qty_for(strat.name))
        if isinstance(strat, MarketMakingStrategy):
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
            await shutdown_bootstrap(bootstrap)
            return

    stop_event = asyncio.Event()

    def _request_shutdown() -> None:
        logger.info("shutdown signal received")
        stop_event.set()

    app = create_app(engine, bus, settings, request_shutdown=_request_shutdown)

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
        pass
    finally:
        server.should_exit = True
        with suppress(asyncio.CancelledError, Exception):
            await server_task
        await engine.stop()
        await shutdown_bootstrap(bootstrap)


def main() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
