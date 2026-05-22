// View-model shapes consumed by the dashboard. Mirrors the camelCase
// translation of the Python DTOs exported from `backend/api/schemas.py`.
// No fixtures live here — every value the UI renders comes from the
// backend over REST + WebSocket (see `useAlgoStream`).

export type AlgoStatus = "running" | "paused" | "stopped" | "starting";

export type StartupProgress = {
  phase: string;
  label: string;
  done: number;
  total: number;
  symbol: string | null;
};

export type Position = {
  symbol: string;
  side: "long" | "short";
  size: number;
  entry: number;
  mark: number;
  /** Venue / engine unrealized PnL in quote currency (matches row-level exchange uPnL when provided). */
  unrealizedPnl: number;
};

export type Trade = {
  id: string;
  ts: string;
  symbol: string;
  side: "buy" | "sell";
  qty: number;
  price: number;
  action: "open" | "close";
  entryPrice: number | null;
  exitPrice: number | null;
  pnl: number | null;
};

export type LogEntry = {
  ts: string;
  level: "debug" | "info" | "warn" | "error" | "signal";
  msg: string;
  logger?: string;
};

// Live working child order tracked by the OMS panel.
export type WorkingOrder = {
  id: string;
  parentId: string | null;
  symbol: string;
  side: "buy" | "sell";
  qty: number;
  filledQty: number;
  price: number | null;
  avgFillPrice: number;
  orderType: "limit" | "market";
  status: "new" | "ack" | "partial" | "filled" | "cancelled" | "rejected" | "expired";
  venueOrderId: string | null;
  createdAt: number;
  updatedAt: number;
};

// One in-flight or completed parent order with execution-quality metrics.
export type ExecutionParent = {
  parentId: string;
  symbol: string;
  side: "buy" | "sell";
  requestedQty: number;
  filledQty: number;
  fillRatio: number;
  arrivalPrice: number;
  vwapPrice: number;
  slippageBps: number;
  feeAdjustedSlippageBps: number;
  impactBps: number;
  durationSec: number;
  algoMode: string | null;
  notes: string;
  signalScore: number;
  strategyName: string;
  startedAt: number;
  completedAt: number | null;
};

export type SystemHealth = {
  latency: Record<string, { p50: number; p95: number; p99: number; count: number }>;
  orderReconcile: { ok?: boolean; venue_only?: number; local_only?: number; ts?: number };
  mdHealth: Record<string, { sequence_gaps: number; crossed_count: number; last_diff_age_ms: number }>;
  clockSkewMs: number;
  tickAgeSec: number;
  userDataAgeSec: number;
  /** True when engine is running and OMS has working orders (staleness enforced). */
  userDataMonitored: boolean;
  /** True when monitored and age exceeds WS_STALE_PAUSE_SEC. */
  userDataStale: boolean;
  /** True when exposure is open and age exceeds reconcile_user_data_fresh_sec. */
  userDataReconcileStale: boolean;
  clockSkewSynced: boolean;
  activeBreakers: string[];
  grossNotional: number;
  netNotional: number;
  realizedPnl: number;
  unrealizedPnl: number;
  equity: number;
};

export type ExecutionAggregate = {
  count: number;
  avgSlippageBps: number;
  avgImpactBps: number;
  avgFillRatio: number;
  avgDurationSec: number;
  totalTradedNotional: number;
};

// Identity of a strategy registered with the engine. ``active`` flags
// the one currently emitting signals; the dashboard hot-swap toggles
// this via POST /api/control/strategy.
export type StrategyInfo = {
  name: string;
  label: string;
  description: string;
  active: boolean;
};

// One historical OHLCV bar used by the position chart.
export type Kline = {
  openTime: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  closeTime: number;
};

export type BreakerStatus = {
  code: string;
  scope: "engine" | "symbol" | "parent";
  severity: "minor" | "major";
  target: string | null;
  state: "armed" | "tripped" | "cooldown" | "latched";
  trippedAt: number;
  cooldownUntil: number | null;
  detail: string;
};

export type BreakerList = {
  active: BreakerStatus[];
  history: BreakerStatus[];
};

export type BacktestDataset = {
  symbol: string;
  interval: string;
  source: "live" | "download" | "mixed";
  rows: number;
  start: string | null;
  end: string | null;
  path: string;
  runIds: string[];
  updatedAt: string;
};

export type BacktestSession = {
  runId: string;
  label: string;
};

export type BacktestMetrics = {
  totalReturnPct: number;
  maxDrawdownPct: number;
  tradeCount: number;
  winRate: number;
  realizedPnl: number;
  finalEquity: number;
};

export type BacktestFill = {
  symbol: string;
  side: string;
  qty: number;
  price: number;
  ts: number;
  reason: string;
  pnl: number;
  action: string;
};

export type BacktestResult = {
  runId: string;
  strategy: string;
  dataset: string;
  barCount: number;
  symbols: string[];
  metrics: BacktestMetrics;
  equityCurve: number[];
  fills: BacktestFill[];
  notes: string[];
};

export type BacktestResultSummary = {
  runId: string;
  strategy: string;
  dataset: string;
  barCount: number;
  totalReturnPct: number;
  savedAt: string | null;
};

export type DailyReport = {
  runDir: string;
  tradeCount: number;
  realizedPnl: number;
  avgSlippageBps: number;
  breakerEvents: number;
  reconcileMismatches: number;
  notes: string[];
};
