"""Pydantic DTOs that mirror the React console's TypeScript types.

The shape of every payload here is dictated by
`src/components/algo/types.ts` so the frontend can consume live data
without any client-side adaptation step.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PositionDTO(BaseModel):
    symbol: str
    side: Literal["long", "short", "flat"]
    size: float
    entry: float
    mark: float
    unrealized_pnl: float


class TradeDTO(BaseModel):
    id: str
    ts: str            # HH:MM:SS in user locale, formatted server-side
    symbol: str
    side: Literal["buy", "sell"]
    qty: float
    price: float
    action: Literal["open", "close"]
    entry_price: float | None
    exit_price: float | None
    pnl: float | None


class LogDTO(BaseModel):
    ts: str
    level: Literal["debug", "info", "warn", "error", "signal"]
    msg: str
    logger: str | None = None


class StartupProgressDTO(BaseModel):
    phase: str
    label: str
    done: int = 0
    total: int = 0
    symbol: str | None = None


class StatusDTO(BaseModel):
    status: Literal["running", "paused", "stopped", "starting"]
    uptime_sec: float
    # Mirrors `Settings.trading_mode`; the dashboard renders a PAPER badge
    # when this is true so the operator never confuses a paper run with live.
    paper_mode: bool = False
    startup: StartupProgressDTO | None = None


class StrategyInfoDTO(BaseModel):
    """Identity of a strategy registered with the engine.

    Surfaced on the initial `/api/state` hydrate so the dashboard can list
    every loaded strategy in its hot-swap toggle. ``active`` flags the one
    currently emitting signals; switching is done via
    ``POST /api/control/strategy``.
    """

    name: str
    label: str
    description: str
    active: bool = False


class EquityDTO(BaseModel):
    equity: list[float]
    last_ts: float


class KpiDTO(BaseModel):
    equity: float
    open_pnl: float
    win_rate: float
    gross_win_pnl: float
    gross_loss_pnl: float
    profit_factor: float | None = None
    realized_pnl: float
    unrealized_pnl: float
    gross_notional: float
    net_notional: float
    # Since engine process start (not reset by the rolling-200 window).
    win_rate_session: float = 0.0
    gross_win_pnl_session: float = 0.0
    gross_loss_pnl_session: float = 0.0
    profit_factor_session: float | None = None
    session_close_wins: int = 0
    session_close_losses: int = 0
    session_close_breakevens: int = 0


class ChildOrderDTO(BaseModel):
    """A single live or recent child order, for the OMS panel."""

    id: str
    parent_id: str | None
    symbol: str
    side: Literal["buy", "sell"]
    qty: float
    filled_qty: float
    price: float | None
    avg_fill_price: float
    order_type: Literal["limit", "market"]
    status: Literal[
        "new", "ack", "partial", "filled", "cancelled", "rejected", "expired",
    ]
    venue_order_id: str | None
    created_at: float
    updated_at: float


class ParentOrderDTO(BaseModel):
    """A working VWAP parent order with child progress + execution stats."""

    parent_id: str
    symbol: str
    side: Literal["buy", "sell"]
    requested_qty: float
    filled_qty: float
    fill_ratio: float
    arrival_price: float
    vwap_price: float
    slippage_bps: float
    fee_adjusted_slippage_bps: float = 0.0
    impact_bps: float
    duration_sec: float
    algo_mode: str | None
    notes: str = ""
    signal_score: float = 0.0
    strategy_name: str = ""
    started_at: float


class ExecutionReportDTO(BaseModel):
    """Completed parent order with full execution-quality breakdown."""

    parent_id: str
    symbol: str
    side: Literal["buy", "sell"]
    requested_qty: float
    filled_qty: float
    fill_ratio: float
    arrival_price: float
    vwap_price: float
    slippage_bps: float
    fee_adjusted_slippage_bps: float = 0.0
    impact_bps: float
    duration_sec: float
    algo_mode: str | None
    notes: str = ""
    signal_score: float = 0.0
    strategy_name: str = ""
    started_at: float
    completed_at: float | None


class ExecutionAggregateDTO(BaseModel):
    """Portfolio-wide execution-quality stats."""

    count: int
    avg_slippage_bps: float
    avg_impact_bps: float
    avg_fill_ratio: float
    avg_duration_sec: float
    total_traded_notional: float


class ExecutionStatsDTO(BaseModel):
    working: list[ParentOrderDTO]
    history: list[ExecutionReportDTO]
    aggregate: ExecutionAggregateDTO


class OrdersDTO(BaseModel):
    working: list[ChildOrderDTO]


class KlineDTO(BaseModel):
    """One OHLCV candle. Times are seconds since epoch (UTC)."""

    open_time: float
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: float


class SystemHealthDTO(BaseModel):
    latency: dict[str, dict[str, float]] = {}
    order_reconcile: dict[str, object] = {}
    md_health: dict[str, dict[str, float | int | bool]] = {}
    clock_skew_ms: float = 0.0
    tick_age_sec: float = -1.0
    user_data_age_sec: float = -1.0
    user_ws_event_age_sec: float = -1.0
    user_data_monitored: bool = False
    user_data_stale: bool = False
    user_data_reconcile_stale: bool = False
    clock_skew_synced: bool = False
    active_breakers: list[str] = []
    gross_notional: float = 0.0
    net_notional: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    equity: float = 0.0


class DailyReportDTO(BaseModel):
    run_dir: str
    trade_count: int = 0
    realized_pnl: float = 0.0
    avg_slippage_bps: float = 0.0
    breaker_events: int = 0
    reconcile_mismatches: int = 0
    notes: list[str] = []


class StateDTO(BaseModel):
    """Full snapshot used for the initial dashboard hydrate."""

    status: StatusDTO
    strategy: StrategyInfoDTO | None
    strategies: list[StrategyInfoDTO]
    kpi: KpiDTO
    equity: EquityDTO
    positions: list[PositionDTO]
    trades: list[TradeDTO]
    realized_trades: list[TradeDTO] = Field(default_factory=list)
    orders: OrdersDTO
    execution: ExecutionStatsDTO
    system_health: SystemHealthDTO | None = None
    event_archive_run_dir: str | None = None


class RiskUpdateDTO(BaseModel):
    max_risk_pct: float


class BreakerStatusDTO(BaseModel):
    """Live state of one circuit-breaker breach.

    Mirrors `engine.risk.circuit_breaker.BreakerStatus.to_dict()` so the
    React console can render the safety state without translation.
    """

    code: str
    scope: Literal["engine", "symbol", "parent"]
    severity: Literal["minor", "major"]
    target: str | None = None
    state: Literal["armed", "tripped", "cooldown", "latched"]
    tripped_at: float
    cooldown_until: float | None = None
    detail: str = ""


class BreakerListDTO(BaseModel):
    active: list[BreakerStatusDTO]
    history: list[BreakerStatusDTO]


class BreakerRearmDTO(BaseModel):
    """Operator re-arm payload.

    Both fields optional: omitting both clears every latched breach.
    """

    code: str | None = None
    target: str | None = None


class BreakerTripDTO(BaseModel):
    """Operator trading halt payload."""

    detail: str = ""
    flatten: bool = True
    pause: bool = True


class BacktestDatasetDTO(BaseModel):
    symbol: str
    interval: str
    source: Literal["live", "download", "mixed"]
    rows: int
    start: str | None = None
    end: str | None = None
    path: str
    run_ids: list[str] = Field(default_factory=list)
    updated_at: str = ""


class BacktestRunSessionDTO(BaseModel):
    run_id: str
    label: str


class BacktestDownloadRequestDTO(BaseModel):
    symbols: list[str]
    interval: str = "1m"
    days: int = 7


class BacktestRunRequestDTO(BaseModel):
    strategy: str
    dataset: str = "library"
    start: str | None = None
    end: str | None = None
    settings_overrides: dict[str, object] = Field(default_factory=dict)


class BacktestJobAcceptedDTO(BaseModel):
    job_id: str
    status: str = "pending"


class AnalyticsJobDTO(BaseModel):
    id: str
    type: str
    status: str
    progress: float = 0.0
    result: dict[str, object] | None = None
    error: str | None = None
    created_at: str = ""
    updated_at: str = ""


class BacktestMetricsDTO(BaseModel):
    total_return_pct: float
    max_drawdown_pct: float
    trade_count: int
    win_rate: float
    realized_pnl: float
    final_equity: float


class BacktestFillDTO(BaseModel):
    symbol: str
    side: str
    qty: float
    price: float
    ts: float
    reason: str
    pnl: float = 0.0
    action: str = "open"


class BacktestResultDTO(BaseModel):
    run_id: str
    strategy: str
    dataset: str
    bar_count: int
    symbols: list[str]
    metrics: BacktestMetricsDTO
    equity_curve: list[float]
    fills: list[BacktestFillDTO]
    notes: list[str] = Field(default_factory=list)


class BacktestResultSummaryDTO(BaseModel):
    run_id: str
    strategy: str
    dataset: str
    bar_count: int
    total_return_pct: float
    saved_at: str | None = None


class MmUniverseRankingDTO(BaseModel):
    symbol: str
    quote_volume_24h: float = 0.0
    last_price: float = 0.0
    median_spread_bps: float = 0.0
    spread_cv: float = 0.0
    mid_vol_bps: float = 0.0
    edge_bps: float = 0.0
    score: float = 0.0
    eligible: bool = False
    reject_reason: str | None = None


class MmUniverseScanReportDTO(BaseModel):
    generated_at: str
    recommended: list[str]
    candidates_scanned: int = 0
    sample_rounds: int = 0
    rankings: list[MmUniverseRankingDTO] = Field(default_factory=list)


class MmUniverseScanRequestDTO(BaseModel):
    sample: bool = True
    settings_overrides: dict[str, object] = Field(default_factory=dict)
