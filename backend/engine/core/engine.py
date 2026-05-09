"""Top-level engine orchestrator.

Wires every subsystem together and owns the asyncio task that drives the
strategy loop. The engine is the only object the API layer holds a
reference to; everything else is reached via accessors.

Lifecycle:
    `start()`  - connect gateway, subscribe streams, seed positions,
                 mark engine RUNNING, start the heartbeat clock.
    `pause()`  - keep streams alive but stop emitting new orders.
                 Existing positions are still monitored.
    `resume()` - inverse of pause.
    `stop()`   - flatten + cancel everything, disconnect, mark STOPPED.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Iterable

from common.config import Settings
from common.enums import EngineStatus, EventType, Side
from common.events import Event, EventBus
from common.types import Fill, Position, Signal, TapeTrade, Tick
from gateways.binance.binance_gateway import BinanceGateway
from gateways.gateway_interface import DepthDiff

from ..execution.algo_wheel import AlgoWheel
from ..execution.execution_metrics import ExecutionTracker
from ..execution.execution_router import ExecutionRouter
from ..execution.impact_model import ImpactConfig, ImpactModel
from ..execution.vwap_executor import VwapExecutor
from ..market_data.feature_store import FeatureStore
from ..market_data.orderbook import OrderBookStore
from ..market_data.trade_tape import TradeTape
from ..orders.order_manager import OrderManager
from ..performance.performance_tracker import PerformanceTracker
from ..portfolio.portfolio import Portfolio
from ..position.position_tracker import PositionTracker
from ..risk.limits import Limits
from ..risk.pnl_tracker import PnLTracker
from ..risk.risk_manager import ExitIntent, RiskManager
from ..risk.stop_loss import StopLossMonitor
from ..strategies.strategy_base import StrategyBase
from .clock import Clock
from .state import EngineSnapshot, EngineState

logger = logging.getLogger(__name__)


class Engine:
    """The entire trading stack as one object."""

    def __init__(
        self,
        settings: Settings,
        bus: EventBus,
        gateway: BinanceGateway,
        strategies: list[StrategyBase],
    ) -> None:
        self._settings = settings
        self._bus = bus
        self._gateway = gateway
        self._strategies = strategies
        self._state = EngineState()

        # All symbols any strategy cares about — that's what we subscribe to.
        wanted: set[str] = set()
        for strat in strategies:
            wanted.update(strat.symbols())
        # Fall back to settings.symbols if no strategy is configured (smoke test).
        self._symbols = sorted(wanted) if wanted else list(settings.symbols)

        # Market data layer
        self._books = OrderBookStore(self._symbols)
        self._tape = TradeTape(window_sec=settings.trade_tape_window_sec)
        self._features = FeatureStore(self._books, self._tape, settings)
        self._latest_tick: dict[str, Tick] = {}

        # OMS + tracker stack
        self._oms = OrderManager(gateway=gateway, bus=bus)
        self._positions = PositionTracker(bus=bus)
        self._portfolio = Portfolio(bus=bus, position_tracker=self._positions)

        # Risk
        self._stop_monitor = StopLossMonitor(limits=Limits.from_settings(settings))
        self._pnl = PnLTracker(self._portfolio)
        self._risk = RiskManager(
            settings=settings,
            portfolio=self._portfolio,
            pnl=self._pnl,
            stop_monitor=self._stop_monitor,
        )

        # Execution
        self._wheel = AlgoWheel()
        self._executor = VwapExecutor(
            order_manager=self._oms,
            features=self._features,
            price_provider=self._top_of_book_for,
            settings=settings,
        )
        self._impact = ImpactModel(ImpactConfig.from_settings(settings))
        self._exec_tracker = ExecutionTracker(bus=bus)
        self._router = ExecutionRouter(
            wheel=self._wheel,
            executor=self._executor,
            features=self._features,
            tracker=self._exec_tracker,
        )

        self._performance = PerformanceTracker(self._portfolio)
        self._clock = Clock(interval_sec=1.0, tick=self._on_clock_tick)

    # --- Public surface for the API ---

    @property
    def status(self) -> EngineStatus:
        return self._state.status

    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def risk(self) -> RiskManager:
        return self._risk

    @property
    def router(self) -> ExecutionRouter:
        return self._router

    @property
    def oms(self) -> OrderManager:
        return self._oms

    @property
    def execution_tracker(self) -> ExecutionTracker:
        return self._exec_tracker

    def snapshot(self) -> EngineSnapshot:
        return EngineSnapshot(
            state=self._state,
            position_tracker=self._positions,
            portfolio=self._portfolio,
            trades=self._performance.trades(),
            win_rate=self._performance.win_rate(),
        )

    # --- Lifecycle ---

    async def start(self) -> None:
        if self._state.status is EngineStatus.RUNNING:
            return
        logger.info("engine starting")
        await self._gateway.connect()

        # Seed cash + positions before subscribing so the first ticks have
        # somewhere to land in mark-to-market.
        cash = await self._gateway.fetch_balance()
        self._portfolio.seed_cash(cash)
        positions = await self._gateway.fetch_positions()
        self._positions.seed(positions)

        await self._gateway.subscribe_market_data(
            symbols=self._symbols,
            on_tick=self._on_tick,
            on_depth=self._on_depth,
            on_trade=self._on_trade,
        )
        await self._gateway.subscribe_user_data(
            on_fill=self._on_fill,
            on_order_update=self._oms.on_order_update,
        )

        self._state.status = EngineStatus.RUNNING
        await self._publish_status()
        self._clock.start()
        logger.info("engine running")

    async def pause(self) -> None:
        if self._state.status is not EngineStatus.RUNNING:
            return
        self._state.status = EngineStatus.PAUSED
        await self._publish_status()
        logger.warning("engine paused")

    async def resume(self) -> None:
        if self._state.status is not EngineStatus.PAUSED:
            return
        self._state.status = EngineStatus.RUNNING
        await self._publish_status()
        logger.info("engine resumed")

    async def stop(self) -> None:
        if self._state.status is EngineStatus.STOPPED:
            return
        logger.error("engine stopping (operator request)")
        await self._clock.stop()
        await self._router.shutdown()
        await self._oms.cancel_all()
        await self._gateway.disconnect()
        self._state.status = EngineStatus.STOPPED
        await self._publish_status()

    async def flatten(self) -> None:
        """Cancel working orders + market-out all positions."""
        logger.warning("flattening all positions")
        await self._oms.cancel_all()
        for position in list(self._positions.all()):
            if position.qty == 0:
                continue
            side = Side.SELL if position.qty > 0 else Side.BUY
            await self._router.submit(
                symbol=position.symbol,
                side=side,
                qty=abs(position.qty),
                notes="operator flatten",
            )

    # --- Market data callbacks ---

    async def _on_tick(self, tick: Tick) -> None:
        self._latest_tick[tick.symbol] = tick
        self._state.last_tick_ts = tick.ts
        await self._positions.on_tick(tick)
        await self._bus.publish(
            Event(
                type=EventType.TICK,
                payload={"symbol": tick.symbol, "bid": tick.bid, "ask": tick.ask, "mid": tick.mid},
            )
        )

    async def _on_depth(self, diff: DepthDiff) -> None:
        # Lazy snapshot: the first diff for an unseen symbol triggers a
        # REST snapshot fetch; subsequent diffs are folded straight in.
        book = self._books.get(diff.symbol)
        if not book.ready():
            await self._snapshot_book(diff.symbol)
        book.apply_diff(diff)

    async def _on_trade(self, trade: TapeTrade) -> None:
        self._tape.record(trade)

    async def _on_fill(self, fill: Fill) -> None:
        # 1. Apply synthetic impact: testnet fills are unrealistically
        #    clean, so we adjust the recorded price by what mainnet would
        #    cost. The raw venue price is preserved on the fill for audit.
        book = self._books.get(fill.symbol) if fill.symbol in self._books else None
        simulated, impact_bps = self._impact.apply(
            side=fill.side,
            qty=fill.qty,
            raw_price=fill.price,
            book=book,
        )
        fill.venue_price = fill.price
        fill.impact_bps = impact_bps
        fill.price = simulated

        # 2. OMS fans out the FILL event onto the bus; we additionally
        #    update position / perf / execution-quality state synchronously
        #    so subsequent reads are coherent.
        await self._oms.on_fill(fill)
        await self._positions.on_fill(fill)
        position = self._positions.get(fill.symbol) or Position(symbol=fill.symbol)
        self._risk.on_fill(fill, position)
        self._performance.record_fill(fill, realized_pnl=position.realized_pnl)
        if fill.parent_id:
            await self._exec_tracker.on_fill(
                parent_id=fill.parent_id,
                side=fill.side,
                qty=fill.qty,
                venue_price=fill.venue_price,
                impact_bps=impact_bps,
            )

    # --- Heartbeat ---

    async def _on_clock_tick(self) -> None:
        await self._portfolio.mark_to_market()
        if self._state.status is not EngineStatus.RUNNING:
            return

        # Risk-driven exits first; an exit can't be vetoed by risk again
        # because it's already a closing trade.
        await self._evaluate_exits()
        await self._evaluate_strategies()

    async def _evaluate_exits(self) -> None:
        positions = self._positions.all()
        for symbol, tick in list(self._latest_tick.items()):
            intent = self._risk.monitor_tick(tick, positions)
            if intent is None:
                continue
            await self._submit_exit(intent)

    async def _submit_exit(self, intent: ExitIntent) -> None:
        side = Side.BUY if intent.side == "buy" else Side.SELL
        await self._router.submit(
            symbol=intent.symbol,
            side=side,
            qty=intent.qty,
            notes=f"risk: {intent.reason}",
        )

    async def _evaluate_strategies(self) -> None:
        if not self._strategies:
            return
        for strat in self._strategies:
            try:
                feats = {sym: self._features.snapshot(sym) for sym in strat.symbols()}
                signals = list(strat.on_tick(feats))
            except Exception:  # noqa: BLE001
                logger.exception("strategy %s on_tick raised", strat.name)
                continue
            await self._dispatch_signals(signals)

    async def _dispatch_signals(self, signals: Iterable[Signal]) -> None:
        for signal in signals:
            mid = self._mid_for(signal.symbol)
            if mid is None:
                continue
            decision = self._risk.check(signal, mid)
            if not decision.approved:
                logger.info("risk vetoed %s: %s", signal.symbol, decision.reason)
                continue
            await self._router.submit(
                symbol=signal.symbol,
                side=signal.side,
                qty=decision.qty,
                notes=signal.reason,
            )

    # --- Helpers ---

    async def _snapshot_book(self, symbol: str) -> None:
        try:
            data = await self._gateway.rest.book_snapshot(symbol, limit=100)
        except Exception:  # noqa: BLE001
            logger.exception("book snapshot failed for %s", symbol)
            return
        self._books.get(symbol).apply_snapshot(
            bids=[(float(p), float(q)) for p, q in data.get("bids", [])],
            asks=[(float(p), float(q)) for p, q in data.get("asks", [])],
            last_update_id=int(data.get("lastUpdateId", 0)),
        )

    def _mid_for(self, symbol: str) -> float | None:
        tick = self._latest_tick.get(symbol)
        if tick is not None:
            return tick.mid
        book = self._books.get(symbol)
        return book.mid()

    def _top_of_book_for(self, symbol: str) -> float | None:
        return self._mid_for(symbol)

    async def _publish_status(self) -> None:
        await self._bus.publish(
            Event(
                type=EventType.STATUS,
                payload={"status": self._state.status.value, "uptime_sec": self.snapshot().uptime_sec},
            )
        )
