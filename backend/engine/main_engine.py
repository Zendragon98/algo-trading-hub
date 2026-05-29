"""Engine-only CLI.

Useful for headless smoke tests and debugging the trading loop without
booting the FastAPI surface. Run:

    python -m engine.main_engine
"""

from __future__ import annotations

import asyncio
import logging
import signal as os_signal
from pathlib import Path

from common.config import get_settings, normalize_strategy_name
from common.enums import TradingMode
from common.events import EventBus
from common.logging import configure_logging, resolve_log_level
from common.universe_bootstrap import resolve_binance_auto_universe
from gateways.factory import create_gateway

from .core.engine import ALL_STRATEGIES_MODE, Engine
from .persistence.market_capture import create_capturer
from .persistence.run_bootstrap import bootstrap_run, shutdown_bootstrap
from .strategies.blended_signals import BlendedSignalsStrategy
from .strategies.flow_momentum import FlowMomentumStrategy
from .strategies.market_making.strategy import MarketMakingV2Strategy
from .strategies.pairs_trading import PairsTradingStrategy
from .strategies.sma_crossover import SmaCrossoverStrategy

logger = logging.getLogger(__name__)


async def run() -> None:
    settings = await resolve_binance_auto_universe(get_settings())
    bus = EventBus()

    backend_root = Path(__file__).resolve().parent.parent
    bootstrap = await bootstrap_run(settings, bus, backend_root)

    configure_logging(
        bus=bus,
        level=resolve_log_level(settings.log_level),
        log_file=bootstrap.log_file,
        log_file_max_bytes=settings.log_file_max_bytes,
        log_file_backup_count=settings.log_file_backup_count,
    )
    if settings.trading_mode is TradingMode.LIVE:
        logger.warning(
            "TRADING_MODE=LIVE venue=%s — REAL MONEY.",
            settings.venue,
        )
    else:
        logger.info(
            "TRADING_MODE=paper venue=%s (testnet/demo).",
            settings.venue,
        )
    if bootstrap.run_dir is not None:
        logger.info("run archive: %s", bootstrap.run_dir)

    gateway = create_gateway(settings)
    strategies = [
        PairsTradingStrategy(settings),
        SmaCrossoverStrategy(settings),
        BlendedSignalsStrategy(settings),
        FlowMomentumStrategy(settings),
        MarketMakingV2Strategy(settings),
    ]
    known_names = {s.name for s in strategies} | {ALL_STRATEGIES_MODE}
    boot_default = normalize_strategy_name(settings.strategy or "pairs")
    if boot_default not in known_names:
        logger.warning(
            "settings.strategy=%r not in %s; falling back to %s",
            settings.strategy,
            sorted(known_names),
            strategies[0].name,
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

    def _position_qty_for(strat_name: str):
        def provider(symbol: str) -> float:
            if engine.is_multi_strategy_mode():  # noqa: SLF001
                return engine.strategy_ledger.qty(strat_name, symbol)  # noqa: SLF001
            pos = engine._positions.get(symbol)  # noqa: SLF001
            return pos.qty if pos is not None else 0.0

        return provider

    mm2_strat = next((s for s in strategies if isinstance(s, MarketMakingV2Strategy)), None)

    def _mm2_symbols_for_blend() -> frozenset[str]:
        if not engine.is_multi_strategy_mode():  # noqa: SLF001
            return frozenset()
        if mm2_strat is None:
            return frozenset()
        return frozenset(mm2_strat.symbols())

    for strat in strategies:
        if isinstance(strat, PairsTradingStrategy):
            strat.attach_equity_provider(lambda: engine.portfolio.snapshot().equity)
            strat.attach_weight_provider(lambda: engine.volume_weights)
        if isinstance(
            strat, (SmaCrossoverStrategy, BlendedSignalsStrategy, FlowMomentumStrategy)
        ):
            strat.attach_equity_provider(lambda: engine.portfolio.snapshot().equity)
            strat.attach_position_provider(_position_qty_for(strat.name))
        if isinstance(strat, BlendedSignalsStrategy):
            strat.attach_mm2_active_symbols_provider(_mm2_symbols_for_blend)
        if isinstance(strat, MarketMakingV2Strategy):
            strat.attach_equity_provider(lambda: engine.portfolio.snapshot().equity)
            strat.attach_position_provider(_position_qty_for(strat.name))

    await engine.start()

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
            # Windows: signal handlers only work for SIGINT in the proactor
            # loop. The KeyboardInterrupt fallback below covers Ctrl+C anyway.
            pass

    try:
        await stop_event.wait()
    except KeyboardInterrupt:
        logger.info("shutdown: keyboard interrupt")
    finally:
        await engine.stop()
        await shutdown_bootstrap(bootstrap, bus=bus)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
