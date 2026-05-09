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
import time
from collections import defaultdict
from typing import Iterable

from common.config import Settings
from common.enums import EngineStatus, EventType, Side
from common.events import Event, EventBus
from common.types import Fill, Position, Signal, TapeTrade, Tick
from gateways.gateway_interface import DepthDiff, GatewayInterface, SymbolFilters

from ..execution.algo_wheel import AlgoWheel
from ..execution.execution_metrics import ExecutionTracker
from ..execution.execution_router import ExecutionRouter, ParentSubmissionRejected
from ..execution.quality_guard import ExecutionQualityGuard
from ..execution.slippage_guard import SlippageGuard
from ..execution.submit_guard import SubmitGuard
from ..execution.vwap_executor import VwapExecutor
from ..market_data.feature_store import FeatureStore
from ..market_data.orderbook import OrderBookStore
from ..market_data.trade_tape import TradeTape
from ..orders.order_manager import OrderManager
from ..performance.performance_tracker import PerformanceTracker
from ..portfolio.portfolio import Portfolio
from ..position.position_tracker import PositionTracker
from ..risk.circuit_breaker import (
    Breach,
    BreakerScope,
    BreakerSeverity,
    CircuitBreaker,
)
from ..risk.exposure_tracker import ExposureTracker
from ..risk.limits import Limits
from ..risk.loss_tracker import LossTracker
from ..risk.market_data_guard import MarketDataGuard
from ..risk.pnl_tracker import PnLTracker
from ..risk.risk_manager import ExitIntent, RiskManager
from ..risk.stop_loss import StopLossMonitor
from ..strategies.strategy_base import StrategyBase
from .clock import Clock
from .connection_monitor import ConnectionMonitor
from .reconciliation import Reconciler
from .state import EngineSnapshot, EngineState

logger = logging.getLogger(__name__)


def _venue_symbol_list(candidates: Iterable[str], fallback: Iterable[str]) -> list[str]:
    """Binance-style symbols only; drop placeholders like AUTO left in settings."""

    def _pick(items: Iterable[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for raw in items:
            s = (raw or "").strip().upper()
            if not s or s == "AUTO":
                continue
            if not s.isalnum():
                continue
            if s not in seen:
                seen.add(s)
                out.append(s)
        return sorted(out)

    got = _pick(candidates)
    if got:
        return got
    got = _pick(fallback)
    return got if got else ["BTCUSDT"]


class Engine:
    """The entire trading stack as one object."""

    def __init__(
        self,
        settings: Settings,
        bus: EventBus,
        gateway: GatewayInterface,
        strategies: list[StrategyBase],
    ) -> None:
        self._settings = settings
        self._bus = bus
        self._gateway = gateway
        self._strategies = strategies
        self._strategies_by_name: dict[str, StrategyBase] = {s.name: s for s in strategies}
        # Active strategy name used by ``_evaluate_strategies`` /``_on_fill``.
        # Defaults to ``settings.strategy`` when present in the registered
        # set, else the first strategy. The dashboard hot-swap calls
        # ``set_active_strategy`` to change this without re-creating the
        # engine.
        default_name = settings.strategy if settings.strategy in self._strategies_by_name else (
            strategies[0].name if strategies else ""
        )
        self._active_strategy_name: str = default_name
        self._state = EngineState()

        # All symbols any strategy cares about — that's what we subscribe to.
        wanted: set[str] = set()
        for strat in strategies:
            wanted.update(strat.symbols())
        # Fall back to settings.symbols if no strategy is configured (smoke test).
        raw_syms = sorted(wanted) if wanted else list(settings.symbols)
        self._symbols = _venue_symbol_list(raw_syms, list(settings.symbols))

        # Market data layer
        self._books = OrderBookStore(self._symbols)
        self._tape = TradeTape(window_sec=settings.trade_tape_window_sec)
        self._features = FeatureStore(self._books, self._tape, settings)
        self._latest_tick: dict[str, Tick] = {}

        # OMS + tracker stack
        self._oms = OrderManager(gateway=gateway, bus=bus)
        self._positions = PositionTracker(bus=bus)
        self._portfolio = Portfolio(
            bus=bus,
            position_tracker=self._positions,
            base_currency=settings.base_currency,
        )

        # Risk. Only the active strategy contributes ``externally_managed``
        # symbols; idle strategies hand their symbols back to the per-leg
        # fixed-% SL/TP bracket so a hot-swap doesn't leave coins
        # uncovered. Portfolio-level safeguards (drawdown kill-switch,
        # gross/per-trade caps) still apply via RiskManager regardless.
        self._stop_monitor = StopLossMonitor(
            limits=Limits.from_settings(settings),
            externally_managed=self._compute_externally_managed(),
        )
        self._pnl = PnLTracker(self._portfolio)
        self._breaker = CircuitBreaker(bus=bus)
        self._risk = RiskManager(
            settings=settings,
            portfolio=self._portfolio,
            pnl=self._pnl,
            stop_monitor=self._stop_monitor,
            breaker=self._breaker,
            market_data_guard=MarketDataGuard.from_settings(settings),
            exposure_tracker=ExposureTracker.from_settings(settings, self._portfolio),
        )

        # Execution. The tracker is built first so the executor can
        # close out parents when their run task ends — without that
        # callback, slice-rejected parents would leak into the OMS panel.
        self._wheel = AlgoWheel()
        self._exec_tracker = ExecutionTracker(bus=bus)
        self._executor = VwapExecutor(
            order_manager=self._oms,
            gateway=gateway,
            features=self._features,
            price_provider=self._top_of_book_for,
            settings=settings,
            on_parent_done=self._exec_tracker.close_parent,
        )
        self._router = ExecutionRouter(
            wheel=self._wheel,
            executor=self._executor,
            features=self._features,
            tracker=self._exec_tracker,
        )

        # Submission + slippage guards. Wired post-construction so the
        # OMS / router don't need a circular reference to ExecutionTracker.
        self._submit_guard = SubmitGuard.from_settings(
            settings=settings,
            breaker=self._breaker,
            open_parent_count=lambda: len(self._exec_tracker.open_reports()),
        )
        self._oms.attach_submit_guard(self._submit_guard)
        self._router.attach_submit_guard(self._submit_guard)
        self._slippage_guard = SlippageGuard(
            breaker=self._breaker,
            tracker=self._exec_tracker,
            cancel_parent=self._router.cancel,
            cooldown_sec=settings.breaker_minor_cooldown_sec,
        )

        self._performance = PerformanceTracker(self._portfolio)
        # Portfolio-level guards: daily loss + consecutive-loss streak.
        # Wired after `_performance` so the loss tracker can read trade history.
        self._loss_tracker = LossTracker.from_settings(
            settings=settings,
            portfolio=self._portfolio,
            performance=self._performance,
            breaker=self._breaker,
        )
        # Execution-quality circuit: trip on rolling-avg slippage blowout.
        self._exec_quality_guard = ExecutionQualityGuard.from_settings(
            settings=settings,
            breaker=self._breaker,
            tracker=self._exec_tracker,
        )
        # System-level: WS / user-data freshness + venue reconciliation.
        self._connection_monitor = ConnectionMonitor.from_settings(
            settings=settings, breaker=self._breaker,
        )
        self._reconciler = Reconciler.from_settings(
            settings=settings,
            gateway=gateway,
            positions=self._positions,
            portfolio=self._portfolio,
            breaker=self._breaker,
            skip_rest_poll=self._reconcile_should_skip_rest,
        )
        self._clock = Clock(interval_sec=1.0, tick=self._on_clock_tick)

        # Background refresh loops spawned on start(), cancelled on stop().
        # Kept as plain tasks rather than wrapping each in a Clock because
        # they're independent of the strategy heartbeat and have their own
        # cadences (30 s vs 30 min).
        self._balance_resync_task: asyncio.Task[None] | None = None
        self._volume_refresh_task: asyncio.Task[None] | None = None
        # Latest 24h notional volume per symbol; refreshed periodically and
        # consumed by strategies via Engine.volume_weights.
        self._volume_weights: dict[str, float] = {}
        # True while an auto-flatten is dispatched in response to a
        # MAJOR engine breach; prevents duplicate flattens stacking up.
        self._auto_flatten_in_progress: bool = False

    def _reconcile_should_skip_rest(self) -> bool:
        """True when periodic reconcile should rely on user-data WS, not REST."""
        if not self._settings.reconcile_skip_rest_when_user_data_fresh:
            return False
        ts = self._oms.last_user_data_ts
        if ts <= 0:
            return False
        return (time.time() - ts) < float(self._settings.reconcile_user_data_fresh_sec)

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

    @property
    def strategies(self) -> list[StrategyBase]:
        return list(self._strategies)

    @property
    def active_strategy_name(self) -> str:
        """Name of the strategy currently emitting signals (``""`` if none)."""
        return self._active_strategy_name

    def set_active_strategy(self, name: str) -> None:
        """Hot-swap the active strategy.

        ``name`` must be one of the strategies the engine was constructed
        with (``__init__`` registers each by ``strategy.name``). Raises
        ``ValueError`` for unknown names so the API layer can surface a
        400 to the dashboard. Updates the StopLossMonitor's
        externally-managed set so only the active strategy's coins skip
        the per-leg SL/TP bracket.
        """
        normalised = (name or "").strip()
        if normalised not in self._strategies_by_name:
            available = ", ".join(sorted(self._strategies_by_name)) or "<none>"
            raise ValueError(f"unknown strategy {name!r}; available: {available}")
        if normalised == self._active_strategy_name:
            return
        previous = self._active_strategy_name
        self._active_strategy_name = normalised
        self._stop_monitor.set_externally_managed(self._compute_externally_managed())
        logger.info("strategy hot-swap: %s -> %s", previous or "<none>", normalised)

    def _compute_externally_managed(self) -> set[str]:
        """Return the set of symbols whose risk the active strategy owns.

        Idle strategies always return an empty set so their coins fall
        back to the engine's per-leg SL/TP bracket the moment they are
        deactivated.
        """
        active = self._strategies_by_name.get(self._active_strategy_name)
        if active is None or not active.manages_own_risk():
            return set()
        return set(active.symbols())

    @property
    def gateway(self) -> GatewayInterface:
        return self._gateway

    @property
    def portfolio(self):
        """Live portfolio. Strategies pull equity from here for sizing."""
        return self._portfolio

    @property
    def volume_weights(self) -> dict[str, float]:
        """Latest 24h notional volume per symbol (defensive copy).

        Strategies that build a liquidity-weighted reference (pairs)
        consume this through ``attach_weight_provider``. Empty until the
        first refresh completes; consumers must fall back to equal
        weights when the cache is empty.
        """
        return dict(self._volume_weights)

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

        # Public market WebSocket first: ``bookTicker`` for mids, depth for L2,
        # ``!ticker@arr`` for rolling 24h volumes (avoids REST ``/ticker/24hr``).
        await self._gateway.subscribe_market_data(
            symbols=self._symbols,
            on_tick=self._on_tick,
            on_depth=self._on_depth,
            on_trade=self._on_trade,
            on_quote_volume_24h=self._on_quote_volume_24h,
        )

        # Prime mids from WS where possible; REST ``/depth`` only for stragglers.
        await self._prime_symbol_prices()

        # Seed cash + positions from REST once; live updates use user-data WS.
        balances = await self._gateway.fetch_balances()
        self._portfolio.seed_balances(balances)
        positions = await self._gateway.fetch_positions()
        self._positions.seed(positions)

        # Configure futures leverage per symbol *before* placing any order
        # so the stop-loss-budgeted notional fits the available margin.
        # Skipped silently on venues whose `set_leverage` is a no-op
        # (spot, IBKR, mock test gateways).
        if self._settings.leverage and self._settings.leverage > 1:
            await asyncio.gather(
                *(self._gateway.set_leverage(sym, self._settings.leverage) for sym in self._symbols),
                return_exceptions=True,
            )

        await self._gateway.subscribe_user_data(
            on_fill=self._on_fill,
            on_order_update=self._oms.on_order_update,
            on_account_update=self._on_account_update,
        )

        # Initial volume snapshot so liquidity-weighted strategies can size
        # their reference at the first tick rather than after the 30 min
        # refresh window. Best-effort; an empty cache falls back to equal
        # weights in the consuming strategy.
        await self._refresh_volume_weights()

        self._state.status = EngineStatus.RUNNING
        await self._publish_status()
        self._clock.start()
        # Spawn the periodic refresh loops only after the engine is RUNNING
        # so a connect failure can't leak orphan tasks.
        # Optional extra REST wallet pulls; 0 = rely on reconcile + user-data WS only
        # so we do not double-hit GET /fapi/v2/account with the reconciler.
        if int(getattr(self._settings, "balance_resync_sec", 0) or 0) > 0:
            self._balance_resync_task = asyncio.create_task(
                self._balance_resync_loop(), name="engine-balance-resync",
            )
        self._volume_refresh_task = asyncio.create_task(
            self._volume_refresh_loop(), name="engine-volume-refresh",
        )
        # Start the periodic venue reconciliation loop so OMS/Portfolio
        # drift from a missed user-data event is caught within one cycle.
        self._reconciler.start()
        logger.info("engine running")

    async def _prime_symbol_prices(self) -> None:
        """Seed mids from ``bookTicker`` WS when possible; REST ``/depth`` for stragglers."""
        if not self._symbols:
            return

        timeout = max(0.5, float(getattr(self._settings, "prime_ws_timeout_sec", 10.0)))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        pending: set[str] = set(self._symbols)
        while pending and loop.time() < deadline:
            await asyncio.sleep(0.025)
            for sym in list(pending):
                if sym in self._latest_tick:
                    pending.discard(sym)

        ws_primed = len(self._symbols) - len(pending)
        if not pending:
            logger.info(
                "price priming: %d/%d symbols from bookTicker WS (no REST)",
                ws_primed,
                len(self._symbols),
            )
            return

        sem = asyncio.Semaphore(8)

        async def _one(symbol: str) -> None:
            async with sem:
                data = await self._gateway.book_snapshot(symbol, depth=5)
            self._books.get(symbol).apply_snapshot(
                bids=[(float(p), float(q)) for p, q in data.get("bids", [])],
                asks=[(float(p), float(q)) for p, q in data.get("asks", [])],
                last_update_id=int(data.get("lastUpdateId", 0)),
            )
            book = self._books.get(symbol)
            mid = book.mid()
            if mid is not None:
                self._latest_tick[symbol] = Tick(
                    symbol=symbol,
                    bid=book.best_bid() or mid,
                    ask=book.best_ask() or mid,
                    ts=time.time(),
                )

        results = await asyncio.gather(*(_one(sym) for sym in pending), return_exceptions=True)
        failures = sum(1 for r in results if isinstance(r, Exception))
        rest_ok = len(pending) - failures
        logger.info(
            "price priming: ws_ticks=%d rest_depth=%d/%d rest_failed=%d",
            ws_primed,
            rest_ok,
            len(pending),
            failures,
        )

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
        # Optionally market-out residual positions before tearing down
        # connections, so a stop never leaves naked exposure on the
        # venue. Skipped on PAPER smoke tests by setting FLATTEN_ON_STOP=false.
        if getattr(self._settings, "flatten_on_stop", True):
            try:
                await self._flatten_and_wait_for_flat()
            except Exception:  # noqa: BLE001
                logger.exception("flatten_on_stop failed")
        await self._clock.stop()
        await self._reconciler.stop()
        await self._cancel_refresh_loops()
        await self._router.shutdown()
        await self._oms.cancel_all()
        await self._gateway.disconnect()
        self._state.status = EngineStatus.STOPPED
        await self._publish_status()

    async def _flatten_and_wait_for_flat(self) -> None:
        """Submit reduce-only flatten orders and wait until flat or timeout."""
        await self.flatten()
        timeout = float(getattr(self._settings, "flatten_timeout_sec", 30.0))
        deadline = asyncio.get_event_loop().time() + max(0.0, timeout)
        poll = 0.5
        while asyncio.get_event_loop().time() < deadline:
            if not self._positions.all():
                return
            await asyncio.sleep(poll)
        remaining = [p.symbol for p in self._positions.all()]
        if remaining:
            logger.warning(
                "flatten timeout: %d positions still open: %s",
                len(remaining), ",".join(remaining),
            )

    async def _cancel_refresh_loops(self) -> None:
        """Cancel + await the background resync tasks spawned in start().

        Safe to call when the tasks were never created (engine was
        stopped before reaching RUNNING); each ``None`` slot is just
        skipped.
        """
        for slot_name in ("_balance_resync_task", "_volume_refresh_task"):
            task: asyncio.Task[None] | None = getattr(self, slot_name)
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            setattr(self, slot_name, None)

    async def _balance_resync_loop(self) -> None:
        """Periodically pull wallet balances from REST as a safety net.

        ``ACCOUNT_UPDATE`` events drive the live cash view, but a missed
        or dropped event would leave the local wallet drifting from the
        venue indefinitely. Enable only when ``balance_resync_sec`` > 0;
        otherwise reconciliation + WS carry balances (see Settings).
        """
        interval = max(5.0, float(getattr(self._settings, "balance_resync_sec", 30.0)))
        while True:
            try:
                await asyncio.sleep(interval)
                balances = await self._gateway.fetch_balances()
                if balances:
                    self._portfolio.update_balances(balances)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("balance resync failed; retrying after interval")
                backoff = getattr(exc, "retry_after_sec", None)
                if backoff is not None:
                    await asyncio.sleep(min(float(backoff) + 1.0, 86_400.0))

    async def _volume_refresh_loop(self) -> None:
        """Periodically top up volume weights when WS cache is incomplete.

        With ``PAIR_VOLUME_FROM_WEBSOCKET=true``, REST ``/ticker/24hr`` runs
        only for symbols still missing from ``!ticker@arr`` after each interval.
        """
        interval = max(60.0, float(getattr(self._settings, "pair_volume_refresh_sec", 1800.0)))
        while True:
            try:
                await asyncio.sleep(interval)
                await self._refresh_volume_weights()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("volume refresh failed; retrying after interval")

    async def _refresh_volume_weights(self) -> None:
        """Pull 24h notional volume for symbols not yet supplied by WS."""
        if not self._symbols:
            return
        use_ws = bool(getattr(self._settings, "pair_volume_from_websocket", True))
        if use_ws:
            fetch_symbols = [s for s in self._symbols if s not in self._volume_weights]
            if not fetch_symbols:
                return
        else:
            fetch_symbols = list(self._symbols)
        try:
            volumes = await self._gateway.fetch_24h_volumes(fetch_symbols)
        except Exception:  # noqa: BLE001
            logger.exception("fetch_24h_volumes failed; keeping previous cache")
            return
        if not volumes:
            return
        if use_ws:
            self._volume_weights.update(volumes)
        else:
            self._volume_weights = volumes
        # Emit a compact summary so the operator can verify the weighting
        # at a glance via the dashboard LIVE LOG panel.
        top = sorted(volumes.items(), key=lambda kv: kv[1], reverse=True)[:5]
        top_str = ", ".join(f"{sym}={vol:.2g}" for sym, vol in top)
        logger.info(
            "volume weights refreshed: %d symbols, top=[%s]",
            len(volumes), top_str,
        )

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
                reduce_only=True,
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

    async def _on_quote_volume_24h(self, symbol: str, quote_vol: float) -> None:
        """Rolling 24h quote-asset volume from public WS (``!ticker@arr``)."""
        sym = symbol.upper()
        if sym not in self._symbols:
            return
        self._volume_weights[sym] = quote_vol

    async def _on_fill(self, fill: Fill) -> None:
        # Exchange-reported fill price only for PnL; arrival vs VWAP slippage is in ExecutionTracker.
        fill.venue_price = fill.price
        fill.impact_bps = 0.0

        # OMS fans out the FILL event onto the bus; we additionally
        #    update position / perf / execution-quality state synchronously
        #    so subsequent reads are coherent.
        await self._oms.on_fill(fill)
        await self._positions.on_fill(fill)
        position = self._positions.get(fill.symbol) or Position(symbol=fill.symbol)
        self._risk.on_fill(fill, position)
        # Prefer exchange-reported realized PnL per fill when available (Binance `rp`).
        self._performance.record_fill(fill, realized_pnl=fill.realized_pnl)
        if fill.parent_id:
            await self._exec_tracker.on_fill(
                parent_id=fill.parent_id,
                side=fill.side,
                qty=fill.qty,
                venue_price=fill.venue_price,
                impact_bps=fill.impact_bps,
            )
            # In-flight slippage abort: cancel + breach when the parent's
            # realised VWAP moves past `max_slippage_bps` from arrival.
            parent = self._oms.parent(fill.parent_id)
            if parent is not None:
                await self._slippage_guard.on_fill(
                    fill.parent_id, parent.max_slippage_bps,
                )
        active = self._strategies_by_name.get(self._active_strategy_name)
        if active is not None:
            try:
                active.on_fill(fill.symbol, fill.qty, fill.side.value)
            except Exception:  # noqa: BLE001
                logger.exception("strategy %s on_fill raised", active.name)

    async def _on_account_update(self, update: dict) -> None:
        """Apply exchange-reported wallet + position state.

        ``ACCOUNT_UPDATE`` only carries the *changed* assets in its ``B``
        array, so we merge per-asset into the portfolio rather than
        overwriting cash. Without this merge a USDC-only fill drops the
        USDT wallet to ``0`` because the message never mentions it. The
        portfolio's ``cash`` view continues to apply the USDT+USDC
        stablecoin combine rule.
        """
        self._oms.touch_user_data_activity()
        wallet_by_asset = update.get("wallet_by_asset") or {}
        for asset, balance in wallet_by_asset.items():
            try:
                self._portfolio.update_asset_balance(str(asset), float(balance))
            except (TypeError, ValueError):
                continue

        positions = update.get("positions") or []
        await self._positions.apply_exchange_positions(positions)

    # --- Heartbeat ---

    async def _on_clock_tick(self) -> None:
        await self._portfolio.mark_to_market()
        # Refresh portfolio guards before the breaker advances so a
        # newly tripped MAJOR is honoured this same tick.
        self._pnl.update()
        self._loss_tracker.update()
        self._exec_quality_guard.evaluate()
        self._connection_monitor.evaluate(
            now=time.time(),
            last_tick_ts=self._state.last_tick_ts,
            last_user_data_ts=self._oms.last_user_data_ts,
            engine_running=self._state.status is EngineStatus.RUNNING,
        )
        # Advance the circuit-breaker so cooled-down minor breaches return
        # to ARMED. Runs even when paused so the engine can auto-resume
        # on the next operator action without a stale block.
        self._breaker.tick()
        # Auto-flatten on a fresh ENGINE-scope MAJOR breach. Idempotent
        # via `_flatten_in_progress`: subsequent ticks during an active
        # flatten don't spawn duplicates.
        await self._maybe_flatten_for_breaker()
        if self._state.status is not EngineStatus.RUNNING:
            return

        # Risk-driven exits first; an exit can't be vetoed by risk again
        # because it's already a closing trade.
        await self._evaluate_exits()
        await self._evaluate_strategies()

    async def _maybe_flatten_for_breaker(self) -> None:
        if not self._breaker.is_blocked(BreakerScope.ENGINE):
            self._auto_flatten_in_progress = False
            return
        if self._auto_flatten_in_progress:
            return
        # Only flatten on MAJOR engine-scope trips; minor cooldowns just
        # pause new orders.
        active = [s for s in self._breaker.active() if s.scope is BreakerScope.ENGINE]
        if not any(s.severity is BreakerSeverity.MAJOR for s in active):
            return
        self._auto_flatten_in_progress = True
        codes = ",".join(s.code for s in active)
        logger.error("auto-flatten triggered by engine breaker(s): %s", codes)
        try:
            await self.flatten()
        except Exception:  # noqa: BLE001
            logger.exception("auto-flatten failed")

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
            # Risk exits always reduce an existing position. Mark them
            # reduce-only so Binance waives MIN_NOTIONAL on tiny positions
            # (otherwise a sub-$50 SL/TP cannot close out at all).
            reduce_only=True,
        )

    async def _evaluate_strategies(self) -> None:
        active = self._strategies_by_name.get(self._active_strategy_name)
        if active is None:
            return
        try:
            feats = {sym: self._features.snapshot(sym) for sym in active.symbols()}
            signals = list(active.on_tick(feats))
        except Exception:  # noqa: BLE001
            logger.exception("strategy %s on_tick raised", active.name)
            return
        await self._dispatch_signals(signals)

    async def _dispatch_signals(self, signals: Iterable[Signal]) -> None:
        # Bucket pair-legs that must trade together; submit non-grouped
        # signals (single-leg strategies, risk-driven exits) one by one.
        groups: dict[str, list[Signal]] = defaultdict(list)
        loose: list[Signal] = []
        for sig in signals:
            if sig.group_id:
                groups[sig.group_id].append(sig)
            else:
                loose.append(sig)

        for sig in loose:
            await self._dispatch_single(sig)
        for gid, legs in groups.items():
            await self._dispatch_group(gid, legs)

    async def _dispatch_single(self, signal: Signal) -> None:
        """Risk-check + venue-floor + submit one ungrouped signal."""
        mid = self._mid_for(signal.symbol)
        if mid is None:
            return
        tick = self._latest_tick.get(signal.symbol)
        feat = self._features.snapshot(signal.symbol)
        decision = self._risk.check(
            signal,
            mid,
            tick_ts=tick.ts if tick is not None else None,
            spread_bps=feat.spread_bps,
        )
        if not decision.approved:
            logger.info("risk vetoed %s: %s", signal.symbol, decision.reason)
            return

        filters = self._gateway.get_symbol_filters(signal.symbol)
        venue_min_qty = _venue_min_qty(symbol=signal.symbol, mid=mid, filters=filters)
        if venue_min_qty is None:
            logger.info("venue vetoed %s: mid=%.6f filters=%s", signal.symbol, mid, filters)
            return

        final_qty = max(decision.qty, venue_min_qty)
        if final_qty > decision.qty + 1e-12:
            snap = self._portfolio.snapshot()
            max_notional_per_trade = snap.equity * self._risk.limits.max_risk_pct
            required_notional = final_qty * mid
            projected_gross = snap.gross_notional + required_notional
            if required_notional > max_notional_per_trade:
                logger.info(
                    "risk vetoed %s: venue_min_qty=%.10f forces notional=%.4f > max=%.4f",
                    signal.symbol, venue_min_qty, required_notional, max_notional_per_trade,
                )
                return
            if projected_gross > self._risk.limits.max_gross_notional:
                logger.info(
                    "risk vetoed %s: venue_min_qty=%.10f would breach max_gross_notional",
                    signal.symbol, venue_min_qty,
                )
                return

        try:
            await self._router.submit(
                symbol=signal.symbol,
                side=signal.side,
                qty=final_qty,
                notes=signal.reason,
            )
        except ParentSubmissionRejected as exc:
            logger.info("router gated %s: %s", signal.symbol, exc)

    async def _dispatch_group(self, group_id: str, legs: list[Signal]) -> None:
        """Submit a pair of legs atomically.

        Pair semantics: every leg in the group must trade with the *same*
        base qty (a single coin's USDT/USDC perps quote in the same base
        unit). The group qty is bumped up to satisfy whichever leg has
        the strictest venue floor; if any leg then breaches a risk
        ceiling, we abort the whole group rather than leave a naked leg.
        """
        # Engine-scope breaker blocks every entry; symbol-scope blocks the leg.
        if self._breaker.is_blocked(BreakerScope.ENGINE):
            logger.info("group %s aborted: engine breaker active", group_id)
            return
        for leg in legs:
            if self._breaker.is_blocked(BreakerScope.SYMBOL, leg.symbol):
                logger.info(
                    "group %s aborted: symbol breaker active for %s",
                    group_id, leg.symbol,
                )
                return

        # Collect mid + venue floor + freshness/spread for every leg.
        mids: dict[str, float] = {}
        floors: dict[str, float] = {}
        for leg in legs:
            mid = self._mid_for(leg.symbol)
            if mid is None or mid <= 0:
                logger.info("group %s aborted: no mid for %s", group_id, leg.symbol)
                return
            tick = self._latest_tick.get(leg.symbol)
            feat = self._features.snapshot(leg.symbol)
            md_breach = self._risk.evaluate_market_data(
                symbol=leg.symbol,
                tick_ts=tick.ts if tick is not None else None,
                spread_bps=feat.spread_bps,
            )
            if md_breach is not None:
                self._breaker.trip(md_breach)
                logger.info(
                    "group %s aborted: %s on %s (%s)",
                    group_id, md_breach.code, leg.symbol, md_breach.detail,
                )
                return
            filters = self._gateway.get_symbol_filters(leg.symbol)
            min_qty = _venue_min_qty(symbol=leg.symbol, mid=mid, filters=filters)
            if min_qty is None:
                logger.info(
                    "group %s aborted: venue vetoed %s (mid=%.6f filters=%s)",
                    group_id, leg.symbol, mid, filters,
                )
                return
            mids[leg.symbol] = mid
            floors[leg.symbol] = min_qty

        # Strategy already sized each leg with stop-loss-budgeted notional;
        # they should agree. Use the max so any per-leg rounding-up survives
        # and bump further if a venue floor demands more base qty.
        strategy_qty = max((leg.qty for leg in legs if leg.qty > 0), default=0.0)
        pair_qty = max(strategy_qty, max(floors.values()))
        if pair_qty <= 0:
            logger.info("group %s aborted: strategy + venue floors both zero", group_id)
            return

        # Risk gate: every leg's notional must clear the per-trade and
        # gross-notional ceilings at the agreed pair qty.
        snap = self._portfolio.snapshot()
        equity = snap.equity
        if equity <= 0:
            logger.info("group %s aborted: non-positive equity", group_id)
            return
        per_leg_cap = equity * self._risk.limits.max_risk_pct
        projected_gross = snap.gross_notional
        for leg in legs:
            leg_notional = pair_qty * mids[leg.symbol]
            if leg_notional > per_leg_cap:
                logger.info(
                    "group %s aborted: leg %s notional=%.2f > per-trade cap=%.2f",
                    group_id, leg.symbol, leg_notional, per_leg_cap,
                )
                return
            projected_gross += leg_notional
        if projected_gross > self._risk.limits.max_gross_notional:
            logger.info(
                "group %s aborted: projected gross=%.2f > max_gross_notional=%.2f",
                group_id, projected_gross, self._risk.limits.max_gross_notional,
            )
            return

        # All-or-none submission. We submit sequentially (fast, single-loop
        # latency) so the OMS sees both parents in one tick.
        logger.info(
            "group %s submitting %d legs at pair_qty=%.8f (per-leg notional≈%.2f, equity=%.2f)",
            group_id, len(legs), pair_qty, pair_qty * mids[legs[0].symbol], equity,
        )
        for leg in legs:
            try:
                await self._router.submit(
                    symbol=leg.symbol,
                    side=leg.side,
                    qty=pair_qty,
                    notes=leg.reason,
                )
            except ParentSubmissionRejected as exc:
                logger.warning(
                    "group %s leg %s gated: %s (other legs may now be naked)",
                    group_id, leg.symbol, exc,
                )
                return

    # --- Helpers ---

    async def _snapshot_book(self, symbol: str) -> None:
        try:
            data = await self._gateway.book_snapshot(symbol, depth=100)
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


def _venue_min_qty(
    *,
    symbol: str,
    mid: float,
    filters: SymbolFilters | None,
) -> float | None:
    """Return the venue-minimum tradable qty, or None to veto.

    We do not have access to the final limit price here, so we use `mid`
    as a conservative proxy for MIN_NOTIONAL checks.
    """
    if mid <= 0:
        return None
    if filters is None:
        # Unknown venue constraints (or a permissive/mock gateway). Treat as "no floor".
        return 0.0

    required = 0.0

    if filters.min_qty is not None and required + 1e-12 < filters.min_qty:
        required = filters.min_qty

    if filters.min_notional is not None:
        min_qty_for_notional = filters.min_notional / mid
        if required + 1e-12 < min_qty_for_notional:
            required = min_qty_for_notional

    if filters.step_size is not None and filters.step_size > 0:
        # Ceil to step so we don't round down below venue minimums.
        step = filters.step_size
        n = int((required + step - 1e-15) / step)
        required = n * step

    # Last sanity: if rounding makes it zero or NaN-ish, veto.
    if required <= 0:
        return None
    return required
