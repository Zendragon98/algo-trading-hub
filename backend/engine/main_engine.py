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

from common.config import get_settings
from common.enums import TradingMode
from common.events import EventBus
from common.logging import configure_logging
from gateways.factory import create_gateway

from .core.engine import Engine
from .persistence.event_recorder import EventRecorder, RecorderConfig, make_run_dir
from .strategies.market_making import MarketMakingStrategy
from .strategies.pairs_trading import PairsTradingStrategy
from .strategies.sma_crossover import SmaCrossoverStrategy

logger = logging.getLogger(__name__)


async def run() -> None:
    settings = get_settings()
    bus = EventBus()

    run_dir: Path | None = None
    if settings.persist_enabled or settings.log_file_enabled:
        base = Path(settings.persist_dir)
        if not base.is_absolute():
            base = Path(__file__).resolve().parent.parent / base
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
    strategy_name = (settings.strategy or "pairs").strip().lower()
    aliases = {
        "pairs": PairsTradingStrategy.name,
        "pairs_trading": PairsTradingStrategy.name,
        "sma": SmaCrossoverStrategy.name,
        "mm": MarketMakingStrategy.name,
        "market_making": MarketMakingStrategy.name,
    }
    boot = aliases.get(strategy_name, strategy_name)
    all_strategies = [
        PairsTradingStrategy(settings),
        SmaCrossoverStrategy(settings),
        MarketMakingStrategy(settings),
    ]
    by_name = {s.name: s for s in all_strategies}
    if boot in by_name:
        strategies = [by_name[boot]]
    else:
        strategies = [all_strategies[0]]
        boot = strategies[0].name
    settings = settings.model_copy(update={"strategy": boot})
    engine = Engine(settings=settings, bus=bus, gateway=gateway, strategies=strategies)

    def _position_qty(symbol: str) -> float:
        pos = engine._positions.get(symbol)  # noqa: SLF001
        return pos.qty if pos is not None else 0.0

    for strat in strategies:
        if isinstance(strat, PairsTradingStrategy):
            strat.attach_equity_provider(lambda: engine.portfolio.snapshot().equity)
            strat.attach_weight_provider(lambda: engine.volume_weights)
        if isinstance(strat, SmaCrossoverStrategy):
            strat.attach_equity_provider(lambda: engine.portfolio.snapshot().equity)
            strat.attach_position_provider(_position_qty)
        if isinstance(strat, MarketMakingStrategy):
            strat.attach_equity_provider(lambda: engine.portfolio.snapshot().equity)
            strat.attach_position_provider(_position_qty)

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
        pass
    finally:
        await engine.stop()
        if recorder is not None:
            await recorder.stop()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
