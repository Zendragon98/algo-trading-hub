"""Top-level engine orchestrator.

Wires every subsystem together and owns the asyncio task that drives the
strategy loop. The engine is the only object the API layer holds a
reference to; everything else is reached via accessors.

Lifecycle:
    `start()`  - connect gateway, subscribe streams, seed positions,
                 mark engine RUNNING, start the heartbeat clock.
    `pause()`  - keep streams alive but stop emitting new orders.
                 Existing positions are still monitored.
    `resume()` - REST-sync wallet, positions, and open orders, then RUNNING.
    `stop()`   - flatten + cancel everything, disconnect, mark STOPPED.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from common.config import Settings, normalize_strategy_name
from common.enums import EngineStatus, EventType, OrderStatus, OrderType, Side, Urgency
from common.events import Event, EventBus
from common.logging import apply_log_level, group_signal_log, resolve_log_level, signal_log_emit
from common.types import (
    ChildOrder,
    Fill,
    ParentOrder,
    Position,
    Signal,
    TapeTrade,
    Tick,
)
from gateways.gateway_interface import DepthDiff, GatewayInterface

from ..execution.algo_wheel import AlgoWheel, WheelConfig
from ..execution.execution_metrics import ExecutionTracker
from ..execution.execution_router import ExecutionRouter, ParentSubmissionRejected
from ..execution.quality_guard import ExecutionQualityGuard
from ..execution.quote_executor import QuoteExecutor
from ..execution.slippage_guard import SlippageGuard
from ..execution.submit_guard import SubmitGuard
from ..execution.vwap_executor import VwapExecutor
from ..market_data.data_quality import DataQualityMonitor, DiffAction
from ..market_data.feature_store import FeatureStore
from ..market_data.microstructure_hub import MicrostructureHub
from ..market_data.orderbook import OrderBookStore
from ..market_data.own_quote_book import OwnBookState, OwnQuoteBook
from ..market_data.symbol_calibration import invalidate_cache
from ..market_data.trade_tape import TradeTape
from ..observability.alert_manager import AlertManager
from ..observability.latency_tracker import LatencyTracker
from ..orders.order_manager import OrderManager, _fill_to_dict, new_client_order_id
from ..performance.fill_classification import classify_fill, position_before_fill
from ..performance.performance_tracker import PerformanceTracker
from ..persistence.journal import replay_wal_async
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
from ..risk.mm_flow_guard import MmFlowGuard
from ..risk.pnl_tracker import PnLTracker
from ..risk.pretrade_validator import PreTradeValidator
from ..risk.risk_manager import ExitIntent, RiskManager
from ..risk.stop_loss import StopLossMonitor
from ..risk.venue_sizing import venue_cap_qty, venue_min_qty, venue_qty_in_bounds
from ..strategies import mm_core
from ..strategies.market_making import MarketMakingStrategy
from ..strategies.market_making_v2 import MarketMakingV2Strategy
from ..strategies.signal_netter import NettedSignal, net_strategy_signals
from ..strategies.strategy_base import StrategyBase
from .clock import Clock
from .connection_monitor import ConnectionMonitor
from .order_reconciliation import OrderReconciler
from .reconciliation import Reconciler
from .state import EngineSnapshot, EngineState

logger = logging.getLogger(__name__)


@dataclass
class StartupProgress:
    """Transient startup / book-resync progress surfaced on the dashboard."""

    phase: str
    label: str
    done: int = 0
    total: int = 0
    symbol: str | None = None


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
        event_archive_dir: Path | None = None,
    ) -> None:
        self._settings = settings
        self._bus = bus
        self._gateway = gateway
        self._recovery_wal = recovery_wal
        self._event_archive_dir = event_archive_dir
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

        # Subscribe only to the active strategy's universe (+ open positions).
        # Hot-swap calls ``refresh_market_universe`` to resubscribe without restart.
        self._symbols = self._resolve_market_symbols()

        # Market data layer
        self._books = OrderBookStore(self._symbols)
        self._tape = TradeTape(
            window_sec=settings.trade_tape_window_sec,
            large_trade_mult=settings.mm_large_trade_mult,
        )
        self._micro = MicrostructureHub(self._books, self._tape, settings)
        self._features = FeatureStore(self._books, self._tape, settings, hub=self._micro)
        self._own_book = OwnQuoteBook(markout_cooldown_sec=settings.mm_markout_cooldown_sec)
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
        self._wheel = AlgoWheel(WheelConfig.from_settings(settings))
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
        self._start_lock = asyncio.Lock()
        self._book_snapshot_sem = asyncio.Semaphore(
            max(1, int(getattr(settings, "book_resync_concurrency", 8))),
        )
        self._reconnect_resync_lock = asyncio.Lock()
        self._reconnect_resync_pending: set[str] = set()
        self._reconnect_resync_debounce_task: asyncio.Task[None] | None = None
        self._bulk_resync_symbols: set[str] = set()
        self._resnapshot_inflight: set[str] = set()
        self._gap_resync_pending: set[str] = set()
        self._gap_resync_lock = asyncio.Lock()
        self._gap_resync_task: asyncio.Task[None] | None = None
        self._capture_flush_task: asyncio.Task[None] | None = None
        self._clock_skew_ms: float = 0.0
        self._clock_skew_synced: bool = False
        self._clock_skew_sync_tick: int = 0
        self._quote_executor = QuoteExecutor(
            order_manager=self._oms,
            own_book=self._own_book,
            settings=settings,
        )
        self._mm_flow = MmFlowGuard(settings)
        self._router = ExecutionRouter(
            wheel=self._wheel,
            executor=self._executor,
            features=self._features,
            tracker=self._exec_tracker,
            settings=settings,
        )
        self._wire_mm_strategies()
        self._warn_multi_strategy_symbol_overlap()
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
        self._market_capturer = None

        # Background refresh loops spawned on start(), cancelled on stop().
        # Kept as plain tasks rather than wrapping each in a Clock because
        # they're independent of the strategy heartbeat and have their own
        # cadences (30 s vs 30 min).
        self._balance_resync_task: asyncio.Task[None] | None = None
        self._volume_refresh_task: asyncio.Task[None] | None = None
        self._mm_universe_refresh_task: asyncio.Task[None] | None = None
        self._mm_universe_last_refresh_ts: float = 0.0
        self._mm_universe_last_adverse_refresh_ts: float = 0.0
        self._mm_universe_last_adverse_check_ts: float = 0.0
        self._mm_universe_spread_baselines: dict[str, float] = {}
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
        self._startup: StartupProgress | None = None
        self._book_resync: StartupProgress | None = None
        self._last_logged_status: str | None = None
        self._last_ops_health_signature: tuple[object, ...] | None = None

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
    def startup_progress(self) -> StartupProgress | None:
        return self._startup

    @property
    def book_resync_progress(self) -> StartupProgress | None:
        return self._book_resync

    @property
    def settings(self) -> Settings:
        return self._settings

    def attach_market_capturer(self, capturer) -> None:
        """Wire live 1m bar capture (flush on stop via ``stop()``)."""
        self._market_capturer = capturer
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._capture_flush_task is None or self._capture_flush_task.done():
            self._capture_flush_task = loop.create_task(
                self._capture_flush_loop(),
                name="engine-market-capture-flush",
            )

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

    def _strategy_symbol_candidates(self) -> set[str]:
        """Symbols required by the active mode (single strategy or ``all`` netting)."""

        if self.is_multi_strategy_mode():
            wanted: set[str] = set()
            for strat in self._strategies:
                wanted.update(strat.symbols())
            return wanted
        active = self._strategies_by_name.get(self._active_strategy_name)
        if active is not None:
            return set(active.symbols())
        return {str(s).strip().upper() for s in (self._settings.symbols or []) if str(s).strip()}

    def _resolve_market_symbols(self) -> list[str]:
        """Venue symbols for market-data WS: strategy universe + open positions."""

        wanted = self._strategy_symbol_candidates()
        positions = getattr(self, "_positions", None)
        if positions is not None:
            for pos in positions.all():
                if abs(pos.qty) > 1e-12:
                    wanted.add(pos.symbol.upper())
        raw = sorted(wanted) if wanted else list(self._settings.symbols)
        return _venue_symbol_list(raw, list(self._settings.symbols))

    async def refresh_market_universe(self) -> bool:
        """Resubscribe market WS when the active strategy's symbol set changes.

        Called after a dashboard hot-swap while the engine is RUNNING so we do
        not keep 400+ idle streams from strategies that are no longer active.
        """

        if not self._md_bootstrap_done:
            return False
        if self._state.status is not EngineStatus.RUNNING:
            return False
        new_syms = self._resolve_market_symbols()
        if new_syms == self._symbols:
            return False
        old_set = set(self._symbols)
        new_set = set(new_syms)
        added = sorted(new_set - old_set)
        removed = old_set - new_set
        self._symbols = new_syms
        if self._market_capturer is not None:
            self._market_capturer.refresh_symbols(new_syms)
        for sym in removed:
            self._latest_tick.pop(sym, None)
        logger.info(
            "market universe refresh: %d symbols (+%d -%d)",
            len(new_syms),
            len(added),
            len(removed),
        )
        await self._gateway.subscribe_market_data(
            symbols=self._symbols,
            on_tick=self._on_tick,
            on_depth=self._on_depth,
            on_trade=self._on_trade,
            on_quote_volume_24h=self._on_quote_volume_24h,
            on_reconnect=self._on_market_ws_reconnect,
        )
        await self._resync_symbol_books(
            added if added else list(new_syms),
            reason="strategy_swap",
            invalidate=True,
        )
        await self._prime_symbol_prices()
        return True

    def set_active_strategy(self, name: str) -> bool:
        """Hot-swap the active strategy or enable multi-strategy netting.

        ``name`` must be a registered ``strategy.name`` or ``"all"`` to run
        every strategy with internal position netting. Raises ``ValueError``
        for unknown names so the API layer can surface a 400 to the dashboard.

        Returns True if the active name actually changed.
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
            return False
        previous = self._active_strategy_name
        self._active_strategy_name = normalised
        self._stop_monitor.set_externally_managed(self._compute_externally_managed())
        sym_n = len(self._resolve_market_symbols())
        if normalised == ALL_STRATEGIES_MODE:
            logger.info(
                "strategy hot-swap: %s -> all (netted, %d market symbols)",
                previous or "<none>",
                sym_n,
            )
        else:
            logger.info(
                "strategy hot-swap: %s -> %s (%d market symbols)",
                previous or "<none>",
                normalised,
                sym_n,
            )
        return True

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
        if clean:
            logger.info("settings patched: %s", ", ".join(sorted(clean.keys())))
        self._apply_runtime_settings(new_settings)
        return new_settings

    def _apply_runtime_settings(self, s: Settings) -> None:
        prev_leverage = self._settings.leverage
        self._settings = s
        if s.leverage != prev_leverage:
            self._leverage_applied_symbols.clear()
        invalidate_cache()
        self._wheel.apply_settings(s)
        self._risk.apply_settings(s)
        self._stop_monitor.replace_limits(Limits.from_settings(s))
        self._features.apply_settings(s)
        self._micro.apply_settings(s)
        self._tape.set_window_sec(
            s.trade_tape_window_sec,
            large_trade_mult=s.mm_large_trade_mult,
        )
        self._quote_executor.apply_settings(s)
        self._mm_flow.apply_settings(s)
        self._own_book.set_markout_cooldown_sec(s.mm_markout_cooldown_sec)
        self._executor.apply_settings(s)
        self._stop_monitor.set_externally_managed(self._compute_externally_managed())
        self._submit_guard.apply_settings(s)
        self._slippage_guard.set_cooldown_sec(s.breaker_minor_cooldown_sec)
        self._loss_tracker.apply_settings(s)
        self._exec_quality_guard.apply_settings(s)
        self._connection_monitor.apply_settings(s)
        self._book_snapshot_sem = asyncio.Semaphore(
            max(1, int(getattr(s, "book_resync_concurrency", 8))),
        )
        self._reconciler.apply_settings(s)
        self._pretrade.apply_settings(s)
        self._order_reconciler.apply_settings(s)
        for strat in self._strategies:
            strat.refresh_settings(s)
        apply_log_level(resolve_log_level(s.log_level))
        logger.info("log level applied: %s", s.log_level.lower())

    def _warn_multi_strategy_symbol_overlap(self) -> None:
        """Log when STRATEGY=all runs multiple alpha strategies on the same symbol."""
        if not self.is_multi_strategy_mode():
            return
        by_symbol: dict[str, list[str]] = defaultdict(list)
        for strat in self._strategies:
            if mm_core.is_mm_strategy(strat.name):
                continue
            for sym in strat.symbols():
                by_symbol[sym.upper()].append(strat.name)
        overlaps = {sym: names for sym, names in by_symbol.items() if len(names) > 1}
        if overlaps:
            logger.warning(
                "STRATEGY=all: %d symbol(s) subscribed by multiple alpha strategies — "
                "signal_netter may cancel opposing intents: %s",
                len(overlaps),
                overlaps,
            )

    def _wire_mm_strategies(self) -> None:
        def own_provider(sym: str) -> OwnBookState:
            sym_u = sym.upper()
            children = [
                c for c in self._oms.working_children() if c.symbol.upper() == sym_u
            ]
            return self._own_book.sync_working(sym_u, list(children))

        for strat in self._strategies:
            if isinstance(strat, MarketMakingStrategy | MarketMakingV2Strategy):
                strat.attach_position_provider(
                    lambda s, pos=self._positions: (
                        pos.get(s).qty if pos.get(s) is not None else 0.0
                    ),
                )
                strat.attach_own_book_provider(own_provider)

    def _sync_own_book(self, symbol: str) -> OwnBookState:
        sym = symbol.upper()
        children = [c for c in self._oms.working_children() if c.symbol.upper() == sym]
        return self._own_book.sync_working(sym, list(children))

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
    def event_archive_dir(self) -> Path | None:
        """Timestamped run folder under ``persist_dir`` when journaling/recording is on."""

        return self._event_archive_dir

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
        gw_s, gl_s = self._performance.gross_pnls_session()
        profit_factor_s = (gw_s / gl_s) if gl_s > 0 else None
        return EngineSnapshot(
            state=self._state,
            position_tracker=self._positions,
            portfolio=self._portfolio,
            trades=self._performance.trades(),
            realized_trades=self._performance.realized_transactions(),
            win_rate=self._performance.win_rate(),
            gross_win_pnl=gross_win,
            gross_loss_pnl=gross_loss,
            profit_factor=profit_factor,
            win_rate_session=self._performance.win_rate_session(),
            gross_win_pnl_session=gw_s,
            gross_loss_pnl_session=gl_s,
            profit_factor_session=profit_factor_s,
            session_close_wins=self._performance.session_wins,
            session_close_losses=self._performance.session_losses,
            session_close_breakevens=self._performance.session_breakevens,
        )

    # --- Lifecycle ---

    async def start(self) -> None:
        async with self._start_lock:
            if self._state.status in (EngineStatus.RUNNING, EngineStatus.STARTING):
                return
            logger.info("engine starting")
            self._state.status = EngineStatus.STARTING
            self._book_resync = None
            await self._set_startup("connect", "Connecting to venue…")
            try:
                await self._start_impl()
            except Exception:
                logger.exception("engine start failed")
                self._state.status = EngineStatus.STOPPED
                self._startup = None
                await self._publish_status()
                await self._abort_start()
                raise

    async def _start_impl(self) -> None:
        await self._gateway.connect()
        await self._set_startup("clock", "Syncing exchange clock…")
        await self._refresh_clock_skew()

        if self._settings.recover_on_start and self._recovery_wal is not None:
            await self._set_startup("replay", "Replaying journal…")
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
            logger.info(
                "journal replay: events=%d fills=%d orders=%d positions=%d open_children=%d errors=%s",
                summary.events_read,
                summary.fills_applied,
                summary.orders_restored,
                summary.positions_seeded,
                summary.open_children,
                summary.errors or "none",
            )

        # REST snapshots before WS: a single pass keeps lastUpdateId fresh; avoid
        # subscribing first (18s of parallel snapshots would stale early symbols).
        await self._bootstrap_order_books()

        await self._set_startup("market_ws", "Subscribing to market data…")
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

        await self._set_startup("prime", "Priming symbol prices…")
        # Prime mids from WS where possible; REST ``/depth`` only for stragglers.
        await self._prime_symbol_prices()

        await self._set_startup("portfolio", "Loading balances and positions…")
        # Seed cash + positions from REST once; live updates use user-data WS.
        balances, positions = await self._gateway.fetch_balances_and_positions()
        self._portfolio.seed_balances(balances)
        self._positions.seed(positions)
        self._oms.touch_venue_truth_from_rest()

        if getattr(self._settings, "order_reconcile_on_startup", True):
            await self._set_startup("orders", "Reconciling open orders…")
            await self._order_reconciler.sync_startup()

        await self._set_startup("user_ws", "Connecting user data stream…")
        subscribe_kw: dict[str, Any] = {
            "on_fill": self._on_fill,
            "on_order_update": self._on_order_update,
            "on_account_update": self._on_account_update,
        }
        if self._settings.venue == "binance":
            subscribe_kw["on_ws_connected"] = self._on_user_ws_connected
        await self._gateway.subscribe_user_data(**subscribe_kw)

        await self._set_startup("volumes", "Loading 24h volume weights…")
        # Initial volume snapshot so liquidity-weighted strategies can size
        # their reference at the first tick rather than after the 30 min
        # refresh window. Best-effort; an empty cache falls back to equal
        # weights in the consuming strategy.
        await self._refresh_volume_weights()

        self._startup = None
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
        if self._mm_universe_refresh_enabled():
            self._mm_universe_spread_baselines = self._load_mm_universe_spread_baselines()
            self._mm_universe_last_refresh_ts = time.time()
            refresh_sec = float(self._settings.mm_universe_refresh_sec)
            if refresh_sec > 0:
                self._mm_universe_refresh_task = asyncio.create_task(
                    self._mm_universe_refresh_loop(),
                    name="engine-mm-universe-refresh",
                )
        # Start the periodic venue reconciliation loop so OMS/Portfolio
        # drift from a missed user-data event is caught within one cycle.
        self._reconciler.start()
        self._order_reconciler.start()
        self._alert_task = asyncio.create_task(self._alert_pump(), name="engine-alerts")
        logger.info("engine running")

    async def _abort_start(self) -> None:
        """Tear down partial wiring after ``start()`` fails mid-flight."""
        logger.warning("engine start failed; disconnecting gateway")
        self._md_bootstrap_done = False
        await self._clock.stop()
        await self._cancel_refresh_loops()
        try:
            await self._gateway.disconnect()
        except Exception:  # noqa: BLE001
            logger.exception("gateway disconnect failed during start abort")

    async def _bootstrap_order_books(self) -> None:
        """REST snapshot every symbol's L2 book before the depth stream is applied."""
        await self._resync_symbol_books(
            self._symbols, reason="startup", invalidate=False, publish_startup=True,
        )

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
        await self._cancel_mm_quotes()
        self._state.status = EngineStatus.PAUSED
        await self._publish_status()
        logger.warning("engine paused")

    async def resume(self) -> None:
        if self._state.status is not EngineStatus.PAUSED:
            return
        await self.sync_trading_book_from_rest()
        self._state.status = EngineStatus.RUNNING
        await self._publish_status()
        logger.info("engine resumed")

    async def sync_trading_book_from_rest(self) -> None:
        """Pull wallet, positions, and open orders from the venue over REST.

        Used when **resuming** after pause or after a **strategy hot-swap** so
        the dashboard and OMS match Binance before signals flow again. Cold
        ``start()`` already seeds + ``OrderReconciler.sync_startup`` — this
        path targets mid-session operator actions without a full reconnect.
        """
        if self._state.status is EngineStatus.STOPPED:
            return
        try:
            balances, positions = await self._gateway.fetch_balances_and_positions()
        except Exception:  # noqa: BLE001
            logger.exception("sync_trading_book_from_rest: fetch_balances_and_positions failed")
            return
        if balances:
            self._portfolio.update_balances(balances)
        open_pos = await self._open_positions_from_rest_slice(positions)
        await self._positions.sync_from_venue(open_pos)
        self._oms.touch_venue_truth_from_rest()
        if getattr(self._settings, "order_reconcile_on_startup", True):
            try:
                await self._order_reconciler.sync_startup()
            except Exception:  # noqa: BLE001
                logger.exception("sync_trading_book_from_rest: order reconcile failed")
        logger.info(
            "trading book synced from REST (%d open position(s))",
            len(open_pos),
        )

    async def _open_positions_from_rest_slice(self, positions: list[Position]) -> list[Position]:
        """Non-zero legs from an account snapshot, with ``positionRisk`` fallback."""
        open_pos = [p for p in positions if abs(p.qty) > 1e-12]
        if not open_pos:
            local_open = [p for p in self._positions.all() if abs(p.qty) > 1e-12]
            if local_open:
                try:
                    alt = await self._gateway.fetch_positions()
                    open_pos = [p for p in alt if abs(p.qty) > 1e-12]
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "sync_trading_book: fallback fetch_positions failed "
                        "(account snapshot had no open legs but local book is not flat)",
                    )
        return open_pos

    async def stop(self, *, force_flatten: bool = False) -> None:
        if self._state.status is EngineStatus.STOPPED:
            return
        logger.error("engine stopping (operator request)")
        # Optionally market-out residual positions before tearing down
        # connections, so a stop never leaves naked exposure on the
        # venue. Skipped on PAPER smoke tests by setting FLATTEN_ON_STOP=false.
        # ``force_flatten`` (dashboard Kill) always unwinds regardless of that flag.
        if force_flatten or getattr(self._settings, "flatten_on_stop", True):
            try:
                await self._flatten_and_wait_for_flat()
            except Exception:  # noqa: BLE001
                logger.exception("flatten_on_stop failed")
        if self._market_capturer is not None:
            self._market_capturer.flush()
        await self._clock.stop()
        if self._alert_task is not None:
            self._alert_task.cancel()
            try:
                await self._alert_task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                logger.exception("alert task shutdown raised")
            self._alert_task = None
        await self._reconciler.stop()
        await self._order_reconciler.stop()
        await self._cancel_refresh_loops()
        await self._cancel_reconnect_resync_task()
        await self._cancel_background_task("_gap_resync_task")
        await self._cancel_background_task("_capture_flush_task")
        await self._router.shutdown()
        try:
            await self._gateway.cancel_all_open_orders()
        except Exception:  # noqa: BLE001
            logger.exception("venue cancel_all_open_orders failed during stop")
        await self._oms.cancel_all()
        await self._gateway.disconnect()
        self._state.status = EngineStatus.STOPPED
        await self._publish_status()
        logger.info("engine stopped")

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
            if was_running and self._state.status is EngineStatus.PAUSED:
                await self.resume()

    async def _cancel_refresh_loops(self) -> None:
        """Cancel + await the background resync tasks spawned in start().

        Safe to call when the tasks were never created (engine was
        stopped before reaching RUNNING); each ``None`` slot is just
        skipped.
        """
        for slot_name in (
            "_balance_resync_task",
            "_volume_refresh_task",
            "_mm_universe_refresh_task",
        ):
            task: asyncio.Task[None] | None = getattr(self, slot_name)
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                logger.exception("%s shutdown raised", slot_name)
            setattr(self, slot_name, None)

    async def _cancel_background_task(self, slot_name: str) -> None:
        task: asyncio.Task[None] | None = getattr(self, slot_name, None)
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            logger.exception("%s shutdown raised", slot_name)
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

    def _mm_universe_refresh_enabled(self) -> bool:
        return bool(
            self._settings.mm_universe_auto or self._settings.mm2_universe_auto,
        )

    def _active_mm_symbols(self) -> list[str]:
        syms: set[str] = set()
        if self.is_multi_strategy_mode():
            targets = self._strategies
        else:
            active = self._strategies_by_name.get(self._active_strategy_name)
            targets = [active] if active is not None else []
        for strat in targets:
            if strat is None or not mm_core.is_mm_strategy(strat.name):
                continue
            syms.update(strat.symbols())
        return sorted(syms)

    def _load_mm_universe_spread_baselines(self) -> dict[str, float]:
        try:
            from analytics.mm_universe_refresher import load_spread_baselines

            return load_spread_baselines()
        except Exception:  # noqa: BLE001
            logger.debug("mm universe spread baselines unavailable", exc_info=True)
            return {}

    async def _mm_universe_refresh_loop(self) -> None:
        """Periodic full MM universe rescan when ``MM_SYMBOLS`` was AUTO at boot."""
        while True:
            try:
                interval = max(60.0, float(self._settings.mm_universe_refresh_sec))
                await asyncio.sleep(interval)
                await self._refresh_mm_universe(reason="periodic")
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("mm universe periodic refresh failed")

    async def _maybe_refresh_mm_universe_adverse(self) -> None:
        if not self._mm_universe_refresh_enabled():
            return
        now = time.time()
        check_sec = max(5.0, float(self._settings.mm_universe_adverse_check_sec))
        if now - self._mm_universe_last_adverse_check_ts < check_sec:
            return
        self._mm_universe_last_adverse_check_ts = now

        from analytics.mm_universe_refresher import (
            SymbolMicroSnapshot,
            evaluate_adverse_universe,
            should_run_adverse_refresh,
        )

        if not should_run_adverse_refresh(
            last_adverse_refresh_ts=self._mm_universe_last_adverse_refresh_ts,
            cooldown_sec=float(self._settings.mm_universe_adverse_refresh_cooldown_sec),
            now=now,
        ):
            return

        mm_syms = self._active_mm_symbols()
        if not mm_syms:
            return

        snaps: dict[str, SymbolMicroSnapshot] = {}
        for sym in mm_syms:
            own = self._sync_own_book(sym)
            pos = self._positions.get(sym)
            pos_qty = pos.qty if pos is not None else 0.0
            feat = self._features.snapshot(sym, own=own, position_qty=pos_qty)
            snaps[sym] = SymbolMicroSnapshot(
                markout_adverse_ewma_bps=feat.markout_adverse_ewma_bps,
                is_toxic=feat.is_toxic,
                jump_active=feat.jump_active,
                spread_bps=feat.spread_bps,
                vol_ewma_bps=feat.vol_ewma_bps,
                mid_return_1s_bps=feat.mid_return_1s_bps,
            )

        signal = evaluate_adverse_universe(
            mm_syms,
            snaps,
            settings=self._settings,
            spread_baselines=self._mm_universe_spread_baselines,
        )
        if signal is None:
            return
        logger.warning(
            "mm universe adverse signal: %s — %s (%s)",
            signal.reason,
            signal.detail,
            ", ".join(signal.symbols[:8]),
        )
        await self._refresh_mm_universe(reason=signal.reason)

    async def _refresh_mm_universe(self, *, reason: str) -> bool:
        if not self._mm_universe_refresh_enabled():
            return False
        if self._state.status is not EngineStatus.RUNNING:
            return False

        from analytics.mm_universe_scanner import resolve_mm_universe
        from gateways.binance.rest_client import BinanceRestClient

        rest = getattr(self._gateway, "_rest", None)
        own_rest = rest is None
        if own_rest:
            rest = BinanceRestClient(
                base_url=self._settings.binance_rest_base,
                api_key=self._settings.binance_api_key,
                api_secret=self._settings.binance_api_secret,
            )
        try:
            symbols = await resolve_mm_universe(
                self._settings,
                rest=rest,
                force_rescan=True,
            )
        except Exception:  # noqa: BLE001
            logger.exception("mm universe refresh failed (%s)", reason)
            return False
        finally:
            if own_rest and rest is not None:
                await rest.close()

        if not symbols:
            return False

        patch: dict[str, Any] = {}
        if self._settings.mm_universe_auto:
            patch["mm_symbols"] = symbols
        if self._settings.mm2_universe_auto:
            patch["mm2_symbols"] = symbols
        if not patch:
            return False

        prev = set(self._active_mm_symbols())
        self.apply_settings_patch(patch)
        self._mm_universe_spread_baselines = self._load_mm_universe_spread_baselines()
        now = time.time()
        self._mm_universe_last_refresh_ts = now
        if reason != "periodic":
            self._mm_universe_last_adverse_refresh_ts = now

        changed = await self.refresh_market_universe()
        new_set = set(symbols)
        logger.info(
            "mm universe refresh (%s): %d symbols %s -> %s market_ws_changed=%s",
            reason,
            len(symbols),
            sorted(prev)[:8],
            symbols[:12],
            changed,
        )
        return changed or prev != new_set

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

    async def _cancel_mm_quotes(self) -> None:
        syms: list[str] = []
        for strat in self._strategies:
            if mm_core.is_mm_strategy(strat.name):
                syms.extend(strat.symbols())
        if syms:
            await self._quote_executor.cancel_all(syms)

    async def flatten(self) -> None:
        """Cancel working orders + close all venue positions (market or VWAP)."""
        await self._cancel_mm_quotes()
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
        mid = tick.mid
        if mid > 0:
            st = self._own_book.state(tick.symbol)
            self._micro.on_mid(
                tick.symbol,
                mid,
                recv_ts,
                own_bid_qty=st.own_bid_qty,
                own_ask_qty=st.own_ask_qty,
            )
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
            sym = diff.symbol
            if sym in self._bulk_resync_symbols:
                return
            if gap > 0:
                self._md_quality.record_gap(sym, gap)
            if sym not in self._resnapshot_inflight:
                self._gap_resync_pending.add(sym)
                self._schedule_gap_resync()
            return
        if action is DiffAction.DROP_STALE:
            return
        if action is DiffAction.RESNAPSHOT:
            return
        book.apply_diff(diff)
        self._md_quality.on_applied(
            diff, best_bid=book.best_bid(), best_ask=book.best_ask(),
        )
        self._touch_symbol_from_book(diff.symbol, book)
        mid = book.mid()
        if mid is not None and mid > 0:
            st = self._own_book.state(diff.symbol)
            self._micro.on_mid(
                diff.symbol,
                mid,
                time.time(),
                own_bid_qty=st.own_bid_qty,
                own_ask_qty=st.own_ask_qty,
            )

    def _schedule_gap_resync(self) -> None:
        task = self._gap_resync_task
        if task is not None and not task.done():
            return
        self._gap_resync_task = asyncio.create_task(
            self._drain_gap_resync(),
            name="engine-gap-resync",
        )

    async def _drain_gap_resync(self) -> None:
        await asyncio.sleep(0)
        async with self._gap_resync_lock:
            if not self._gap_resync_pending:
                return
            batch = sorted(self._gap_resync_pending)
            self._gap_resync_pending.clear()
        concurrency = self._book_resync_concurrency("gap")

        async def _one(symbol: str) -> None:
            if symbol in self._bulk_resync_symbols:
                return
            self._resnapshot_inflight.add(symbol)
            try:
                await self._snapshot_book(symbol)
            finally:
                self._resnapshot_inflight.discard(symbol)

        await self._run_book_resync_workers(
            batch,
            concurrency=concurrency,
            worker=_one,
        )

    async def _capture_flush_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(1.0)
                capturer = self._market_capturer
                if capturer is not None and capturer.flush_requested():
                    await asyncio.to_thread(capturer.flush)
        except asyncio.CancelledError:
            capturer = self._market_capturer
            if capturer is not None:
                await asyncio.to_thread(capturer.flush)
            raise

    async def _on_market_ws_reconnect(self, symbols: list[str]) -> None:
        """REST-resync L2 books after a public market WebSocket reconnect.

        Debounce + coalesce shard reconnects so parallel snapshot storms do not
        starve market WS ping handlers.
        """
        self._reconnect_resync_pending.update(s.upper() for s in symbols)
        task = self._reconnect_resync_debounce_task
        if task is not None and not task.done():
            return
        self._reconnect_resync_debounce_task = asyncio.create_task(
            self._flush_reconnect_resync(),
            name="engine-market-ws-reconnect-resync",
        )

    async def _flush_reconnect_resync(self) -> None:
        delay = max(
            0.0,
            float(getattr(self._settings, "market_ws_reconnect_resync_delay_sec", 3.0)),
        )
        if delay > 0:
            await asyncio.sleep(delay)
        async with self._reconnect_resync_lock:
            while self._reconnect_resync_pending:
                batch = sorted(self._reconnect_resync_pending)
                self._reconnect_resync_pending.clear()
                await self._resync_symbol_books(batch, reason="reconnect")

    def _book_resync_concurrency(self, reason: str) -> int:
        if reason == "reconnect":
            return max(
                1,
                int(getattr(self._settings, "book_resync_reconnect_concurrency", 3)),
            )
        return max(1, int(getattr(self._settings, "book_resync_concurrency", 8)))

    async def _run_book_resync_workers(
        self,
        symbols: list[str],
        *,
        concurrency: int,
        worker: Callable[[str], Awaitable[None]],
    ) -> int:
        """Run ``worker(symbol)`` with a fixed pool size (no N-task gather storms)."""
        if not symbols:
            return 0
        limit = min(max(1, concurrency), len(symbols))
        sym_iter = iter(symbols)
        iter_lock = asyncio.Lock()
        failures = 0
        fail_lock = asyncio.Lock()

        async def _runner() -> None:
            nonlocal failures
            while True:
                async with iter_lock:
                    try:
                        sym = next(sym_iter)
                    except StopIteration:
                        return
                try:
                    await worker(sym)
                except Exception:  # noqa: BLE001
                    logger.exception("book resync worker failed for %s", sym)
                    async with fail_lock:
                        failures += 1
                await asyncio.sleep(0)

        await asyncio.gather(*(_runner() for _ in range(limit)))
        return failures

    async def _cancel_reconnect_resync_task(self) -> None:
        task = self._reconnect_resync_debounce_task
        if task is None:
            return
        self._reconnect_resync_debounce_task = None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            logger.exception("reconnect resync task shutdown raised")

    async def _resync_symbol_books(
        self,
        symbols: list[str],
        *,
        reason: str,
        invalidate: bool = True,
        publish_startup: bool = False,
    ) -> None:
        if not symbols:
            return
        logger.info("%s: resyncing %d symbol L2 book(s)", reason, len(symbols))
        normalized = [s.upper() for s in symbols]
        if invalidate:
            self._md_quality.invalidate(normalized)
            for sym in normalized:
                self._books.get(sym).invalidate()

        self._bulk_resync_symbols.update(normalized)
        total = len(normalized)
        done = 0
        done_lock = asyncio.Lock()
        show_progress = publish_startup or self._state.status is EngineStatus.RUNNING

        if show_progress and reason != "startup":
            self._book_resync = StartupProgress(
                phase="books",
                label="Resyncing L2 order books after reconnect…",
                done=0,
                total=total,
            )
            logger.info("book resync started (%s): %d symbols", reason, total)
            await self._publish_book_resync()

        async def _one(symbol: str) -> None:
            nonlocal done
            async with done_lock:
                in_flight = done
            if publish_startup:
                await self._set_startup(
                    "books",
                    "Syncing L2 order books…",
                    done=in_flight,
                    total=total,
                    symbol=symbol,
                )
            await self._snapshot_book(symbol)
            await asyncio.sleep(0)
            async with done_lock:
                done += 1
                completed = done
            if publish_startup:
                await self._set_startup(
                    "books",
                    "Syncing L2 order books…",
                    done=completed,
                    total=total,
                    symbol=symbol,
                )
            elif self._book_resync is not None and (
                completed % 8 == 0 or completed == total
            ):
                await self._publish_book_resync(done=completed, total=total, symbol=symbol)

        try:
            failures = await self._run_book_resync_workers(
                normalized,
                concurrency=self._book_resync_concurrency(reason),
                worker=_one,
            )
            if failures:
                logger.warning(
                    "%s book resync: %d/%d snapshots failed",
                    reason,
                    failures,
                    len(normalized),
                )
        finally:
            self._bulk_resync_symbols.difference_update(normalized)

        if self._book_resync is not None and reason != "startup":
            self._book_resync = None
            await self._publish_book_resync(clear=True)

    async def _on_trade(self, trade: TapeTrade) -> None:
        self._state.last_tick_ts = time.time()
        self._micro.on_trade(trade)

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
        logger.info(
            "fill %s %s qty=%.8f @ %.8f parent=%s trade=%s",
            fill.symbol,
            fill.side.value,
            fill.qty,
            fill.price,
            fill.parent_id or "-",
            fill.trade_id or "-",
        )
        post_position = self._positions.get(fill.symbol)
        pre_position = post_position
        if self._positions_from_account_updates():
            pre_position = position_before_fill(
                post_position,
                fill,
                fallback_entry=self._positions.entry_before_flat(fill.symbol),
            )
        classification = classify_fill(pre_position, fill)
        exclude_from_streak = self._is_emergency_flatten_fill(fill)
        # Binance ACCOUNT_UPDATE already carries authoritative ``pa``; applying
        # the same ORDER_TRADE_UPDATE fill doubles qty when events arrive out
        # of order (ACCOUNT_UPDATE first — observed on CRVUSDC).
        if not self._positions_from_account_updates():
            await self._positions.on_fill(fill)
        position = self._positions.get(fill.symbol) or Position(symbol=fill.symbol)
        self._risk.on_fill(fill, position)
        record = self._performance.record_fill(
            fill,
            classification,
            exclude_from_streak=exclude_from_streak,
        )
        if classification.action == "close":
            self._positions.clear_entry_before_flat(fill.symbol)
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
        parent = self._oms.parent(fill.parent_id) if fill.parent_id else None
        if parent is not None and mm_core.is_mm_strategy(parent.strategy_name):
            await self._handle_mm_fill(fill, parent.strategy_name)

        self._notify_strategies_on_fill(fill)

    async def _handle_mm_fill(self, fill: Fill, strategy_name: str) -> None:
        sym = fill.symbol.upper()
        mid = self._mid_for(sym) or fill.price
        if mid <= 0:
            return
        self._micro.on_fill(sym, fill, mid, time.time())
        pos = self._positions.get(sym) or Position(symbol=sym)
        adverse = self._micro.last_fill_adverse_bps(sym)
        self._own_book.on_level_fill(sym, fill, position_qty=pos.qty, adverse_bps=adverse)
        strat = self._strategies_by_name.get(strategy_name)
        if strat is None or not hasattr(strat, "on_tick_quotes"):
            return
        equity = self._portfolio.snapshot().equity
        own = self._sync_own_book(sym)
        feat = self._features.snapshot(sym, own=own, position_qty=pos.qty, equity=equity)
        intents = list(strat.on_tick_quotes({sym: feat}))
        await self._quote_executor.refresh(intents)

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
        wallet_by_asset = update.get("wallet_by_asset") or {}
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

        # Drive the equity *curve and WS payloads* off live tick marks so the console
        # updates every second. When user-data is fresh we still omit mark-based unrealized in
        # ``Portfolio.snapshot()`` (default ``use_mark_pnl=False``); loss / HWM monitors use that
        # path and stay aligned with venue-reported unrealized between ACCOUNT_UPDATEs.
        await self._portfolio.mark_to_market(use_mark_pnl=True)
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

        await self._maybe_refresh_mm_universe_adverse()

        # Risk-driven exits first; an exit can't be vetoed by risk again
        # because it's already a closing trade.
        await self._latency.maybe_emit(self._settings.latency_metrics_interval_sec)
        stale = self._md_quality.tick_staleness(now=time.time())
        for sym in stale[:_MAX_MD_RESNAPSHOTS_PER_TICK]:
            await self._snapshot_book(sym)
        if self.is_multi_strategy_mode():
            self._refresh_ticks_from_books(self._symbols)
        else:
            active = self._strategies_by_name.get(self._active_strategy_name)
            if active is not None:
                self._refresh_ticks_from_books(active.symbols())
        if self._book_resync is not None:
            return
        if self._market_capturer is not None:
            self._market_capturer.on_clock()
        await self._evaluate_exits()
        await self._evaluate_strategies()

    async def _maybe_flatten_for_breaker(self) -> None:
        if not self._breaker.is_engine_halted():
            self._auto_flatten_in_progress = False
            self._latched_major_flatten_done = False
            return
        if self._auto_flatten_in_progress:
            return
        active = [
            s for s in self._breaker.active()
            if s.scope is BreakerScope.ENGINE and s.severity is BreakerSeverity.MAJOR
        ]
        if not active:
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
            if getattr(self._settings, "auto_rearm_consecutive_losses_after_flatten", True):
                if any(s.code == "consecutive_losses" for s in active):
                    if self._breaker.rearm(code="consecutive_losses"):
                        self.apply_breaker_rearm_side_effects({"consecutive_losses"})
                        self._latched_major_flatten_done = False
                        logger.info(
                            "consecutive_losses cleared after auto-flatten; trading may resume",
                        )

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
        if mm_core.is_mm_strategy(active.name):
            await self._evaluate_mm_quotes(active)
            return
        try:
            feats = {sym: self._features.snapshot(sym) for sym in active.symbols()}
            signals = list(active.on_tick(feats))
        except Exception:  # noqa: BLE001
            logger.exception("strategy %s on_tick raised", active.name)
            return
        await self._dispatch_signals(signals)

    async def _evaluate_mm_quotes(self, strat: StrategyBase) -> None:
        if not self._settings.mm_quote_enabled:
            return
        equity = self._portfolio.snapshot().equity
        feats: dict[str, Any] = {}
        for sym in strat.symbols():
            own = self._sync_own_book(sym)
            pos = self._positions.get(sym)
            pos_qty = pos.qty if pos is not None else 0.0
            feat = self._features.snapshot(sym, own=own, position_qty=pos_qty, equity=equity)
            breach = self._mm_flow.evaluate_entry(feat, reduce_only=False)
            if breach is not None:
                self._breaker.trip(breach)
            feats[sym] = feat
        if not hasattr(strat, "on_tick_quotes"):
            return
        try:
            intents = list(strat.on_tick_quotes(feats))
        except Exception:  # noqa: BLE001
            logger.exception("strategy %s on_tick_quotes raised", strat.name)
            return
        for intent in intents:
            if intent.reservation_mid > 0 or intent.reason:
                signal_log_emit(
                    logger,
                    (
                        f"MM {intent.symbol} venue={intent.venue_mid:.4f} "
                        f"res={intent.reservation_mid:.4f} inv={intent.inventory_ratio:+.3f} "
                        f"bid={intent.bid_price} ask={intent.ask_price} "
                        f"pnl_bps={intent.unrealized_pnl_bps:.1f}"
                    ),
                    reason=intent.reason,
                )
        await self._quote_executor.refresh(intents)

    async def _evaluate_all_strategies_netted(self) -> None:
        tagged: list[tuple[str, Signal]] = []
        symbol_union: set[str] = set()
        alpha_strategies: list[StrategyBase] = []
        for strat in self._strategies:
            if mm_core.is_mm_strategy(strat.name):
                await self._evaluate_mm_quotes(strat)
            else:
                alpha_strategies.append(strat)
                symbol_union.update(strat.symbols())
        feats_cache = {
            sym: self._features.snapshot(sym) for sym in symbol_union
        }
        for strat in alpha_strategies:
            try:
                feats = {sym: feats_cache[sym] for sym in strat.symbols() if sym in feats_cache}
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
        sn = signal.strategy_name or self._active_strategy_name
        if mm_core.is_mm_strategy(sn):
            logger.error(
                "MM strategy %s must use QuoteExecutor, not VWAP router (signal ignored)",
                sn,
            )
            return None
        mid = self._mid_for(signal.symbol)
        if mid is None:
            signal_log_emit(
                logger,
                f"dispatch skipped {signal.symbol}: no mid price",
                reason=signal.reason,
            )
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
            signal_log_emit(
                logger,
                f"parent {parent.id} submitted {signal.side.value} {signal.symbol} "
                f"qty={result.qty:.8f}",
                reason=signal.reason,
            )
            if return_parent:
                return parent
        except ParentSubmissionRejected as exc:
            signal_log_emit(
                logger,
                f"router gated {signal.symbol}: {exc}",
                reason=signal.reason,
            )
        except Exception:  # noqa: BLE001
            logger.exception("dispatch failed for %s", signal.symbol)
            signal_log_emit(
                logger,
                f"dispatch error {signal.symbol}",
                reason=signal.reason,
            )
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
                group_signal_log(logger, group_id, f"aborted: no mid for {leg.symbol}", legs)
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
                group_signal_log(logger, group_id, f"aborted: venue vetoed {leg.symbol}", legs)
                return
            floors[leg.symbol] = floor

        strategy_qty = max((leg.qty for leg in legs if leg.qty > 0), default=0.0)
        pair_qty = max(strategy_qty, max(floors.values()))
        group_reduce_only = all(leg.reduce_only for leg in legs)
        if group_reduce_only:
            for leg in legs:
                pos = self._positions.get(leg.symbol)
                if pos is not None and abs(pos.qty) > 1e-12:
                    pair_qty = min(pair_qty, abs(pos.qty))
            if pair_qty <= 0:
                group_signal_log(logger, group_id, "aborted: no position to close", legs)
                return
        else:
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
                    group_signal_log(
                        logger,
                        group_id,
                        f"aborted: {decision.reason} for {leg.symbol}",
                        legs,
                    )
                    return
                min_allowed = min(min_allowed, decision.qty)
            pair_qty = min_allowed
        for leg in legs:
            filt = self._gateway.get_symbol_filters(leg.symbol)
            pair_qty = min(pair_qty, venue_cap_qty(pair_qty, filt))
        if pair_qty <= 0:
            group_signal_log(logger, group_id, "aborted: venue max_qty caps pair to zero", legs)
            return
        for leg in legs:
            filt = self._gateway.get_symbol_filters(leg.symbol)
            mid = mids[leg.symbol]
            if not venue_qty_in_bounds(
                pair_qty, filt, mid, reduce_only=group_reduce_only,
            ):
                group_signal_log(
                    logger,
                    group_id,
                    f"aborted: {leg.symbol} qty={pair_qty:.8f} fails venue bounds",
                    legs,
                )
                return
        result = self._pretrade.validate_group(
            legs,
            pair_qty,
            mids,
            tick_ts_by_symbol=tick_ts,
            spread_bps_by_symbol=spread_bps,
        )
        if not result.approved:
            group_signal_log(logger, group_id, f"pretrade veto: {result.reason}", legs)
            return

        for leg in legs:
            allowed, reason = self._submit_guard.can_submit_parent(leg.symbol)
            if not allowed:
                group_signal_log(logger, group_id, f"aborted: {reason} for {leg.symbol}", legs)
                return

        group_signal_log(
            logger,
            group_id,
            f"submitting {len(legs)} legs at pair_qty={pair_qty:.8f}",
            legs,
        )
        submitted: list[tuple[Signal, ParentOrder]] = []
        for leg in legs:
            try:
                if not group_reduce_only:
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
                    reduce_only=group_reduce_only,
                )
                self._latency.on_child_submitted(leg.symbol)
                submitted.append((leg, parent))
                signal_log_emit(
                    logger,
                    f"group {group_id} parent {parent.id} {leg.side.value} "
                    f"{leg.symbol} qty={pair_qty:.8f}",
                    reason=leg.reason,
                )
                if leg.strategy_name and self.is_multi_strategy_mode():
                    sym = leg.symbol.upper()
                    delta = pair_qty if leg.side is Side.BUY else -pair_qty
                    self._parent_attribution.setdefault(parent.id, {}).setdefault(
                        leg.strategy_name, {},
                    )[sym] = delta
            except ParentSubmissionRejected as exc:
                await self._compensate_group_submission(group_id, submitted, exc)
                return
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "group %s leg %s submit raised",
                    group_id,
                    leg.symbol,
                )
                await self._compensate_group_submission(
                    group_id,
                    submitted,
                    ParentSubmissionRejected(str(exc)),
                )
                return

    async def _compensate_group_submission(
        self,
        group_id: str,
        submitted: list[tuple[Signal, ParentOrder]],
        exc: ParentSubmissionRejected,
    ) -> None:
        legs = [leg for leg, _parent in submitted]
        signal_log_emit(
            logger,
            f"group {group_id} partial failure after {len(submitted)} leg(s): {exc} "
            "— compensating unwind",
            reason=" | ".join(f"{leg.symbol}:{leg.reason}" for leg in legs[:4]),
        )
        logger.error(
            "group %s partial failure after %d leg(s): %s — unwinding",
            group_id, len(submitted), exc,
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
            book = self._books.get(symbol)
            book.apply_snapshot(
                bids=[(float(p), float(q)) for p, q in data.get("bids", [])],
                asks=[(float(p), float(q)) for p, q in data.get("asks", [])],
                last_update_id=last_id,
            )
            self._md_quality.on_snapshot(symbol, last_id)
            self._touch_symbol_from_book(symbol, book)

    def _mid_for(self, symbol: str) -> float | None:
        tick = self._latest_tick.get(symbol)
        if tick is not None:
            return tick.mid
        book = self._books.get(symbol)
        return book.mid()

    def _top_of_book_for(self, symbol: str) -> float | None:
        return self._mid_for(symbol)

    def _startup_payload(self) -> dict[str, object] | None:
        if self._startup is None:
            return None
        sp = self._startup
        return {
            "phase": sp.phase,
            "label": sp.label,
            "done": sp.done,
            "total": sp.total,
            "symbol": sp.symbol,
        }

    async def _set_startup(
        self,
        phase: str,
        label: str,
        *,
        done: int = 0,
        total: int = 0,
        symbol: str | None = None,
    ) -> None:
        self._startup = StartupProgress(
            phase=phase, label=label, done=done, total=total, symbol=symbol,
        )
        await self._publish_status()

    async def _publish_book_resync(
        self,
        *,
        done: int | None = None,
        total: int | None = None,
        symbol: str | None = None,
        clear: bool = False,
    ) -> None:
        if clear:
            logger.info("book resync complete")
            await self._bus.publish(
                Event(type=EventType.STATUS, payload={"kind": "book_resync", "clear": True}),
            )
            return
        br = self._book_resync
        if br is None:
            return
        if done is not None:
            br.done = done
        if total is not None:
            br.total = total
        if symbol is not None:
            br.symbol = symbol
        await self._bus.publish(
            Event(
                type=EventType.STATUS,
                payload={
                    "kind": "book_resync",
                    "phase": br.phase,
                    "label": br.label,
                    "done": br.done,
                    "total": br.total,
                    "symbol": br.symbol,
                },
            ),
        )

    async def _publish_status(self) -> None:
        payload: dict[str, object] = {
            "status": self._state.status.value,
            "uptime_sec": self.snapshot().uptime_sec,
        }
        startup = self._startup_payload()
        if startup is not None:
            payload["startup"] = startup
        status_value = self._state.status.value
        if status_value != self._last_logged_status:
            logger.info("engine status -> %s", status_value)
            self._last_logged_status = status_value
        await self._bus.publish(Event(type=EventType.STATUS, payload=payload))

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
        # Align ops/REST ``system_health`` with the ~1Hz ``mark_to_market`` pulse
        # (``use_mark_pnl=True``) so open PnL and equity move with BBO marks instead
        # of freezing on the last ACCOUNT_UPDATE uPnL between venue pushes.
        snap = self._portfolio.snapshot(use_mark_pnl=True)
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
        user_health = self._user_data_health(now)
        active_breakers = tuple(sorted(s.code for s in self._breaker.active()))
        signature: tuple[object, ...] = (
            active_breakers,
            bool(user_health.get("user_data_stale")),
            bool(user_health.get("user_data_reconcile_stale")),
            round(tick_age) if tick_age >= 0 else -1,
        )
        if signature != self._last_ops_health_signature:
            portfolio = self._portfolio_health()
            logger.info(
                "system_health breakers=%s tick_age=%.1fs user_stale=%s "
                "reconcile_stale=%s equity=%.2f",
                active_breakers or "none",
                tick_age,
                user_health.get("user_data_stale"),
                user_health.get("user_data_reconcile_stale"),
                portfolio.get("equity", 0.0),
            )
            self._last_ops_health_signature = signature
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
                    **user_health,
                    **self._portfolio_health(),
                    "active_breakers": list(active_breakers),
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
        logger.warning(
            "order reconcile mismatch venue_only=%s local_only=%s",
            result.get("venue_only"),
            result.get("local_only"),
        )
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


