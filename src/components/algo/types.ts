// View-model shapes consumed by the dashboard. Mirrors the camelCase
// translation of the Python DTOs exported from `backend/api/schemas.py`.
// No fixtures live here — every value the UI renders comes from the
// backend over REST + WebSocket (see `useAlgoStream`).

export type AlgoStatus = "running" | "paused" | "stopped";

export type Position = {
  symbol: string;
  side: "long" | "short";
  size: number;
  entry: number;
  mark: number;
};

export type Trade = {
  id: string;
  ts: string;
  symbol: string;
  side: "buy" | "sell";
  qty: number;
  price: number;
  pnl: number | null;
};

export type LogEntry = {
  ts: string;
  level: "info" | "warn" | "error" | "signal";
  msg: string;
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
  status: "new" | "ack" | "partial" | "filled" | "cancelled" | "rejected";
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
  impactBps: number;
  durationSec: number;
  algoMode: string | null;
  startedAt: number;
  completedAt: number | null;
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
