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
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

from common.config import Settings, normalize_strategy_name
from common.enums import EngineStatus, EventType, LogLevel, OrderStatus, OrderType, Side, Urgency
from common.events import Event, EventBus
from common.types import ChildOrder, Fill, ParentOrder, Position, Signal, TapeTrade, Tick
from gateways.gateway_interface import DepthDiff, GatewayInterface

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
from ..orders.order_manager import OrderManager, new_client_order_id, _fill_to_dict
from ..performance.fill_classification import FillClassification, classify_fill
from ..performance.performance_tracker import PerformanceTracker
from ..portfolio.portfolio import Portfolio
from ..position.position_tracker import PositionTracker
from ..position.strategy_ledger import StrategyPositionLedger
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
from ..market_data.data_quality import DataQualityMonitor, DiffAction
from ..observability.alert_manager import AlertManager
from ..observability.latency_tracker import LatencyTracker
from ..risk.pretrade_validator import PreTradeValidator
from ..risk.risk_manager import ExitIntent, RiskManager
from ..risk.stop_loss import StopLossMonitor
from ..risk.venue_sizing import venue_cap_qty, venue_min_qty
from ..strategies.signal_netter import NettedSignal, net_strategy_signals
from ..strategies.strategy_base import StrategyBase
from ..persistence.journal import replay_wal_async
from .clock import Clock
from .connection_monitor import ConnectionMonitor
from .order_reconciliation import OrderReconciler
from .reconciliation import Reconciler
from .state import EngineSnapshot, EngineState

logger = logging.getLogger(__name__)

# Cap REST book resnapshots per clock tick so mark-to-market stays ~1 Hz.
_MAX_MD_RESNAPSHOTS_PER_TICK = 5


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


# Hot-swap / boot mode: run every registered strategy with internal netting.
ALL_STRATEGIES_MODE = "all"


class Engine:
    """The entire trading stack as one object."""

    def __init__(
        self,
        settings: Settings,
        bus: EventBus,
        gateway: GatewayInterface,
        strategies: list[StrategyBase],
        *,
        recovery_wal: Path | None = None,
    ) -> None:
        self._settings = settings
        self._bus = bus
        self._gateway = gateway
        self._recovery_wal = recovery_wal
        self._strategies = strategies
        self._strategies_by_name: dict[str, StrategyBase] = {s.name: s for s in strategies}
        # Active strategy name used by ``_evaluate_strategies`` /``_on_fill``.
        # Defaults to ``settings.strategy`` when present in the registered
        # set, else the first strategy. The dashboard hot-swap calls
        # ``set_active_strategy`` to change this without re-creating the
        # engine.
        boot = normalize_strategy_name(settings.strategy)
        if boot == ALL_STRATEGIES_MODE:
            default_name = ALL_STRATEGIES_MODE
        elif boot in self._strategies_by_name:
            default_name = boot
        else:
            default_name = strategies[0].name if strategies else ""
        self._active_strategy_name: str = default_name
        self._strategy_ledger = StrategyPositionLedger()
        # parent_id -> strategy -> symbol -> signed intended delta (multi-mode fills)
        self._parent_attribution: dict[str, dict[str, dict[str, float]]] = {}
        self._parent_filled_qty: dict[str, float] = {}
        self._parent_ledger_applied_fraction: dict[str, float] = {}
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
        self._pretrade = PreTradeValidator(
            settings=settings,
            risk=self._risk,
            gateway=gateway,
            portfolio=self._portfolio,
            positions=self._positions,
        )
        self._executor.set_limit_collar_check(self._pretrade.check_limit_collar)
        self._latency = LatencyTracker(bus=bus)
        self._alerts = AlertManager(
            bus=bus,
            webhook_url=settings.alert_webhook_url,
            cooldown_sec=settings.alert_cooldown_sec,
        )
        self._md_quality = DataQualityMonitor(
            breaker=self._breaker,
            stale_resnapshot_sec=settings.md_stale_resnapshot_sec,
            crossed_book_breaker=settings.md_crossed_book_breaker,
        )
        self._md_bootstrap_done = False
        self._book_snapshot_sem = asyncio.Semaphore(8)
        self._resnapshot_inflight: set[str] = set()
        self._clock_skew_ms: float = 0.0
        self._clock_skew_synced: bool = False
        self._clock_skew_sync_tick: int = 0
        self._router = ExecutionRouter(
            wheel=self._wheel,
            executor=self._executor,
            features=self._features,
            tracker=self._exec_tracker,
            settings=settings,
        )
        self._order_reconciler = OrderReconciler(
            gateway=gateway,
            oms=self._oms,
            breaker=self._breaker,
            cancel_orphans=settings.reconcile_cancel_orphans,
            on_mismatch=self._on_order_reconcile_mismatch,
            bus=bus,
        )
        self._order_reconciler.apply_settings(settings)

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
            on_authoritative_snap=self._oms.touch_venue_truth_from_rest,
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
        # After a successful auto-flatten, MAJOR latched breaches persist until
        # re-arm; without this we'd re-run ``flatten()`` every heartbeat (spam
        # logs + venue churn). Cleared when ENGINE scope is no longer blocked.
        self._latched_major_flatten_done: bool = False
        # Futures venues: symbols we've already POSTed configured leverage for
        # this session (lazy — avoids N REST calls at startup for every subscribe).
        self._leverage_applied_symbols: set[str] = set()
        self._alert_task: asyncio.Task[None] | None = None

    def _reconcile_should_skip_rest(self) -> bool:
        """True when periodic reconcile should rely on user-data WS, not REST.

        Uses **WebSocket activity only**. Binance may send no ``ACCOUNT_UPDATE``
        for long stretches while holding exposure; successful REST reconciles
        refresh :attr:`OrderManager.last_venue_truth_ts` for health/ready
        without suppressing this poll.
        """
        if not self._settings.reconcile_skip_rest_when_user_data_fresh:
            return False
        ts = self._oms.last_ws_user_activity_ts
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

    @property
    def strategy_ledger(self) -> StrategyPositionLedger:
        return self._strategy_ledger

    def is_multi_strategy_mode(self) -> bool:
        return self._active_strategy_name == ALL_STRATEGIES_MODE

    def set_active_strategy(self, name: str) -> None:
        """Hot-swap the active strategy or enable multi-strategy netting.

        ``name`` must be a registered ``strategy.name`` or ``"all"`` to run
        every strategy with internal position netting. Raises ``ValueError``
        for unknown names so the API layer can surface a 400 to the dashboard.
        """
        raw = (name or "").strip()
        alias = normalize_strategy_name(raw)
        if alias == ALL_STRATEGIES_MODE:
            normalised = ALL_STRATEGIES_MODE
        elif raw in self._strategies_by_name:
            normalised = raw
        elif alias in self._strategies_by_name:
            normalised = alias
        else:
            known = set(self._strategies_by_name) | {ALL_STRATEGIES_MODE}
            available = ", ".join(sorted(known)) or "<none>"
            raise ValueError(f"unknown strategy {name!r}; available: {available}")
        if normalised == self._active_strategy_name:
            return
        previous = self._active_strategy_name
        self._active_strategy_name = normalised
        self._stop_monitor.set_externally_managed(self._compute_externally_managed())
        if normalised == ALL_STRATEGIES_MODE:
            sym_n = len(self._symbols)
            logger.info(
                "strategy hot-swap: %s -> all (netted, %d symbols)",
                previous or "<none>",
                sym_n,
            )
            return
        active = self._strategies_by_name.get(normalised)
        sym_n = len(active.symbols()) if active is not None else 0
        logger.info(
            "strategy hot-swap: %s -> %s (%d symbols)",
            previous or "<none>",
            normalised,
            sym_n,
        )

    def apply_breaker_rearm_side_effects(self, cleared_codes: set[str]) -> None:
        """Reset subsystem baselines when operator rearms latched breakers.

        Clearing the :class:`CircuitBreaker` alone is not enough for breaching
        conditions derived from streaks, drawdown anchors, or rolling TCA
        windows — those would re-trip on the next heartbeat.

        ``cleared_codes`` is the set of breach codes removed by this rearm
        (``active before`` minus ``active after``).
        """
        if not cleared_codes:
            return
        if "consecutive_losses" in cleared_codes:
            self._loss_tracker.clear_streak_after_rearm()
        if "daily_loss" in cleared_codes:
            self._loss_tracker.reanchor_daily_baseline_after_rearm()
        if "max_drawdown" in cleared_codes:
            self._portfolio.reanchor_session_start_equity_after_drawdown_rearm()
        if "hwm_drawdown" in cleared_codes:
            self._pnl.reanchor_hwm_after_drawdown_rearm()
        if "exec_quality" in cleared_codes:
            self._exec_tracker.clear_completed_history_after_rearm()

    def apply_settings_patch(self, patch: dict[str, Any]) -> Settings:
        """Merge ``patch`` into runtime ``Settings`` and refresh subsystems.

        Secrets omitted from the patch (empty / ``***``) keep their previous
        values. Some venue binds (e.g. ``api_host`` / ``api_port``) only take
        effect after an API server restart.
        """
        cur = self._settings.model_dump(mode="json")
        clean = dict(patch)
        for key in ("binance_api_key", "binance_api_secret"):
            val = clean.get(key)
            if val in (None, "", "***"):
                clean.pop(key, None)
        if isinstance(clean.get("strategy"), str):
            clean["strategy"] = normalize_strategy_name(clean["strategy"])
        merged = {**cur, **clean}
        new_settings = Settings.model_validate(merged)
        self._apply_runtime_settings(new_settings)
        return new_settings

    def _apply_runtime_settings(self, s: Settings) -> None:
        prev_leverage = self._settings.leverage
        self._settings = s
        if s.leverage != prev_leverage:
            self._leverage_applied_symbols.clear()
        self._risk.apply_settings(s)
        self._stop_monitor.replace_limits(Limits.from_settings(s))
        self._features.apply_settings(s)
        self._tape.set_window_sec(s.trade_tape_window_sec)
        self._executor.apply_settings(s)
        self._submit_guard.apply_settings(s)
        self._slippage_guard.set_cooldown_sec(s.breaker_minor_cooldown_sec)
        self._loss_tracker.apply_settings(s)
        self._exec_quality_guard.apply_settings(s)
        self._connection_monitor.apply_settings(s)
        self._reconciler.apply_settings(s)
        self._pretrade.apply_settings(s)
        self._order_reconciler.apply_settings(s)
        for strat in self._strategies:
            strat.refresh_settings(s)

    def _compute_externally_managed(self) -> set[str]:
        """Return symbols whose per-leg SL/TP bracket is suppressed.

        In multi-strategy mode every strategy that ``manages_own_risk()``
        contributes its symbols to the externally-managed set.
        """
        if self.is_multi_strategy_mode():
            managed: set[str] = set()
            for strat in self._strategies:
                if strat.manages_own_risk():
                    managed.update(strat.symbols())
            return managed
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
        gross_win, gross_loss = self._performance.gross_pnls()
        profit_factor = (gross_win / gross_loss) if gross_loss > 0 else None
        return EngineSnapshot(
            state=self._state,
            position_tracker=self._positions,
            portfolio=self._portfolio,
            trades=self._performance.trades(),
            win_rate=self._performance.win_rate(),
            gross_win_pnl=gross_win,
            gross_loss_pnl=gross_loss,
            profit_factor=profit_factor,
        )

    # --- Lifecycle ---

    async def start(self) -> None:
        if self._state.status is EngineStatus.RUNNING:
            return
        logger.info("engine starting")
        await self._gateway.connect()
        await self._refresh_clock_skew()

        if self._settings.recover_on_start and self._recovery_wal is not None:
            summary = await replay_wal_async(
                self._recovery_wal, self._oms, self._positions,
            )
            await self._bus.publish(
                Event(
                    type=EventType.STATUS,
                    payload={"replay_summary": asdict(summary)},
                    source="journal",
                )
            )

        # REST snapshots before WS: a single pass keeps lastUpdateId fresh; avoid
        # subscribing first (18s of parallel snapshots would stale early symbols).
        await self._bootstrap_order_books()

        # Public market WebSocket: ``bookTicker`` for mids, depth for L2,
        # ``!ticker@arr`` for rolling 24h volumes (avoids REST ``/ticker/24hr``).
        await self._gateway.subscribe_market_data(
            symbols=self._symbols,
            on_tick=self._on_tick,
            on_depth=self._on_depth,
            on_trade=self._on_trade,
            on_quote_volume_24h=self._on_quote_volume_24h,
            on_reconnect=self._on_market_ws_reconnect,
        )
        self._md_bootstrap_done = True

        # Prime mids from WS where possible; REST ``/depth`` only for stragglers.
        await self._prime_symbol_prices()

        # Seed cash + positions from REST once; live updates use user-data WS.
        balances, positions = await self._gateway.fetch_balances_and_positions()
        self._portfolio.seed_balances(balances)
        self._positions.seed(positions)
        self._oms.touch_venue_truth_from_rest()

        if getattr(self._settings, "order_reconcile_on_startup", True):
            await self._order_reconciler.sync_startup()

        subscribe_kw: dict[str, Any] = {
            "on_fill": self._on_fill,
            "on_order_update": self._on_order_update,
            "on_account_update": self._on_account_update,
        }
        if self._settings.venue == "binance":
            subscribe_kw["on_ws_connected"] = self._on_user_ws_connected
        await self._gateway.subscribe_user_data(**subscribe_kw)

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
        self._order_reconciler.start()
        self._alert_task = asyncio.create_task(self._alert_pump(), name="engine-alerts")
        logger.info("engine running")

    async def _bootstrap_order_books(self) -> None:
        """REST snapshot every symbol's L2 book before the depth stream is applied."""
        await self._resync_symbol_books(self._symbols, reason="startup", invalidate=False)

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
            book = self._books.get(symbol)
            if book.ready():
                mid = book.mid()
                if mid is not None:
                    self._latest_tick[symbol] = Tick(
                        symbol=symbol,
                        bid=book.best_bid() or mid,
                        ask=book.best_ask() or mid,
                        ts=time.time(),
                    )
                return
            async with sem:
                data = await self._gateway.book_snapshot(symbol, depth=5)
            last_id = int(data.get("lastUpdateId", 0))
            book.apply_snapshot(
                bids=[(float(p), float(q)) for p, q in data.get("bids", [])],
                asks=[(float(p), float(q)) for p, q in data.get("asks", [])],
                last_update_id=last_id,
            )
            self._md_quality.on_snapshot(symbol, last_id)
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
        if self._alert_task is not None:
            self._alert_task.cancel()
            try:
                await self._alert_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._alert_task = None
        await self._reconciler.stop()
        await self._order_reconciler.stop()
        await self._cancel_refresh_loops()
        await self._router.shutdown()
        try:
            await self._gateway.cancel_all_open_orders()
        except Exception:  # noqa: BLE001
            logger.exception("venue cancel_all_open_orders failed during stop")
        await self._oms.cancel_all()
        await self._gateway.disconnect()
        self._state.status = EngineStatus.STOPPED
        await self._publish_status()

    async def _flatten_and_wait_for_flat(self) -> None:
        """Submit reduce-only flatten orders and wait until venue is flat."""
        was_running = self._state.status is EngineStatus.RUNNING
        if was_running:
            await self.pause()
        try:
            await self.flatten()
            base_timeout = float(getattr(self._settings, "flatten_timeout_sec", 30.0))
            loop = asyncio.get_event_loop()
            deadline = loop.time() + max(base_timeout, 15.0)
            poll = 0.75
            while loop.time() < deadline:
                open_pos = await self._fetch_venue_open_positions()
                await self._positions.sync_from_venue(open_pos)
                if not open_pos:
                    logger.info("flatten complete: venue reports flat")
                    return
                logger.info(
                    "flatten wait: %d position(s) still open on venue, retrying",
                    len(open_pos),
                )
                await self._close_positions_for_flatten(open_pos, retry=True)
                await asyncio.sleep(poll)
            remaining = [p.symbol for p in await self._fetch_venue_open_positions()]
            if remaining:
                logger.warning(
                    "flatten timeout: %d positions still open on venue: %s",
                    len(remaining),
                    ",".join(remaining),
                )
        finally:
            try:
                open_pos = await self._fetch_venue_open_positions()
                await self._positions.sync_from_venue(open_pos)
            except Exception:  # noqa: BLE001
                logger.exception("final venue position sync after flatten failed")

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

    def _touch_symbol_from_book(self, symbol: str, book) -> None:
        """Keep per-symbol tick timestamps fresh when only depth (not bookTicker) updates."""
        mid = book.mid()
        if mid is None or mid <= 0:
            return
        bb = book.best_bid()
        ba = book.best_ask()
        recv_ts = time.time()
        self._latest_tick[symbol] = Tick(
            symbol=symbol,
            bid=bb if bb is not None else mid,
            ask=ba if ba is not None else mid,
            ts=recv_ts,
        )

    def _refresh_ticks_from_books(self, symbols: Iterable[str]) -> None:
        """Refresh tick receive times from ready L2 books (see _on_tick comment)."""
        for sym in symbols:
            book = self._books.get(sym)
            if book is None or not book.ready():
                continue
            self._touch_symbol_from_book(sym, book)

    async def _fetch_venue_open_positions(self) -> list[Position]:
        rows = await self._gateway.fetch_positions()
        return [p for p in rows if abs(p.qty) > 1e-12]

    def _flatten_close_mode(self, position: Position, *, retry: bool) -> str:
        """Pick market vs passive/aggressive VWAP for a flatten leg.

        Returns ``market``, ``flatten_passive`` (limit-heavy schedule), or
        ``flatten`` (short urgent VWAP with market fallback).
        """
        if retry:
            return "market"
        mid = self._mid_for(position.symbol)
        if mid is None or mid <= 0:
            return "market"
        notional = abs(position.qty) * mid
        max_market = float(getattr(self._settings, "flatten_market_max_notional_usd", 250.0))
        min_passive = float(getattr(self._settings, "flatten_vwap_min_notional_usd", 1500.0))
        wide_bps = float(getattr(self._settings, "flatten_wide_spread_bps", 100.0))
        passive_bps = float(getattr(self._settings, "flatten_passive_spread_bps", 20.0))
        feat = self._features.snapshot(position.symbol)
        spread = feat.spread_bps
        if notional <= max_market:
            return "market"
        if spread is not None and spread > wide_bps:
            return "market"
        if notional >= min_passive and spread is not None and spread <= passive_bps:
            return "flatten_passive"
        return "flatten"

    async def _close_positions_for_flatten(
        self,
        positions: list[Position],
        *,
        retry: bool = False,
    ) -> None:
        for position in positions:
            if abs(position.qty) <= 0:
                continue
            mode = self._flatten_close_mode(position, retry=retry)
            side = Side.SELL if position.qty > 0 else Side.BUY
            qty = abs(position.qty)
            if mode == "market":
                await self._market_reduce_only(position.symbol, side, qty)
                continue
            try:
                await self._router.submit(
                    symbol=position.symbol,
                    side=side,
                    qty=qty,
                    notes=mode,
                    reduce_only=True,
                    urgency=(
                        Urgency.PASSIVE
                        if mode == "flatten_passive"
                        else Urgency.AGGRESSIVE
                    ),
                )
                logger.info(
                    "flatten vwap %s %s %s qty=%.8f",
                    mode,
                    side.value,
                    position.symbol,
                    qty,
                )
            except ParentSubmissionRejected as exc:
                logger.warning(
                    "flatten vwap rejected %s (%s), falling back to market: %s",
                    position.symbol,
                    mode,
                    exc,
                )
                await self._market_reduce_only(position.symbol, side, qty)

    async def _market_reduce_only(
        self,
        symbol: str,
        side: Side,
        qty: float,
    ) -> None:
        """One-shot market reduce-only child; bypasses the VWAP router."""
        parent_id = f"P-flat-{symbol[:12]}"
        child = ChildOrder(
            id=new_client_order_id(parent_id, 0),
            parent_id=parent_id,
            symbol=symbol,
            side=side,
            qty=qty,
            price=None,
            order_type=OrderType.MARKET,
            reduce_only=True,
        )
        try:
            await self._oms.submit_child(child)
            logger.info(
                "flatten market %s %s qty=%.8f",
                side.value,
                symbol,
                qty,
            )
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "code", None) == -2022:
                logger.info("flatten skip %s: already flat at venue", symbol)
                return
            logger.warning("flatten market failed %s: %s", symbol, exc)

    def _is_emergency_flatten_fill(self, fill: Fill) -> bool:
        """True for reduce-only unwind parents (auto-/operator-flatten path).

        VWAP/market slices from those parents must not advance the
        consecutive-loss streak — a single flatten can emit many closes
        that would otherwise instantly re-trip ``consecutive_losses``.
        """
        pid = fill.parent_id or ""
        if pid.startswith("P-flat-"):
            return True
        parent = self._oms.parent(pid) if pid else None
        if parent is None:
            return False
        return parent.notes in ("flatten", "flatten_passive")

    async def flatten(self) -> None:
        """Cancel working orders + close all venue positions (market or VWAP)."""
        if self._auto_flatten_in_progress:
            logger.warning("flatten skipped: flatten already in progress")
            return
        self._auto_flatten_in_progress = True
        logger.warning("flattening all positions")
        try:
            try:
                await self._gateway.cancel_all_open_orders()
            except Exception:  # noqa: BLE001
                logger.exception("venue cancel_all_open_orders failed during flatten")
            await self._oms.cancel_all()
            try:
                await self._order_reconciler.reconcile_once(trip_on_mismatch=False)
            except Exception:  # noqa: BLE001
                logger.exception("order reconcile after flatten failed")
            rounds = max(3, int(getattr(self._settings, "flatten_rounds", 4)))
            for attempt in range(rounds):
                try:
                    open_pos = await self._fetch_venue_open_positions()
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "fetch_positions failed during flatten (attempt %d)",
                        attempt + 1,
                    )
                    open_pos = self._positions.all()
                await self._positions.sync_from_venue(open_pos)
                if not open_pos:
                    logger.info("flatten: venue flat after attempt %d", attempt + 1)
                    return
                logger.info(
                    "flatten attempt %d/%d: closing %d position(s)",
                    attempt + 1,
                    rounds,
                    len(open_pos),
                )
                await self._close_positions_for_flatten(
                    open_pos,
                    retry=attempt > 0,
                )
                await asyncio.sleep(1.5)
        finally:
            self._auto_flatten_in_progress = False

    async def operator_halt(
        self,
        *,
        detail: str = "",
        flatten: bool = True,
        pause: bool = True,
    ) -> None:
        """Operator trading halt: latch a MAJOR engine breaker and flatten."""
        logger.error("operator halt requested%s", f": {detail}" if detail else "")
        self._breaker.trip(
            Breach(
                code="operator_halt",
                scope=BreakerScope.ENGINE,
                severity=BreakerSeverity.MAJOR,
                detail=detail or "operator_halt",
            )
        )
        if flatten:
            try:
                try:
                    await self._gateway.cancel_all_open_orders()
                except Exception:  # noqa: BLE001
                    logger.exception("venue cancel_all before operator halt failed")
                await self.flatten()
            except Exception:  # noqa: BLE001
                logger.exception("operator halt flatten failed")
        if pause:
            await self.pause()

    # --- Market data callbacks ---

    async def _on_tick(self, tick: Tick) -> None:
        # Wall-clock receive time for `Tick.ts`: Binance `E` is the exchange
        # time of the last *BBO change*. With an unchanged best bid/ask the
        # feed can go quiet for minutes while quotes stay valid; using `E`
        # makes MarketDataGuard think the tick is stale (age vs `time.time()`).
        recv_ts = time.time()
        self._latest_tick[tick.symbol] = Tick(
            symbol=tick.symbol,
            bid=tick.bid,
            ask=tick.ask,
            last=tick.last,
            ts=recv_ts,
        )
        # ConnectionMonitor: any public-WS activity (same reasoning as above).
        self._state.last_tick_ts = recv_ts
        self._latency.on_tick(tick.symbol)
        await self._positions.on_tick(tick)
        # TICK is a firehose (one event per BBO change × symbol). Only publish when
        # tick archival is enabled; otherwise it floods subscriber queues and drops
        # STATUS/EQUITY events the dashboard relies on.
        if self._settings.persist_record_ticks:
            await self._bus.publish(
                Event(
                    type=EventType.TICK,
                    payload={
                        "symbol": tick.symbol,
                        "bid": tick.bid,
                        "ask": tick.ask,
                        "mid": tick.mid,
                    },
                )
            )

    async def _on_depth(self, diff: DepthDiff) -> None:
        if not self._md_bootstrap_done:
            return
        self._state.last_tick_ts = time.time()
        book = self._books.get(diff.symbol)
        action, gap = self._md_quality.assess(
            diff,
            book_ready=book.ready(),
            book_last_update_id=book.last_update_id,
        )
        if action is DiffAction.RESNAPSHOT:
            if gap > 0:
                self._md_quality.record_gap(diff.symbol, gap)
            sym = diff.symbol
            if sym not in self._resnapshot_inflight:
                self._resnapshot_inflight.add(sym)
                try:
                    await self._snapshot_book(sym)
                finally:
                    self._resnapshot_inflight.discard(sym)
            action, gap = self._md_quality.assess(
                diff,
                book_ready=book.ready(),
                book_last_update_id=book.last_update_id,
            )
        if action is DiffAction.DROP_STALE:
            return
        if action is DiffAction.RESNAPSHOT:
            return
        book.apply_diff(diff)
        self._md_quality.on_applied(
            diff, best_bid=book.best_bid(), best_ask=book.best_ask(),
        )
        self._touch_symbol_from_book(diff.symbol, book)

    async def _on_market_ws_reconnect(self, symbols: list[str]) -> None:
        """REST-resync L2 books after a public market WebSocket reconnect."""
        await self._resync_symbol_books(symbols, reason="reconnect")

    async def _resync_symbol_books(
        self,
        symbols: list[str],
        *,
        reason: str,
        invalidate: bool = True,
    ) -> None:
        if not symbols:
            return
        logger.info("%s: resyncing %d symbol L2 book(s)", reason, len(symbols))
        if invalidate:
            self._md_quality.invalidate(symbols)
            for sym in symbols:
                self._books.get(sym).invalidate()

        async def _one(symbol: str) -> None:
            await self._snapshot_book(symbol)

        results = await asyncio.gather(*(_one(s) for s in symbols), return_exceptions=True)
        failures = sum(1 for r in results if isinstance(r, Exception))
        if failures:
            logger.warning(
                "%s book resync: %d/%d snapshots failed",
                reason,
                failures,
                len(symbols),
            )

    async def _on_trade(self, trade: TapeTrade) -> None:
        self._state.last_tick_ts = time.time()
        self._tape.record(trade)

    async def _on_quote_volume_24h(self, symbol: str, quote_vol: float) -> None:
        """Rolling 24h quote-asset volume from public WS (``!ticker@arr``)."""
        sym = symbol.upper()
        if sym not in self._symbols:
            return
        self._state.last_tick_ts = time.time()
        self._volume_weights[sym] = quote_vol

    async def _on_order_update(self, update: ChildOrder) -> None:
        await self._oms.on_order_update(update)
        if update.status is OrderStatus.ACK:
            self._latency.on_venue_ack(update.symbol)

    async def _on_fill(self, fill: Fill) -> None:
        # Exchange-reported fill price only for PnL; arrival vs VWAP slippage is in ExecutionTracker.
        fill.venue_price = fill.price
        fill.impact_bps = 0.0

        if not await self._oms.on_fill(fill):
            return
        pre_position = self._positions.get(fill.symbol)
        classification = classify_fill(pre_position, fill)
        if self._is_emergency_flatten_fill(fill):
            classification = FillClassification(
                action=classification.action,
                entry_price=classification.entry_price,
                exit_price=classification.exit_price,
                pnl=None,
            )
        # Binance ACCOUNT_UPDATE already carries authoritative ``pa``; applying
        # the same ORDER_TRADE_UPDATE fill doubles qty when events arrive out
        # of order (ACCOUNT_UPDATE first — observed on CRVUSDC).
        if not self._positions_from_account_updates():
            await self._positions.on_fill(fill)
        position = self._positions.get(fill.symbol) or Position(symbol=fill.symbol)
        self._risk.on_fill(fill, position)
        record = self._performance.record_fill(fill, classification)
        await self._bus.publish(
            Event(
                type=EventType.FILL,
                payload={
                    **_fill_to_dict(fill),
                    "action": record.action,
                    "entry_price": record.entry_price,
                    "exit_price": record.exit_price,
                    "pnl": record.pnl,
                },
            )
        )
        if fill.parent_id:
            await self._exec_tracker.on_fill(
                parent_id=fill.parent_id,
                side=fill.side,
                qty=fill.qty,
                venue_price=fill.venue_price,
                impact_bps=fill.impact_bps,
                fee=fill.fee,
            )
            # In-flight slippage abort: cancel + breach when the parent's
            # realised VWAP moves past `max_slippage_bps` from arrival.
            parent = self._oms.parent(fill.parent_id)
            if parent is not None:
                await self._slippage_guard.on_fill(
                    fill.parent_id, parent.max_slippage_bps,
                )
        self._notify_strategies_on_fill(fill)

    def _notify_strategies_on_fill(self, fill: Fill) -> None:
        parent_id = fill.parent_id or ""
        parent = self._oms.parent(parent_id) if parent_id else None
        attr = self._parent_attribution.get(parent_id) if parent_id else None

        if self.is_multi_strategy_mode() and attr:
            self._apply_attributed_fill(fill, parent_id, attr, parent)
            return

        strategy_name = parent.strategy_name if parent else ""
        if (
            self.is_multi_strategy_mode()
            and strategy_name
            and strategy_name in self._strategies_by_name
        ):
            self._strategy_ledger.apply_fill(strategy_name, fill.symbol, fill.side, fill.qty)
            self._call_strategy_on_fill(strategy_name, fill)
            return

        active = self._strategies_by_name.get(self._active_strategy_name)
        if active is not None:
            self._call_strategy_on_fill(active.name, fill)

    def _apply_attributed_fill(
        self,
        fill: Fill,
        parent_id: str,
        attr: dict[str, dict[str, float]],
        parent: ParentOrder | None,
    ) -> None:
        parent_qty = parent.qty if parent is not None and parent.qty > 0 else fill.qty
        cumulative = self._parent_filled_qty.get(parent_id, 0.0) + fill.qty
        self._parent_filled_qty[parent_id] = cumulative
        fill_fraction = min(1.0, cumulative / parent_qty) if parent_qty > 0 else 1.0
        prev = self._parent_ledger_applied_fraction.get(parent_id, 0.0)
        delta_fraction = fill_fraction - prev
        self._parent_ledger_applied_fraction[parent_id] = fill_fraction

        sym = fill.symbol.upper()
        for strat, sym_deltas in attr.items():
            intended = sym_deltas.get(sym, 0.0)
            if abs(intended) < 1e-12:
                continue
            self._strategy_ledger.apply_delta(strat, sym, intended * delta_fraction)
            self._call_strategy_on_fill(strat, fill)

        if fill_fraction >= 1.0:
            self._parent_attribution.pop(parent_id, None)
            self._parent_filled_qty.pop(parent_id, None)
            self._parent_ledger_applied_fraction.pop(parent_id, None)

    def _call_strategy_on_fill(self, strategy_name: str, fill: Fill) -> None:
        strat = self._strategies_by_name.get(strategy_name)
        if strat is None:
            return
        try:
            strat.on_fill(fill.symbol, fill.qty, fill.side.value)
        except Exception:  # noqa: BLE001
            logger.exception("strategy %s on_fill raised", strategy_name)

    async def _on_user_ws_connected(self) -> None:
        """Resync wallet + positions after user-data WS reconnect.

        Events may have been missed while the socket was down; REST is authoritative.
        """
        if self._settings.venue != "binance":
            return
        try:
            balances, positions = await self._gateway.fetch_balances_and_positions()
        except Exception:  # noqa: BLE001
            logger.exception("user_ws reconnect: venue snapshot failed")
            return
        if balances:
            self._portfolio.update_balances(balances)
        open_pos = [p for p in positions if abs(p.qty) > 1e-12]
        await self._positions.sync_from_venue(open_pos)
        # Treat a successful post-reconnect REST snapshot like fresh user-data so
        # age / reconcile-skip reflect recovered venue connectivity, not silence.
        self._oms.touch_ws_user_data_activity()
        logger.info("user_ws reconnect: synced %d open position(s) from venue", len(open_pos))

    async def _on_account_update(self, update: dict) -> None:
        """Apply exchange-reported wallet + position state.

        ``ACCOUNT_UPDATE`` only carries the *changed* assets in its ``B``
        array, so we merge per-asset into the portfolio rather than
        overwriting cash. Without this merge a USDC-only fill drops the
        USDT wallet to ``0`` because the message never mentions it. The
        portfolio's ``cash`` view continues to apply the USDT+USDC
        stablecoin combine rule.
        """
        self._oms.touch_ws_user_data_activity()
        for asset, balance in wallet_by_asset.items():
            try:
                self._portfolio.update_asset_balance(str(asset), float(balance))
            except (TypeError, ValueError):
                continue

        positions = update.get("positions") or []
        await self._positions.apply_exchange_positions(positions)

    # --- Heartbeat ---

    def _user_data_fresh(self) -> bool:
        ts = self._oms.last_ws_user_activity_ts
        if ts <= 0:
            return False
        return (time.time() - ts) < float(self._settings.reconcile_user_data_fresh_sec)

    def _positions_from_account_updates(self) -> bool:
        """True when venue user-data ACCOUNT_UPDATE drives position qty."""
        if self._settings.venue != "binance":
            return False
        return self._user_data_fresh()

    async def _refresh_clock_skew(self) -> None:
        try:
            await self._gateway.sync_clock()
            self._clock_skew_ms = float(self._gateway.clock_skew_ms())
            self._clock_skew_synced = True
        except Exception:  # noqa: BLE001
            logger.debug("clock skew sync failed", exc_info=True)

    async def _on_clock_tick(self) -> None:
        self._clock_skew_sync_tick += 1
        if self._clock_skew_sync_tick % 15 == 0:
            await self._refresh_clock_skew()

        await self._portfolio.mark_to_market(use_mark_pnl=not self._user_data_fresh())
        # Refresh portfolio guards before the breaker advances so a
        # newly tripped MAJOR is honoured this same tick.
        self._pnl.update()
        if not self._auto_flatten_in_progress:
            self._loss_tracker.update()
        self._exec_quality_guard.evaluate()
        has_working_orders = any(True for _ in self._oms.working_children())
        self._connection_monitor.evaluate(
            now=time.time(),
            last_tick_ts=self._state.last_tick_ts,
            last_user_data_ts=self._oms.last_ws_user_activity_ts,
            engine_running=self._state.status is EngineStatus.RUNNING,
            check_user_data_stale=has_working_orders,
        )
        # Advance the circuit-breaker so cooled-down minor breaches return
        # to ARMED. Runs even when paused so the engine can auto-resume
        # on the next operator action without a stale block.
        self._breaker.tick()
        # Auto-flatten on a fresh ENGINE-scope MAJOR breach. Idempotent
        # via `_flatten_in_progress`: subsequent ticks during an active
        # flatten don't spawn duplicates.
        await self._maybe_flatten_for_breaker()
        # Ops metrics (tick age, md_health, breakers) must update even when paused
        # so the console stays live while the operator inspects a halt.
        await self._publish_ops_status()
        if self._state.status is not EngineStatus.RUNNING:
            return

        # Risk-driven exits first; an exit can't be vetoed by risk again
        # because it's already a closing trade.
        await self._latency.maybe_emit(self._settings.latency_metrics_interval_sec)
        stale = self._md_quality.tick_staleness(now=time.time())
        for sym in stale[:_MAX_MD_RESNAPSHOTS_PER_TICK]:
            await self._snapshot_book(sym)
        active = self._strategies_by_name.get(self._active_strategy_name)
        if active is not None:
            self._refresh_ticks_from_books(active.symbols())
        await self._evaluate_exits()
        await self._evaluate_strategies()

    async def _maybe_flatten_for_breaker(self) -> None:
        if not self._breaker.is_blocked(BreakerScope.ENGINE):
            self._auto_flatten_in_progress = False
            self._latched_major_flatten_done = False
            return
        if self._auto_flatten_in_progress:
            return
        # Only flatten on MAJOR engine-scope trips; minor cooldowns just
        # pause new orders.
        active = [s for s in self._breaker.active() if s.scope is BreakerScope.ENGINE]
        if not any(s.severity is BreakerSeverity.MAJOR for s in active):
            return
        if self._latched_major_flatten_done:
            return
        codes = ",".join(s.code for s in active)
        logger.error("auto-flatten triggered by engine breaker(s): %s", codes)
        try:
            await self.flatten()
        except Exception:  # noqa: BLE001
            logger.exception("auto-flatten failed")
        else:
            self._latched_major_flatten_done = True

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
        if self.is_multi_strategy_mode():
            await self._evaluate_all_strategies_netted()
            return
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

    async def _evaluate_all_strategies_netted(self) -> None:
        tagged: list[tuple[str, Signal]] = []
        for strat in self._strategies:
            try:
                feats = {sym: self._features.snapshot(sym) for sym in strat.symbols()}
                for sig in strat.on_tick(feats):
                    tagged.append((strat.name, sig))
            except Exception:  # noqa: BLE001
                logger.exception("strategy %s on_tick raised", strat.name)
        if not tagged:
            return
        result = net_strategy_signals(tagged)
        for netted in result.loose:
            await self._dispatch_netted_single(netted)
        for gid, legs in result.groups.items():
            await self._dispatch_group(gid, legs)

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

    async def _dispatch_netted_single(self, netted: NettedSignal) -> None:
        """Submit a cross-strategy net order and record fill attribution."""
        signal = netted.signal
        parent = await self._dispatch_single(signal, return_parent=True)
        if parent is None:
            return
        sym = signal.symbol.upper()
        self._parent_attribution[parent.id] = {
            strat: {sym: delta} for strat, delta in netted.contributions.items()
        }

    async def _dispatch_single(
        self,
        signal: Signal,
        *,
        return_parent: bool = False,
    ) -> ParentOrder | None:
        """Pre-trade validate + submit one ungrouped signal."""
        mid = self._mid_for(signal.symbol)
        if mid is None:
            return None
        tick = self._latest_tick.get(signal.symbol)
        feat = self._features.snapshot(signal.symbol)
        self._latency.on_signal(signal.symbol)
        result = self._pretrade.validate_single(
            signal,
            mid,
            tick_ts=tick.ts if tick is not None else None,
            spread_bps=feat.spread_bps,
        )
        if not result.approved:
            logger.info("pretrade vetoed %s: %s", signal.symbol, result.reason)
            return None
        self._latency.on_risk_passed(signal.symbol)
        try:
            if not signal.reduce_only:
                await self._ensure_leverage_before_entry(signal.symbol)
            parent = await self._router.submit(
                symbol=signal.symbol,
                side=signal.side,
                qty=result.qty,
                notes=signal.reason,
                signal_score=signal.score,
                reduce_only=signal.reduce_only,
                strategy_name=signal.strategy_name,
            )
            self._latency.on_child_submitted(signal.symbol)
            if return_parent:
                return parent
        except ParentSubmissionRejected as exc:
            logger.info("router gated %s: %s", signal.symbol, exc)
        return None

    async def _dispatch_group(self, group_id: str, legs: list[Signal]) -> None:
        """Submit pair legs with unified pre-trade checks and compensating unwind."""
        mids: dict[str, float] = {}
        tick_ts: dict[str, float | None] = {}
        spread_bps: dict[str, float | None] = {}
        floors: dict[str, float] = {}

        for leg in legs:
            mid = self._mid_for(leg.symbol)
            if mid is None or mid <= 0:
                logger.info("group %s aborted: no mid for %s", group_id, leg.symbol)
                return
            tick = self._latest_tick.get(leg.symbol)
            feat = self._features.snapshot(leg.symbol)
            mids[leg.symbol] = mid
            tick_ts[leg.symbol] = tick.ts if tick is not None else None
            spread_bps[leg.symbol] = feat.spread_bps
            floor = venue_min_qty(
                mid=mid,
                filters=self._gateway.get_symbol_filters(leg.symbol),
            )
            if floor is None:
                logger.info("group %s aborted: venue vetoed %s", group_id, leg.symbol)
                return
            floors[leg.symbol] = floor

        strategy_qty = max((leg.qty for leg in legs if leg.qty > 0), default=0.0)
        pair_qty = max(strategy_qty, max(floors.values()))
        # Apply per-leg risk caps at the proposed pair size so validate_group
        # does not veto with risk_scale when one leg notional exceeds max_risk_pct.
        min_allowed = pair_qty
        for leg in legs:
            probe = Signal(
                symbol=leg.symbol,
                side=leg.side,
                qty=pair_qty,
                reason=leg.reason,
                score=leg.score,
                group_id=leg.group_id,
            )
            decision = self._risk.check(
                probe,
                mids[leg.symbol],
                tick_ts=tick_ts[leg.symbol],
                spread_bps=spread_bps[leg.symbol],
            )
            if not decision.approved:
                logger.info(
                    "group %s aborted: %s for %s",
                    group_id,
                    decision.reason,
                    leg.symbol,
                )
                return
            min_allowed = min(min_allowed, decision.qty)
        pair_qty = min_allowed
        for leg in legs:
            filt = self._gateway.get_symbol_filters(leg.symbol)
            pair_qty = min(pair_qty, venue_cap_qty(pair_qty, filt))
        if pair_qty <= 0:
            logger.info("group %s aborted: venue max_qty caps pair to zero", group_id)
            return
        result = self._pretrade.validate_group(
            legs,
            pair_qty,
            mids,
            tick_ts_by_symbol=tick_ts,
            spread_bps_by_symbol=spread_bps,
        )
        if not result.approved:
            logger.info("group %s pretrade veto: %s", group_id, result.reason)
            return

        for leg in legs:
            allowed, reason = self._submit_guard.can_submit_parent(leg.symbol)
            if not allowed:
                logger.info("group %s aborted: %s for %s", group_id, reason, leg.symbol)
                return

        logger.info(
            "group %s submitting %d legs at pair_qty=%.8f",
            group_id, len(legs), pair_qty,
        )
        submitted: list[tuple[Signal, ParentOrder]] = []
        for leg in legs:
            try:
                await self._ensure_leverage_before_entry(leg.symbol)
                self._latency.on_risk_passed(leg.symbol)
                parent = await self._router.submit(
                    symbol=leg.symbol,
                    side=leg.side,
                    qty=pair_qty,
                    notes=leg.reason,
                    signal_score=leg.score,
                    group_id=group_id,
                    strategy_name=leg.strategy_name,
                )
                self._latency.on_child_submitted(leg.symbol)
                submitted.append((leg, parent))
                if leg.strategy_name and self.is_multi_strategy_mode():
                    sym = leg.symbol.upper()
                    delta = pair_qty if leg.side is Side.BUY else -pair_qty
                    self._parent_attribution.setdefault(parent.id, {}).setdefault(
                        leg.strategy_name, {},
                    )[sym] = delta
            except ParentSubmissionRejected as exc:
                await self._compensate_group_submission(group_id, submitted, exc)
                return

    async def _compensate_group_submission(
        self,
        group_id: str,
        submitted: list[tuple[Signal, ParentOrder]],
        exc: ParentSubmissionRejected,
    ) -> None:
        logger.error(
            "group %s partial failure after %d leg(s): %s — unwinding",
            group_id, len(submitted), exc,
        )
        await self._bus.publish(
            Event(
                type=EventType.LOG,
                payload={
                    "level": LogLevel.ERROR.value,
                    "message": (
                        f"group {group_id} partial submit; compensating unwind "
                        f"({len(submitted)} leg(s)): {exc}"
                    ),
                },
                source="engine",
            )
        )
        for leg, parent in submitted:
            try:
                await self._router.submit(
                    symbol=leg.symbol,
                    side=leg.side.opposite,
                    qty=parent.qty,
                    notes=f"compensate:{group_id}",
                    reduce_only=True,
                    urgency=Urgency.AGGRESSIVE,
                )
            except Exception:  # noqa: BLE001
                logger.exception("compensating unwind failed for %s", leg.symbol)
                self._breaker.trip(
                    Breach(
                        code="group_unwind_failed",
                        scope=BreakerScope.SYMBOL,
                        severity=BreakerSeverity.MAJOR,
                        target=leg.symbol,
                        detail=f"group={group_id}",
                    )
                )

    # --- Helpers ---

    async def _ensure_leverage_before_entry(self, symbol: str) -> None:
        """POST venue leverage once per symbol before the first opening order.

        Reduce-only exits skip this path. No-op when leverage <= 1 or the
        gateway does not implement futures leverage (spot / mocks).
        """
        lev = self._settings.leverage
        if not lev or lev <= 1:
            return
        sym_u = symbol.upper()
        if sym_u in self._leverage_applied_symbols:
            return
        await self._gateway.set_leverage(sym_u, lev)
        self._leverage_applied_symbols.add(sym_u)

    async def _snapshot_book(self, symbol: str) -> None:
        async with self._book_snapshot_sem:
            try:
                data = await self._gateway.book_snapshot(symbol, depth=100)
            except Exception:  # noqa: BLE001
                logger.exception("book snapshot failed for %s", symbol)
                return
            last_id = int(data.get("lastUpdateId", 0))
            self._books.get(symbol).apply_snapshot(
                bids=[(float(p), float(q)) for p, q in data.get("bids", [])],
                asks=[(float(p), float(q)) for p, q in data.get("asks", [])],
                last_update_id=last_id,
            )
            self._md_quality.on_snapshot(symbol, last_id)

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

    def _user_data_health(self, now: float) -> dict[str, float | bool]:
        """User-data freshness for ops UI.

        ``user_data_age_sec`` is time since the last *authoritative* venue
        alignment (user-data WebSocket event or successful REST account
        reconcile). ``user_ws_event_age_sec`` is WS-only silence — can be
        high while holding exposure with no fills; see ConnectionMonitor.
        """
        ws_ts = self._oms.last_ws_user_activity_ts
        truth_ts = self._oms.last_venue_truth_ts
        ws_age = (now - ws_ts) if ws_ts > 0 else -1.0
        truth_age = (now - truth_ts) if truth_ts > 0 else -1.0
        monitored = (
            self._state.status is EngineStatus.RUNNING
            and any(True for _ in self._oms.working_children())
        )
        stale = (
            monitored
            and ws_ts > 0
            and ws_age > float(self._settings.ws_stale_pause_sec)
        )
        has_exposure = self.snapshot().gross_notional > 1e-6
        reconcile_stale = (
            self._state.status is EngineStatus.RUNNING
            and has_exposure
            and truth_ts > 0
            and truth_age > float(self._settings.reconcile_user_data_fresh_sec)
        )
        return {
            "user_data_age_sec": truth_age,
            "user_ws_event_age_sec": ws_age,
            "user_data_monitored": monitored,
            "user_data_stale": stale,
            "user_data_reconcile_stale": reconcile_stale,
        }

    def _portfolio_health(self) -> dict[str, float]:
        snap = self.snapshot()
        return {
            "gross_notional": snap.gross_notional,
            "net_notional": snap.net_notional,
            "realized_pnl": snap.realized_pnl,
            "unrealized_pnl": snap.unrealized_pnl,
            "equity": snap.equity,
        }

    async def _publish_ops_status(self) -> None:
        now = time.time()
        tick_age = (now - self._state.last_tick_ts) if self._state.last_tick_ts > 0 else -1.0
        await self._bus.publish(
            Event(
                type=EventType.STATUS,
                payload={
                    "kind": "system_health",
                    "latency": self._latency.histograms(),
                    "order_reconcile": dict(self._order_reconciler.last_result),
                    "md_health": self._md_quality.metrics(),
                    "clock_skew_ms": self._clock_skew_ms,
                    "clock_skew_synced": self._clock_skew_synced,
                    "tick_age_sec": tick_age,
                    **self._user_data_health(now),
                    **self._portfolio_health(),
                    "active_breakers": [s.code for s in self._breaker.active()],
                },
                source="engine",
            )
        )

    async def _alert_pump(self) -> None:
        try:
            async with self._bus.subscribe(types=[EventType.BREAKER]) as queue:
                while True:
                    event = await queue.get()
                    sev = str(event.payload.get("severity", "")).lower()
                    if sev == BreakerSeverity.MAJOR.value:
                        await self._alerts.fire(
                            "major_breaker",
                            f"MAJOR breaker: {event.payload.get('code')}",
                            extra=event.payload,
                        )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("alert pump crashed")

    async def _on_order_reconcile_mismatch(self, result: dict[str, object]) -> None:
        await self._alerts.fire(
            "order_reconcile_mismatch",
            f"order reconcile mismatch venue_only={result.get('venue_only')} "
            f"local_only={result.get('local_only')}",
            extra=result,
        )

    def system_health(self) -> dict[str, object]:
        """Snapshot for REST /api/state."""
        now = time.time()
        return {
            "latency": self._latency.histograms(),
            "order_reconcile": dict(self._order_reconciler.last_result),
            "md_health": self._md_quality.metrics(),
            "clock_skew_ms": self._clock_skew_ms,
            "clock_skew_synced": self._clock_skew_synced,
            "tick_age_sec": (now - self._state.last_tick_ts) if self._state.last_tick_ts > 0 else -1.0,
            **self._user_data_health(now),
            "active_breakers": [s.code for s in self._breaker.active()],
            **self._portfolio_health(),
        }


