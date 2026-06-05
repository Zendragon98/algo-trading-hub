// REST + WebSocket client for the trading backend (FastAPI on :8000).
//
// All payload shapes are dictated by `backend/api/schemas.py` and mirror
// the types in `src/components/algo/types.ts`. Keep the two in sync —
// the dashboard binds directly to these shapes.

import type {
  AlgoStatus,
  ExecutionAggregate,
  ExecutionParent,
  Kline,
  LogEntry,
  Position,
  StrategyInfo,
  Trade,
  WorkingOrder,
} from "@/components/algo/types";

const RAW_BASE = (import.meta as { env?: Record<string, string | undefined> }).env?.VITE_API_BASE;
// In Vite dev, same-origin + `vite.config` proxy avoids CORS entirely (empty default).
const DEFAULT_BASE = import.meta.env.DEV ? "" : "http://127.0.0.1:8000";

function resolveApiBase(raw: string | undefined): string {
  let base = (raw ?? DEFAULT_BASE).replace(/\/$/, "");
  // Misconfigured VITE_API_BASE=http://… on an https dashboard triggers misleading CORS
  // errors (redirects omit ACAO). Upgrade remote hosts when the page is served over TLS.
  if (
    typeof window !== "undefined" &&
    window.location.protocol === "https:" &&
    base.startsWith("http://") &&
    !/^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?/.test(base)
  ) {
    base = `https://${base.slice("http://".length)}`;
  }
  return base;
}

export const API_BASE = resolveApiBase(RAW_BASE);

const API_TOKEN = (
  (import.meta as { env?: Record<string, string | undefined> }).env?.VITE_API_TOKEN ?? ""
).trim();

function authHeaders(): Record<string, string> {
  if (!API_TOKEN) return {};
  return { authorization: `Bearer ${API_TOKEN}` };
}

function isControlRoute(path: string): boolean {
  return path.startsWith("/api/control");
}

function httpToWsBase(httpOrEmpty: string): string {
  if (!httpOrEmpty) {
    if (typeof window === "undefined") {
      return "ws://127.0.0.1:8000";
    }
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${window.location.host}`;
  }
  return httpOrEmpty.replace(/^http/, "ws");
}

/** Event stream URL — call from the browser (useEffect); resolves relative to page when API_BASE is "". */
export function getAlgoWsUrl(): string {
  return `${httpToWsBase(API_BASE)}/ws`;
}

export type StartupProgressDTO = {
  phase: string;
  label: string;
  done: number;
  total: number;
  symbol: string | null;
};

export type StatusDTO = {
  status: AlgoStatus;
  uptime_sec: number;
  paper_mode: boolean;
  startup?: StartupProgressDTO | null;
};

export type KpiDTO = {
  equity: number;
  open_pnl: number;
  win_rate: number;
  gross_win_pnl: number;
  gross_loss_pnl: number;
  profit_factor: number | null;
  realized_pnl: number;
  unrealized_pnl: number;
  gross_notional: number;
  net_notional: number;
  win_rate_session: number;
  gross_win_pnl_session: number;
  gross_loss_pnl_session: number;
  profit_factor_session: number | null;
  session_close_wins: number;
  session_close_losses: number;
  session_close_breakevens: number;
  rolling_close_wins: number;
  rolling_close_losses: number;
  rolling_close_breakevens: number;
};

export type EquityDTO = { equity: number[]; timestamps: number[]; last_ts: number };

// Wire shapes for the OMS + Execution Quality panels. snake_case here so
// the file mirrors the Python DTOs verbatim; the camelCase translation
// happens in useAlgoStream.
export type ChildOrderDTO = {
  id: string;
  parent_id: string | null;
  symbol: string;
  side: "buy" | "sell";
  qty: number;
  filled_qty: number;
  price: number | null;
  avg_fill_price: number;
  order_type: "limit" | "market";
  status: "new" | "ack" | "partial" | "filled" | "cancelled" | "rejected" | "expired";
  venue_order_id: string | null;
  created_at: number;
  updated_at: number;
};

export type ParentOrderDTO = {
  parent_id: string;
  symbol: string;
  side: "buy" | "sell";
  requested_qty: number;
  filled_qty: number;
  fill_ratio: number;
  arrival_price: number;
  vwap_price: number;
  slippage_bps: number;
  fee_adjusted_slippage_bps?: number;
  impact_bps: number;
  duration_sec: number;
  algo_mode: string | null;
  notes?: string;
  signal_score?: number;
  strategy_name?: string;
  started_at: number;
};

export type ExecutionReportDTO = ParentOrderDTO & {
  completed_at: number | null;
};

export type ExecutionAggregateDTO = {
  count: number;
  avg_slippage_bps: number;
  avg_impact_bps: number;
  avg_fill_ratio: number;
  avg_duration_sec: number;
  total_traded_notional: number;
};

export type ExecutionStatsDTO = {
  working: ParentOrderDTO[];
  history: ExecutionReportDTO[];
  aggregate: ExecutionAggregateDTO;
};

export type OrdersDTO = {
  working: ChildOrderDTO[];
};

export type StrategyInfoDTO = {
  name: string;
  label: string;
  description: string;
  active: boolean;
};

export type TradeDTO = {
  id: string;
  ts: string;
  symbol: string;
  side: "buy" | "sell";
  qty: number;
  price: number;
  action?: "open" | "close";
  entry_price?: number | null;
  exit_price?: number | null;
  pnl?: number | null;
  strategy_name?: string;
  strategy_contributions?: Record<string, number>;
};

export type KlineDTO = {
  open_time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  close_time: number;
};

/** REST `/api/positions` + `StateDTO.positions` wire shape. */
export type PositionDTO = {
  symbol: string;
  side: "long" | "short" | "flat";
  size: number;
  entry: number;
  mark: number;
  unrealized_pnl: number;
};

/** Full backend `Settings` model as JSON (GET/PATCH /api/settings). */
export type SettingsDTO = Record<string, unknown>;

export type SettingsPayloadDTO = {
  settings: SettingsDTO;
  ok?: boolean;
};

export type SystemHealthDTO = {
  latency: Record<string, { p50: number; p95: number; p99: number; count: number }>;
  order_reconcile: Record<string, unknown>;
  md_health: Record<string, Record<string, number | boolean>>;
  clock_skew_ms: number;
  tick_age_sec: number;
  user_data_age_sec: number;
  user_data_monitored?: boolean;
  user_data_stale?: boolean;
  user_data_reconcile_stale?: boolean;
  clock_skew_synced?: boolean;
  active_breakers: string[];
  gross_notional: number;
  net_notional: number;
  realized_pnl: number;
  unrealized_pnl: number;
  equity: number;
  session_peak_equity?: number;
  session_max_drawdown_abs?: number;
  session_max_drawdown_pct?: number;
};

export type StateDTO = {
  status: StatusDTO;
  strategy: StrategyInfoDTO | null;
  strategies: StrategyInfoDTO[];
  kpi: KpiDTO;
  equity: EquityDTO;
  positions: PositionDTO[];
  /** Full fill tape (opens + closes) for the RECENT TRADES widget. */
  trades: TradeDTO[];
  /** Last N closes with realized PnL only — powers win-rate KPI (aligned with engine). */
  realized_trades: TradeDTO[];
  orders: OrdersDTO;
  execution: ExecutionStatsDTO;
  system_health?: SystemHealthDTO | null;
  strategy_analytics?: Record<string, Record<string, string | number | boolean | null>>;
  /** Absolute path to this process's run folder (journal + JSONL). */
  event_archive_run_dir?: string | null;
};

export type StrategyLegDTO = {
  symbol: string;
  side: "long" | "short";
  size: number;
  entry: number;
  mark: number;
  unrealized_pnl: number;
};

export type StrategyPnlDTO = {
  name: string;
  label: string;
  realized_pnl: number;
  unrealized_pnl: number;
  total_pnl: number;
  open_legs: StrategyLegDTO[];
};

export type StrategyHubPortfolioDTO = {
  realized_pnl: number;
  unrealized_pnl: number;
  equity: number;
  session_start_equity: number;
};

export type StrategyHubDTO = {
  ts: number;
  mode: "single" | "all";
  strategies: StrategyPnlDTO[];
  analytics: Record<string, Record<string, string | number | boolean | null>>;
  portfolio: StrategyHubPortfolioDTO;
  run_dir: string | null;
  log_path: string | null;
};

export type StrategyHubLogDTO = {
  lines: Array<Record<string, unknown>>;
  log_path: string | null;
};

export type BreakerStatusDTO = {
  code: string;
  scope: "engine" | "symbol" | "parent";
  severity: "minor" | "major";
  target: string | null;
  state: "armed" | "tripped" | "cooldown" | "latched";
  tripped_at: number;
  cooldown_until: number | null;
  detail: string;
};

export type BreakerDefinitionDTO = {
  code: string;
  severity: "minor" | "major";
  scope: "engine" | "symbol" | "parent";
  label: string;
  description: string;
  group:
    | "market_data"
    | "execution"
    | "portfolio"
    | "reconciliation"
    | "market_making"
    | "operator";
  default_enabled: boolean;
  disableable: boolean;
};

export type BreakerListDTO = {
  active: BreakerStatusDTO[];
  history: BreakerStatusDTO[];
  registry?: BreakerDefinitionDTO[];
  enabled?: Record<string, boolean>;
};

export type BreakerEnabledPatchDTO = {
  code?: string;
  enabled?: boolean;
  patch?: Record<string, boolean>;
  confirm_live_disable?: boolean;
  confirm_token?: string;
};

export type DailyReportDTO = {
  run_dir: string;
  trade_count: number;
  realized_pnl: number;
  avg_slippage_bps: number;
  breaker_events: number;
  reconcile_mismatches: number;
  notes: string[];
};

/** Loose STATUS payload from WebSocket (engine status, system_health, replay, etc.). */
export type StatusEventData = {
  status?: AlgoStatus;
  uptime_sec?: number;
  paper_mode?: boolean;
  startup?: StartupProgressDTO;
  kind?: string;
  clear?: boolean;
  phase?: string;
  label?: string;
  done?: number;
  total?: number;
  symbol?: string | null;
  latency?: SystemHealthDTO["latency"];
  order_reconcile?: Record<string, unknown>;
  md_health?: SystemHealthDTO["md_health"];
  clock_skew_ms?: number;
  tick_age_sec?: number;
  user_data_age_sec?: number;
  user_data_monitored?: boolean;
  user_data_stale?: boolean;
  user_data_reconcile_stale?: boolean;
  clock_skew_synced?: boolean;
  active_breakers?: string[];
  gross_notional?: number;
  net_notional?: number;
  realized_pnl?: number;
  unrealized_pnl?: number;
  equity?: number;
  session_peak_equity?: number;
  session_max_drawdown_abs?: number;
  session_max_drawdown_pct?: number;
  replay_summary?: {
    events_read?: number;
    fills_applied?: number;
    orders_restored?: number;
    open_children?: number;
    wal_path?: string;
    errors?: string[];
  };
};

function formatApiErrorDetail(body: unknown): string {
  if (!body || typeof body !== "object") return "";
  const d = (body as { detail?: unknown }).detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) {
    return d
      .map((item) => {
        if (item && typeof item === "object" && "msg" in item) {
          const msg = (item as { msg?: unknown }).msg;
          const loc = (item as { loc?: unknown }).loc;
          const bits = [Array.isArray(loc) ? loc.join(".") : "", String(msg ?? "")].filter(Boolean);
          return bits.join(": ") || JSON.stringify(item);
        }
        return JSON.stringify(item);
      })
      .join("; ");
  }
  if (d !== undefined) return JSON.stringify(d);
  return "";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    "content-type": "application/json",
    ...(init?.headers as Record<string, string> | undefined),
  };
  if (isControlRoute(path)) {
    Object.assign(headers, authHeaders());
  }
  const response = await fetch(`${API_BASE}${path}`, {
    headers,
    ...init,
  });
  if (!response.ok) {
    // FastAPI: `{ "detail": "..." }` or validation `[{ loc, msg, type }]`.
    let detail = "";
    try {
      const body = await response.clone().json();
      detail = formatApiErrorDetail(body);
      if (!detail && body) detail = JSON.stringify(body);
    } catch {
      try {
        detail = await response.text();
      } catch {
        detail = "";
      }
    }
    const suffix = detail ? ` — ${detail}` : "";
    throw new Error(`API ${path} failed: ${response.status} ${response.statusText}${suffix}`);
  }
  return (await response.json()) as T;
}

export const api = {
  state: () => request<StateDTO>("/api/state"),
  strategyHub: () => request<StrategyHubDTO>("/api/strategy-hub"),
  strategyHubLog: (tail = 20) =>
    request<StrategyHubLogDTO>(`/api/strategy-hub/log?tail=${tail}`),
  status: () => request<StatusDTO>("/api/status"),
  equity: () => request<EquityDTO>("/api/equity"),
  positions: () => request<PositionDTO[]>("/api/positions").then((rows) => rows.map(toPosition)),
  trades: (limit = 40) => request<TradeDTO[]>(`/api/trades?limit=${limit}`),
  /** Full session history by default; pass limit to cap newest N. */
  logs: (limit = 0) => request<LogEntry[]>(`/api/logs?limit=${limit}`),
  orders: () => request<OrdersDTO>("/api/orders"),
  execution: () => request<ExecutionStatsDTO>("/api/execution"),
  klines: (symbol: string, interval: string, limit = 120) =>
    request<KlineDTO[]>(
      `/api/klines?symbol=${encodeURIComponent(symbol)}&interval=${encodeURIComponent(interval)}&limit=${limit}`,
    ),

  start: () => request<StatusDTO>("/api/control/start", { method: "POST" }),
  pause: () => request<StatusDTO>("/api/control/pause", { method: "POST" }),
  resume: () => request<StatusDTO>("/api/control/resume", { method: "POST" }),
  stop: () => request<StatusDTO>("/api/control/stop", { method: "POST" }),
  /** Flatten + stop engine; API process keeps running (dashboard E-Stop). */
  kill: () => request<StatusDTO>("/api/control/kill", { method: "POST" }),
  /** Stop engine and exit the backend process — use only when you intend to restart the server. */
  shutdown: () => request<StatusDTO>("/api/control/shutdown", { method: "POST" }),
  flatten: () => request<StatusDTO>("/api/control/flatten", { method: "POST" }),
  setRisk: (max_risk_pct: number) =>
    request<StatusDTO>("/api/control/risk", {
      method: "PATCH",
      body: JSON.stringify({ max_risk_pct }),
    }),
  setStrategy: (name: string) =>
    request<StatusDTO>("/api/control/strategy", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),

  getSettings: () => request<SettingsPayloadDTO>("/api/settings"),
  patchSettings: (
    patch: SettingsDTO & { confirm_live_disable?: boolean; confirm_token?: string },
  ) =>
    request<SettingsPayloadDTO>("/api/settings", {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),

  reportsLatest: () => request<DailyReportDTO>("/api/reports/latest"),
  listBreakers: () => request<BreakerListDTO>("/api/control/breakers"),
  patchBreakerEnabled: (body: BreakerEnabledPatchDTO) =>
    request<BreakerListDTO>("/api/control/breakers/enabled", {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  rearmBreakers: (body: { code?: string; target?: string } = {}) =>
    request<BreakerListDTO>("/api/control/breakers/rearm", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  tripBreakers: (body: { detail?: string; flatten?: boolean; pause?: boolean } = {}) =>
    request<BreakerListDTO>("/api/control/breakers/trip", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  backtestDatasets: () => request<BacktestDatasetDTO[]>("/api/backtest/datasets"),
  backtestSessions: () => request<BacktestSessionDTO[]>("/api/backtest/sessions"),
  backtestJob: (jobId: string) => request<AnalyticsJobDTO>(`/api/backtest/jobs/${jobId}`),
  backtestDownload: (body: { symbols: string[]; interval?: string; days?: number }) =>
    request<BacktestJobAcceptedDTO>("/api/backtest/download", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  backtestRun: (body: {
    strategy: string;
    dataset?: string;
    start?: string;
    end?: string;
    settings_overrides?: Record<string, unknown>;
  }) =>
    request<BacktestJobAcceptedDTO>("/api/backtest/run", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  backtestRuns: () => request<BacktestResultSummaryDTO[]>("/api/backtest/runs"),
  backtestRunById: (runId: string) => request<BacktestResultDTO>(`/api/backtest/runs/${runId}`),

  mmUniverseReport: () => request<MmUniverseScanReportDTO | null>("/api/analytics/mm-universe"),
  mmUniverseScan: (body: { sample?: boolean; settings_overrides?: Record<string, unknown> } = {}) =>
    request<BacktestJobAcceptedDTO>("/api/analytics/mm-universe/scan", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};

export type WsEvent =
  | { type: "tick"; ts: number; data: { symbol: string; bid: number; ask: number; mid: number } }
  | {
      type: "fill";
      ts: number;
      data: TradeDTO & {
        child_id: string;
        parent_id: string | null;
        trade_id?: string | null;
        venue_price: number;
        impact_bps: number;
      };
    }
  | { type: "order"; ts: number; data: ChildOrderDTO }
  | { type: "parent"; ts: number; data: ParentOrderDTO }
  | { type: "execution"; ts: number; data: ExecutionReportDTO }
  | { type: "position"; ts: number; data: Record<string, unknown> }
  | {
      type: "equity";
      ts: number;
      data: {
        equity: number;
        cash: number;
        ts: number;
        session_peak_equity?: number;
        session_max_drawdown_abs?: number;
        session_max_drawdown_pct?: number;
      };
    }
  | { type: "strategy_hub"; ts: number; data: Record<string, unknown> }
  | { type: "log"; ts: number; data: { level: LogEntry["level"]; msg: string; logger?: string } }
  | { type: "status"; ts: number; data: StatusEventData }
  | { type: "breaker"; ts: number; data: BreakerStatusDTO & { action?: string } };

// --- camelCase mappers (DTO -> view model) ---

/** Mark-to-market PnL for dashboard rows — matches engine ``mark_to_market(use_mark_pnl=True)``. */
export function markDerivedPositionPnl(position: {
  side: Position["side"];
  size: number;
  entry: number;
  mark: number;
}): number {
  const { entry, mark, size } = position;
  if (!(mark > 0) || !(entry > 0) || !(size > 0)) return 0;
  const dir = position.side === "short" ? -1 : 1;
  return (mark - entry) * size * dir;
}

export function toPosition(d: PositionDTO): Position {
  const side: Position["side"] = d.side === "short" ? "short" : "long";
  const entry = Number(d.entry);
  const mark = Number(d.mark);
  const size = Number(d.size);
  const unrealizedPnl = markDerivedPositionPnl({ side, size, entry, mark });
  return {
    symbol: String(d.symbol ?? ""),
    side,
    size: Number.isFinite(size) ? size : 0,
    entry: Number.isFinite(entry) ? entry : 0,
    mark: Number.isFinite(mark) ? mark : 0,
    unrealizedPnl: Number.isFinite(unrealizedPnl) ? unrealizedPnl : 0,
  };
}

export function toWorkingOrder(d: ChildOrderDTO): WorkingOrder {
  return {
    id: d.id,
    parentId: d.parent_id,
    symbol: d.symbol,
    side: d.side,
    qty: d.qty,
    filledQty: d.filled_qty,
    price: d.price,
    avgFillPrice: d.avg_fill_price,
    orderType: d.order_type,
    status: d.status,
    venueOrderId: d.venue_order_id,
    createdAt: d.created_at,
    updatedAt: d.updated_at,
  };
}

export function toExecutionParent(d: ParentOrderDTO | ExecutionReportDTO): ExecutionParent {
  return {
    parentId: d.parent_id,
    symbol: d.symbol,
    side: d.side,
    requestedQty: d.requested_qty,
    filledQty: d.filled_qty,
    fillRatio: d.fill_ratio,
    arrivalPrice: d.arrival_price,
    vwapPrice: d.vwap_price,
    slippageBps: d.slippage_bps,
    feeAdjustedSlippageBps: d.fee_adjusted_slippage_bps ?? d.slippage_bps,
    impactBps: d.impact_bps,
    durationSec: d.duration_sec,
    algoMode: d.algo_mode,
    notes: d.notes ?? "",
    signalScore: d.signal_score ?? 0,
    strategyName: d.strategy_name ?? "",
    startedAt: d.started_at,
    completedAt: "completed_at" in d ? d.completed_at : null,
  };
}

export function toExecutionAggregate(d: ExecutionAggregateDTO): ExecutionAggregate {
  return {
    count: d.count,
    avgSlippageBps: d.avg_slippage_bps,
    avgImpactBps: d.avg_impact_bps,
    avgFillRatio: d.avg_fill_ratio,
    avgDurationSec: d.avg_duration_sec,
    totalTradedNotional: d.total_traded_notional,
  };
}

export function toStrategyInfo(d: StrategyInfoDTO): StrategyInfo {
  return { name: d.name, label: d.label, description: d.description, active: d.active };
}

export function toTrade(d: TradeDTO): Trade {
  const action = d.action ?? (d.exit_price != null ? "close" : "open");
  return {
    id: d.id,
    ts: d.ts,
    symbol: d.symbol,
    side: d.side,
    qty: d.qty,
    price: d.price,
    action,
    entryPrice: d.entry_price ?? d.price,
    exitPrice: d.exit_price ?? null,
    pnl: d.pnl ?? null,
    strategyName: d.strategy_name ?? "",
    strategyContributions: d.strategy_contributions ?? {},
  };
}

export function toBreakerStatus(d: BreakerStatusDTO): import("@/components/algo/types").BreakerStatus {
  return {
    code: d.code,
    scope: d.scope,
    severity: d.severity,
    target: d.target,
    state: d.state,
    trippedAt: d.tripped_at,
    cooldownUntil: d.cooldown_until,
    detail: d.detail,
  };
}

function toBreakerDefinition(
  d: BreakerDefinitionDTO,
): import("@/components/algo/types").BreakerDefinition {
  return {
    code: d.code,
    severity: d.severity,
    scope: d.scope,
    label: d.label,
    description: d.description,
    group: d.group,
    defaultEnabled: d.default_enabled,
    disableable: d.disableable,
  };
}

export function toBreakerList(d: BreakerListDTO): import("@/components/algo/types").BreakerList {
  return {
    active: d.active.map(toBreakerStatus),
    history: d.history.map(toBreakerStatus),
    registry: (d.registry ?? []).map(toBreakerDefinition),
    enabled: d.enabled ?? {},
  };
}

function toStrategyHubPortfolio(
  raw: StrategyHubPortfolioDTO | Record<string, unknown> | undefined,
): import("@/components/algo/types").StrategyHubPortfolio {
  const p = (raw ?? {}) as Record<string, unknown>;
  return {
    realizedPnl: Number(p.realized_pnl ?? p.realizedPnl ?? 0),
    unrealizedPnl: Number(p.unrealized_pnl ?? p.unrealizedPnl ?? 0),
    equity: Number(p.equity ?? 0),
    sessionStartEquity: Number(p.session_start_equity ?? p.sessionStartEquity ?? 0),
  };
}

export function toStrategyHub(d: StrategyHubDTO): import("@/components/algo/types").StrategyHubSnapshot {
  return {
    ts: d.ts,
    mode: d.mode,
    strategies: d.strategies.map((row) => ({
      name: row.name,
      label: row.label,
      realizedPnl: row.realized_pnl,
      unrealizedPnl: row.unrealized_pnl,
      totalPnl: row.total_pnl,
      openLegs: row.open_legs.map((leg) => ({
        symbol: leg.symbol,
        side: leg.side,
        size: leg.size,
        entry: leg.entry,
        mark: leg.mark,
        unrealizedPnl: leg.unrealized_pnl,
      })),
    })),
    analytics: d.analytics ?? {},
    portfolio: toStrategyHubPortfolio(d.portfolio),
    runDir: d.run_dir,
    logPath: d.log_path,
  };
}

export function toDailyReport(d: DailyReportDTO): import("@/components/algo/types").DailyReport {
  return {
    runDir: d.run_dir,
    tradeCount: d.trade_count,
    realizedPnl: d.realized_pnl,
    avgSlippageBps: d.avg_slippage_bps,
    breakerEvents: d.breaker_events,
    reconcileMismatches: d.reconcile_mismatches,
    notes: d.notes,
  };
}

export function toSystemHealth(d: SystemHealthDTO): import("@/components/algo/types").SystemHealth {
  return {
    latency: d.latency ?? {},
    orderReconcile: d.order_reconcile ?? {},
    mdHealth: Object.fromEntries(
      Object.entries(d.md_health ?? {}).map(([k, v]) => [
        k,
        {
          sequence_gaps: Number(v.sequence_gaps ?? 0),
          crossed_count: Number(v.crossed_count ?? 0),
          last_diff_age_ms: Number(v.last_diff_age_ms ?? -1),
        },
      ]),
    ),
    clockSkewMs: d.clock_skew_ms,
    tickAgeSec: d.tick_age_sec,
    userDataAgeSec: d.user_data_age_sec,
    userDataMonitored: Boolean(d.user_data_monitored),
    userDataStale: Boolean(d.user_data_stale),
    userDataReconcileStale: Boolean(d.user_data_reconcile_stale),
    clockSkewSynced: Boolean(d.clock_skew_synced),
    activeBreakers: d.active_breakers ?? [],
    grossNotional: d.gross_notional,
    netNotional: d.net_notional,
    realizedPnl: d.realized_pnl,
    unrealizedPnl: d.unrealized_pnl,
    equity: d.equity,
    sessionPeakEquity: Number(d.session_peak_equity ?? d.equity ?? 0),
    sessionMaxDrawdownAbs: Number(d.session_max_drawdown_abs ?? 0),
    sessionMaxDrawdownPct: Number(d.session_max_drawdown_pct ?? 0),
  };
}

export function toStrategyHubPayload(
  data: Record<string, unknown>,
): import("@/components/algo/types").StrategyHubSnapshot {
  const strategies = Array.isArray(data.strategies) ? data.strategies : [];
  return {
    ts: Number(data.ts ?? 0),
    mode: data.mode === "all" ? "all" : "single",
    strategies: strategies.map((row) => {
      const r = row as Record<string, unknown>;
      const legs = Array.isArray(r.open_legs) ? r.open_legs : [];
      return {
        name: String(r.name ?? ""),
        label: String(r.label ?? r.name ?? ""),
        realizedPnl: Number(r.realized_pnl ?? 0),
        unrealizedPnl: Number(r.unrealized_pnl ?? 0),
        totalPnl: Number(r.total_pnl ?? 0),
        openLegs: legs.map((leg) => {
          const l = leg as Record<string, unknown>;
          return {
            symbol: String(l.symbol ?? ""),
            side: l.side === "short" ? "short" : "long",
            size: Number(l.size ?? 0),
            entry: Number(l.entry ?? 0),
            mark: Number(l.mark ?? 0),
            unrealizedPnl: Number(l.unrealized_pnl ?? 0),
          };
        }),
      };
    }),
    analytics:
      (data.analytics as import("@/components/algo/types").StrategyAnalytics) ?? {},
    portfolio: toStrategyHubPortfolio(
      data.portfolio as Record<string, unknown> | undefined,
    ),
    runDir: null,
    logPath: null,
  };
}

export function toKline(d: KlineDTO): Kline {
  return {
    openTime: d.open_time,
    open: d.open,
    high: d.high,
    low: d.low,
    close: d.close,
    volume: d.volume,
    closeTime: d.close_time,
  };
}

export type BacktestDatasetDTO = {
  symbol: string;
  interval: string;
  source: "live" | "download" | "mixed";
  rows: number;
  start: string | null;
  end: string | null;
  path: string;
  run_ids: string[];
  updated_at: string;
};

export type BacktestSessionDTO = {
  run_id: string;
  label: string;
};

export type BacktestMetricsDTO = {
  total_return_pct: number;
  max_drawdown_pct: number;
  trade_count: number;
  win_rate: number;
  realized_pnl: number;
  final_equity: number;
};

export type BacktestFillDTO = {
  symbol: string;
  side: string;
  qty: number;
  price: number;
  ts: number;
  reason: string;
  pnl: number;
  action: string;
};

export type BacktestResultDTO = {
  run_id: string;
  strategy: string;
  dataset: string;
  bar_count: number;
  symbols: string[];
  metrics: BacktestMetricsDTO;
  equity_curve: number[];
  fills: BacktestFillDTO[];
  notes: string[];
};

export type BacktestResultSummaryDTO = {
  run_id: string;
  strategy: string;
  dataset: string;
  bar_count: number;
  total_return_pct: number;
  saved_at: string | null;
};

export type BacktestJobAcceptedDTO = {
  job_id: string;
  status: string;
};

export type MmUniverseRankingDTO = {
  symbol: string;
  quote_volume_24h: number;
  last_price: number;
  median_spread_bps: number;
  spread_cv: number;
  mid_vol_bps: number;
  edge_bps: number;
  score: number;
  eligible: boolean;
  reject_reason: string | null;
};

export type MmUniverseThresholdsDTO = {
  max_spread_cv: number;
  max_mid_vol_bps: number;
  stability_percentile: number;
  spread_cv_median: number;
  mid_vol_median: number;
  range_vol_24h_median: number;
  source: string;
};

export type MmUniverseScanReportDTO = {
  generated_at: string;
  recommended: string[];
  candidates_scanned: number;
  sample_rounds: number;
  rankings: MmUniverseRankingDTO[];
  thresholds: MmUniverseThresholdsDTO | null;
};

export type AnalyticsJobDTO = {
  id: string;
  type: string;
  status: string;
  progress: number;
  result: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
  updated_at: string;
};

const JOB_POLL_MS = 500;
const JOB_MAX_WAIT_MS = 600_000;

export async function pollAnalyticsJob(
  jobId: string,
  opts?: { intervalMs?: number; maxWaitMs?: number },
): Promise<AnalyticsJobDTO> {
  const intervalMs = opts?.intervalMs ?? JOB_POLL_MS;
  const maxWaitMs = opts?.maxWaitMs ?? JOB_MAX_WAIT_MS;
  const deadline = Date.now() + maxWaitMs;
  while (Date.now() < deadline) {
    const job = await api.backtestJob(jobId);
    if (job.status === "done" || job.status === "failed") {
      return job;
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  throw new Error(`job ${jobId} timed out after ${maxWaitMs}ms`);
}

export function toBacktestDataset(d: BacktestDatasetDTO): import("@/components/algo/types").BacktestDataset {
  return {
    symbol: d.symbol,
    interval: d.interval,
    source: d.source,
    rows: d.rows,
    start: d.start,
    end: d.end,
    path: d.path,
    runIds: d.run_ids,
    updatedAt: d.updated_at,
  };
}

export function toBacktestResult(d: BacktestResultDTO): import("@/components/algo/types").BacktestResult {
  return {
    runId: d.run_id,
    strategy: d.strategy,
    dataset: d.dataset,
    barCount: d.bar_count,
    symbols: d.symbols,
    metrics: {
      totalReturnPct: d.metrics.total_return_pct,
      maxDrawdownPct: d.metrics.max_drawdown_pct,
      tradeCount: d.metrics.trade_count,
      winRate: d.metrics.win_rate,
      realizedPnl: d.metrics.realized_pnl,
      finalEquity: d.metrics.final_equity,
    },
    equityCurve: d.equity_curve,
    fills: d.fills.map((f) => ({
      symbol: f.symbol,
      side: f.side,
      qty: f.qty,
      price: f.price,
      ts: f.ts,
      reason: f.reason,
      pnl: f.pnl,
      action: f.action,
    })),
    notes: d.notes,
  };
}
