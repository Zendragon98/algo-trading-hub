// REST + WebSocket client for the ALPHA-7 backend (FastAPI on :8000).
//
// All payload shapes are dictated by `backend/api/schemas.py` and mirror
// the types that already live in `src/components/algo/mockData.ts`. Keep
// the two in sync — the dashboard binds directly to these shapes.

import type {
  AlgoStatus,
  ExecutionAggregate,
  ExecutionParent,
  LogEntry,
  Position,
  Trade,
  WorkingOrder,
} from "@/components/algo/mockData";

const RAW_BASE = (import.meta as { env?: Record<string, string | undefined> }).env?.VITE_API_BASE;
export const API_BASE = (RAW_BASE ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const WS_BASE = API_BASE.replace(/^http/, "ws");

export type StatusDTO = { status: AlgoStatus; uptime_sec: number; paper_mode: boolean };

export type KpiDTO = {
  equity: number;
  open_pnl: number;
  win_rate: number;
  realized_pnl: number;
  unrealized_pnl: number;
  gross_notional: number;
  net_notional: number;
};

export type EquityDTO = { equity: number[]; last_ts: number };

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
  status: "new" | "ack" | "partial" | "filled" | "cancelled" | "rejected";
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
  impact_bps: number;
  duration_sec: number;
  algo_mode: string | null;
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

export type StateDTO = {
  status: StatusDTO;
  kpi: KpiDTO;
  equity: EquityDTO;
  positions: Position[];
  trades: Trade[];
  orders: OrdersDTO;
  execution: ExecutionStatsDTO;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!response.ok) {
    throw new Error(`API ${path} failed: ${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}

export const api = {
  state: () => request<StateDTO>("/api/state"),
  status: () => request<StatusDTO>("/api/status"),
  positions: () => request<Position[]>("/api/positions"),
  trades: (limit = 40) => request<Trade[]>(`/api/trades?limit=${limit}`),
  logs: (limit = 60) => request<LogEntry[]>(`/api/logs?limit=${limit}`),
  orders: () => request<OrdersDTO>("/api/orders"),
  execution: () => request<ExecutionStatsDTO>("/api/execution"),

  start: () => request<StatusDTO>("/api/control/start", { method: "POST" }),
  pause: () => request<StatusDTO>("/api/control/pause", { method: "POST" }),
  resume: () => request<StatusDTO>("/api/control/resume", { method: "POST" }),
  stop: () => request<StatusDTO>("/api/control/stop", { method: "POST" }),
  flatten: () => request<StatusDTO>("/api/control/flatten", { method: "POST" }),
  setRisk: (max_risk_pct: number) =>
    request<StatusDTO>("/api/control/risk", {
      method: "PATCH",
      body: JSON.stringify({ max_risk_pct }),
    }),
};

export type WsEvent =
  | { type: "tick"; ts: number; data: { symbol: string; bid: number; ask: number; mid: number } }
  | {
      type: "fill";
      ts: number;
      data: {
        child_id: string;
        parent_id: string | null;
        symbol: string;
        side: "buy" | "sell";
        qty: number;
        price: number;
        venue_price: number;
        impact_bps: number;
      };
    }
  | { type: "order"; ts: number; data: ChildOrderDTO }
  | { type: "parent"; ts: number; data: ParentOrderDTO }
  | { type: "execution"; ts: number; data: ExecutionReportDTO }
  | { type: "position"; ts: number; data: Position & { unrealized_pnl: number; notional: number } }
  | { type: "equity"; ts: number; data: { equity: number; cash: number; ts: number } }
  | { type: "log"; ts: number; data: { level: LogEntry["level"]; msg: string; logger?: string } }
  | { type: "status"; ts: number; data: { status: AlgoStatus; uptime_sec: number } };

export const wsUrl = `${WS_BASE}/ws`;

// --- camelCase mappers (DTO -> view model) ---

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
    impactBps: d.impact_bps,
    durationSec: d.duration_sec,
    algoMode: d.algo_mode,
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
