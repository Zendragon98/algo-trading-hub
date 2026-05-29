/**
 * Pure live-console state: REST hydrate, WS event reducers, and defaults.
 * Connection lifecycle lives in `hooks/useAlgoStream.ts`.
 */

import {
  toBreakerStatus,
  toExecutionAggregate,
  toExecutionParent,
  toPosition,
  toStrategyInfo,
  toSystemHealth,
  toTrade,
  toWorkingOrder,
  type KpiDTO,
  type PositionDTO,
  type StateDTO,
  type StartupProgressDTO,
  type StatusEventData,
  type WsEvent,
} from "@/lib/api";
import {
  accumulateParentClose,
  appendRealizedClose,
  bumpKpiOnRealizedClose,
  finalizeParentCloseTrade,
  rollingKpiFromRealized,
  type PendingParentClose,
} from "@/lib/parentCloseKpi";
import type {
  AlgoStatus,
  BreakerList,
  StartupProgress,
  ExecutionAggregate,
  ExecutionParent,
  LogEntry,
  Position,
  StrategyInfo,
  SystemHealth,
  Trade,
  WorkingOrder,
} from "@/components/algo/types";

const fmtTime = (epoch?: number) => {
  const date = epoch ? new Date(epoch * 1000) : new Date();
  return date.toLocaleTimeString("en-GB", { hour12: false });
};

export const EMPTY_BREAKERS: BreakerList = {
  active: [],
  history: [],
  registry: [],
  enabled: {},
};

/** Must match ``PerformanceTracker`` default ``history_size`` (engine). */
export const PERFORMANCE_TRADE_HISTORY_CAP = 200;

export type AlgoStream = {
  status: AlgoStatus;
  startupProgress: StartupProgress | null;
  bookResyncProgress: StartupProgress | null;
  paperMode: boolean;
  uptimeSec: number;
  strategy: StrategyInfo | null;
  strategies: StrategyInfo[];
  equity: number[];
  positions: Position[];
  trades: Trade[];
  realizedTrades: Trade[];
  logs: LogEntry[];
  orders: WorkingOrder[];
  workingParents: ExecutionParent[];
  executionHistory: ExecutionParent[];
  executionAggregate: ExecutionAggregate;
  connected: boolean;
  backendReachable: boolean;
  error: string | null;
  systemHealth: SystemHealth | null;
  maxRiskPct: number;
  maxGrossNotional: number;
  settingsSnapshot: Record<string, unknown>;
  breakers: BreakerList;
  replaySummary: string | null;
  refresh: () => Promise<void>;
  markBackendOffline: (message?: string) => void;
  kpi: KpiDTO;
  eventArchiveRunDir: string | null;
};

export const EMPTY_AGG: ExecutionAggregate = {
  count: 0,
  avgSlippageBps: 0,
  avgImpactBps: 0,
  avgFillRatio: 0,
  avgDurationSec: 0,
  totalTradedNotional: 0,
};

const NOOP_REFRESH = async () => {};

export const EMPTY_KPI: KpiDTO = {
  equity: 0,
  open_pnl: 0,
  win_rate: 0,
  gross_win_pnl: 0,
  gross_loss_pnl: 0,
  profit_factor: null,
  realized_pnl: 0,
  unrealized_pnl: 0,
  gross_notional: 0,
  net_notional: 0,
  win_rate_session: 0,
  gross_win_pnl_session: 0,
  gross_loss_pnl_session: 0,
  profit_factor_session: null,
  session_close_wins: 0,
  session_close_losses: 0,
  session_close_breakevens: 0,
};

export const TERMINAL_ORDER_STATUSES: ReadonlyArray<WorkingOrder["status"]> = [
  "filled",
  "cancelled",
  "rejected",
  "expired",
];

/** Periodic REST hydrate so positions/orders stay aligned with the engine. */
export const TRADING_STATE_SYNC_MS = 5_000;
/** Debounce rapid WS reconnects before pulling a full snapshot. */
export const WS_RESYNC_DEBOUNCE_MS = 250;

const LATENCY_KEYS = new Set([
  "tick_to_signal_ms",
  "signal_to_risk_ms",
  "risk_to_submit_ms",
  "tick_to_submit_ms",
  "submit_to_ack_ms",
  "tick_to_ack_ms",
]);

export const BACKEND_OFFLINE_MSG =
  "Backend unreachable. After Kill, restart the API (e.g. python backend/main.py).";

export function createEmptyAlgoStream(): AlgoStream {
  return {
    status: "stopped",
    startupProgress: null,
    bookResyncProgress: null,
    paperMode: false,
    uptimeSec: 0,
    strategy: null,
    strategies: [],
    equity: [],
    positions: [],
    trades: [],
    realizedTrades: [],
    logs: [],
    orders: [],
    workingParents: [],
    executionHistory: [],
    executionAggregate: EMPTY_AGG,
    connected: false,
    backendReachable: false,
    error: null,
    systemHealth: null,
    maxRiskPct: 0.35,
    maxGrossNotional: 100_000,
    settingsSnapshot: {},
    breakers: EMPTY_BREAKERS,
    replaySummary: null,
    refresh: NOOP_REFRESH,
    markBackendOffline: () => {},
    kpi: EMPTY_KPI,
    eventArchiveRunDir: null,
  };
}

export function toStartupProgress(d: StartupProgressDTO): StartupProgress {
  return {
    phase: d.phase,
    label: d.label,
    done: d.done,
    total: d.total,
    symbol: d.symbol,
  };
}

export function numSetting(
  settings: Record<string, unknown>,
  key: string,
  fallback: number,
): number {
  const v = settings[key];
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}

function formatReplaySummary(
  summary: NonNullable<StatusEventData["replay_summary"]>,
): string {
  const parts = [
    `WAL replay: ${summary.events_read ?? 0} events`,
    `${summary.fills_applied ?? 0} fills`,
    `${summary.orders_restored ?? 0} orders restored`,
    `${summary.open_children ?? 0} open children`,
  ];
  if (summary.errors?.length) {
    parts.push(`${summary.errors.length} error(s)`);
  }
  return parts.join(" · ");
}

function mergeLatencyMetrics(
  prev: SystemHealth | null,
  data: StatusEventData,
): SystemHealth["latency"] {
  const latency = { ...(prev?.latency ?? {}) };
  for (const [key, val] of Object.entries(data)) {
    if (key === "kind" || !LATENCY_KEYS.has(key)) continue;
    if (val && typeof val === "object" && "p95" in val) {
      latency[key] = val as SystemHealth["latency"][string];
    }
  }
  return latency;
}

function mapPositionsFromState(raw: unknown): Position[] {
  if (!Array.isArray(raw)) return [];
  const out: Position[] = [];
  for (const item of raw) {
    try {
      const p = toPosition(item as PositionDTO);
      if (p.symbol) out.push(p);
    } catch {
      /* one bad row must not blank the whole positions panel */
    }
  }
  return out;
}

export function applyBackendOffline(prev: AlgoStream, message?: string): AlgoStream {
  return {
    ...prev,
    connected: false,
    backendReachable: false,
    status: "stopped",
    startupProgress: null,
    bookResyncProgress: null,
    error: message ?? prev.error ?? BACKEND_OFFLINE_MSG,
  };
}

export function applyTradingState(
  prev: AlgoStream,
  state: StateDTO,
  parentClosePending: Map<string, PendingParentClose>,
): AlgoStream {
  parentClosePending.clear();
  return {
    ...prev,
    backendReachable: true,
    status: state.status.status,
    startupProgress: state.status.startup ? toStartupProgress(state.status.startup) : null,
    bookResyncProgress: state.status.status === "starting" ? null : prev.bookResyncProgress,
    paperMode: state.status.paper_mode,
    uptimeSec: Math.floor(state.status.uptime_sec),
    strategy: state.strategy ? toStrategyInfo(state.strategy) : null,
    strategies: state.strategies.map(toStrategyInfo),
    equity: state.equity.equity,
    positions: mapPositionsFromState(state.positions),
    trades: state.trades.map(toTrade),
    realizedTrades: (state.realized_trades ?? []).map(toTrade),
    orders: state.orders.working.map(toWorkingOrder),
    workingParents: state.execution.working.map(toExecutionParent),
    executionHistory: state.execution.history.map(toExecutionParent),
    executionAggregate: toExecutionAggregate(state.execution.aggregate),
    systemHealth: state.system_health ? toSystemHealth(state.system_health) : null,
    kpi: state.kpi,
    eventArchiveRunDir: state.event_archive_run_dir ?? null,
    error: null,
  };
}

export function systemHealthFromStatus(
  prev: SystemHealth | null,
  data: StatusEventData,
): SystemHealth {
  const base = prev ?? {
    latency: {},
    orderReconcile: {},
    mdHealth: {},
    clockSkewMs: 0,
    tickAgeSec: -1,
    userDataAgeSec: -1,
    userDataMonitored: false,
    userDataStale: false,
    userDataReconcileStale: false,
    clockSkewSynced: false,
    activeBreakers: [],
    grossNotional: 0,
    netNotional: 0,
    realizedPnl: 0,
    unrealizedPnl: 0,
    equity: 0,
  };
  return {
    latency: data.latency ? { ...base.latency, ...data.latency } : base.latency,
    orderReconcile:
      (data.order_reconcile as SystemHealth["orderReconcile"]) ?? base.orderReconcile,
    mdHealth: data.md_health
      ? Object.fromEntries(
          Object.entries(data.md_health).map(([k, v]) => [
            k,
            {
              sequence_gaps: Number(v.sequence_gaps ?? 0),
              crossed_count: Number(v.crossed_count ?? 0),
              last_diff_age_ms: Number(v.last_diff_age_ms ?? -1),
            },
          ]),
        )
      : base.mdHealth,
    clockSkewMs: Number(data.clock_skew_ms ?? base.clockSkewMs),
    tickAgeSec: Number(data.tick_age_sec ?? base.tickAgeSec),
    userDataAgeSec: Number(data.user_data_age_sec ?? base.userDataAgeSec),
    userDataMonitored: Boolean(data.user_data_monitored ?? base.userDataMonitored),
    userDataStale: Boolean(data.user_data_stale ?? base.userDataStale),
    userDataReconcileStale: Boolean(
      data.user_data_reconcile_stale ?? base.userDataReconcileStale,
    ),
    clockSkewSynced: Boolean(data.clock_skew_synced ?? base.clockSkewSynced),
    activeBreakers: (data.active_breakers as string[]) ?? base.activeBreakers,
    grossNotional: Number(data.gross_notional ?? base.grossNotional),
    netNotional: Number(data.net_notional ?? base.netNotional),
    realizedPnl: Number(data.realized_pnl ?? base.realizedPnl),
    unrealizedPnl: Number(data.unrealized_pnl ?? base.unrealizedPnl),
    equity: Number(data.equity ?? base.equity),
  };
}

export function aggregateExecutionHistory(
  history: ExecutionParent[],
): ExecutionAggregate {
  if (!history.length) return EMPTY_AGG;
  const n = history.length;
  return {
    count: n,
    avgSlippageBps: history.reduce((a, r) => a + r.slippageBps, 0) / n,
    avgImpactBps: history.reduce((a, r) => a + r.impactBps, 0) / n,
    avgFillRatio: history.reduce((a, r) => a + r.fillRatio, 0) / n,
    avgDurationSec: history.reduce((a, r) => a + r.durationSec, 0) / n,
    totalTradedNotional: history.reduce((a, r) => a + r.filledQty * r.vwapPrice, 0),
  };
}

export function applyWsEvent(
  prev: AlgoStream,
  event: WsEvent,
  parentClosePending: Map<string, PendingParentClose>,
): AlgoStream {
  switch (event.type) {
    case "status": {
      const data = event.data;
      let next = prev;
      if (data.status !== undefined) {
        next = {
          ...prev,
          status: data.status,
          uptimeSec: Math.floor(data.uptime_sec ?? prev.uptimeSec),
          startupProgress:
            data.status === "starting" && data.startup
              ? toStartupProgress(data.startup)
              : data.status === "starting"
                ? prev.startupProgress
                : null,
          bookResyncProgress: data.status === "starting" ? null : prev.bookResyncProgress,
        };
      } else if (data.startup) {
        next = {
          ...next,
          startupProgress: toStartupProgress(data.startup),
        };
      }

      if (data.kind === "book_resync") {
        if (data.clear) {
          return { ...next, bookResyncProgress: null };
        }
        if (data.label && data.phase) {
          return {
            ...next,
            bookResyncProgress: {
              phase: data.phase,
              label: data.label,
              done: Number(data.done ?? 0),
              total: Number(data.total ?? 0),
              symbol: data.symbol ?? null,
            },
          };
        }
      }

      if (data.replay_summary) {
        next = { ...next, replaySummary: formatReplaySummary(data.replay_summary) };
      }

      if (data.kind === "system_health") {
        return {
          ...next,
          systemHealth: systemHealthFromStatus(prev.systemHealth, data),
        };
      }

      if (data.kind === "latency_metrics") {
        const latency = mergeLatencyMetrics(prev.systemHealth, data);
        const base = prev.systemHealth ?? systemHealthFromStatus(null, {});
        return {
          ...next,
          systemHealth: { ...base, latency },
        };
      }

      return next;
    }

    case "equity": {
      const point = event.data.equity;
      const nextEquity = [...prev.equity, point];
      const trimmed = nextEquity.length > 256 ? nextEquity.slice(-256) : nextEquity;
      const d = event.data as {
        gross_notional?: number;
        net_notional?: number;
        realized_pnl?: number;
        unrealized_pnl?: number;
        equity?: number;
      };
      if (
        prev.systemHealth &&
        (d.gross_notional !== undefined ||
          d.net_notional !== undefined ||
          d.realized_pnl !== undefined ||
          d.unrealized_pnl !== undefined)
      ) {
        return {
          ...prev,
          equity: trimmed,
          systemHealth: {
            ...prev.systemHealth,
            grossNotional: Number(d.gross_notional ?? prev.systemHealth.grossNotional),
            netNotional: Number(d.net_notional ?? prev.systemHealth.netNotional),
            realizedPnl: Number(d.realized_pnl ?? prev.systemHealth.realizedPnl),
            unrealizedPnl: Number(d.unrealized_pnl ?? prev.systemHealth.unrealizedPnl),
            equity: Number(d.equity ?? point),
          },
        };
      }
      return { ...prev, equity: trimmed };
    }

    case "position": {
      const incoming = event.data as Record<string, unknown>;
      const symbol = String(incoming.symbol ?? "");
      if (!symbol) {
        return prev;
      }
      const others = prev.positions.filter((p) => p.symbol !== symbol);
      const qtyRaw = incoming.qty;
      const sizeRaw = incoming.size;
      const qtyNum = qtyRaw === undefined || qtyRaw === null ? NaN : Number(qtyRaw);
      const isFlat =
        qtyRaw === 0 ||
        (typeof qtyRaw === "number" && Math.abs(qtyRaw) < 1e-12) ||
        sizeRaw === 0 ||
        (typeof sizeRaw === "number" && Math.abs(sizeRaw) < 1e-12);
      if (isFlat) {
        return { ...prev, positions: others };
      }
      const size =
        sizeRaw !== undefined && sizeRaw !== null && String(sizeRaw) !== ""
          ? Number(sizeRaw)
          : Number.isFinite(qtyNum)
            ? Math.abs(qtyNum)
            : 0;
      if (!Number.isFinite(size) || size < 1e-12) {
        return { ...prev, positions: others };
      }
      const sideRaw = String(incoming.side ?? "long");
      const side = (sideRaw === "short" ? "short" : "long") as Position["side"];
      const entry = Number(
        (incoming as { avg_entry_price?: number }).avg_entry_price ?? incoming.entry ?? 0,
      );
      const mark = Number((incoming as { mark_price?: number }).mark_price ?? incoming.mark ?? 0);
      const dir = side === "short" ? -1 : 1;
      const rawUp = (incoming as { unrealized_pnl?: number }).unrealized_pnl;
      const unrealizedPnl =
        rawUp !== undefined && Number.isFinite(Number(rawUp))
          ? Number(rawUp)
          : (mark - entry) * size * dir;
      return {
        ...prev,
        positions: [...others, { symbol, side, size, entry, mark, unrealizedPnl }],
      };
    }

    case "fill": {
      const d = event.data as {
        id?: string;
        trade_id?: string;
        child_id?: string;
        parent_id?: string | null;
        symbol: string;
        side: "buy" | "sell";
        qty: number;
        price: number;
        action?: "open" | "close";
        entry_price?: number | null;
        exit_price?: number | null;
        pnl?: number | null;
      };
      const tradeId = String(d.id ?? d.trade_id ?? d.child_id ?? "");
      if (tradeId && prev.trades.some((t) => t.id === tradeId)) {
        return prev;
      }
      const trade = toTrade({
        id: tradeId,
        ts: fmtTime(event.ts),
        symbol: d.symbol,
        side: d.side,
        qty: d.qty,
        price: d.price,
        action: d.action,
        entry_price: d.entry_price,
        exit_price: d.exit_price,
        pnl: d.pnl,
      });
      const isRealizedClose = trade.action === "close" && trade.pnl != null;
      const nextTrades = [trade, ...prev.trades].slice(0, PERFORMANCE_TRADE_HISTORY_CAP);
      const parentId = d.parent_id ? String(d.parent_id) : null;

      if (isRealizedClose && parentId) {
        accumulateParentClose(parentClosePending, parentId, trade, trade.pnl ?? 0);
        return { ...prev, trades: nextTrades };
      }

      let nextRealized = prev.realizedTrades;
      let nextKpi = prev.kpi;
      if (isRealizedClose) {
        nextRealized = appendRealizedClose(
          prev.realizedTrades,
          trade,
          PERFORMANCE_TRADE_HISTORY_CAP,
        );
        nextKpi = bumpKpiOnRealizedClose(prev.kpi, trade.pnl ?? 0);
        nextKpi = rollingKpiFromRealized(nextRealized, nextKpi);
      }
      return {
        ...prev,
        trades: nextTrades,
        realizedTrades: nextRealized,
        kpi: nextKpi,
      };
    }

    case "order": {
      const incoming = toWorkingOrder(event.data);
      const others = prev.orders.filter((o) => o.id !== incoming.id);
      const orders = TERMINAL_ORDER_STATUSES.includes(incoming.status)
        ? others
        : [...others, incoming];
      return { ...prev, orders };
    }

    case "parent": {
      const incoming = toExecutionParent(event.data);
      const others = prev.workingParents.filter((p) => p.parentId !== incoming.parentId);
      return { ...prev, workingParents: [...others, incoming] };
    }

    case "execution": {
      const incoming = toExecutionParent(event.data);
      const working = prev.workingParents.filter((p) => p.parentId !== incoming.parentId);
      const history = [incoming, ...prev.executionHistory].slice(0, 100);
      const aggregated = finalizeParentCloseTrade(parentClosePending, incoming.parentId);
      if (!aggregated || aggregated.pnl == null) {
        return {
          ...prev,
          workingParents: working,
          executionHistory: history,
          executionAggregate: aggregateExecutionHistory(history),
        };
      }
      const nextRealized = appendRealizedClose(
        prev.realizedTrades,
        aggregated,
        PERFORMANCE_TRADE_HISTORY_CAP,
      );
      let nextKpi = bumpKpiOnRealizedClose(prev.kpi, aggregated.pnl);
      nextKpi = rollingKpiFromRealized(nextRealized, nextKpi);
      return {
        ...prev,
        workingParents: working,
        executionHistory: history,
        executionAggregate: aggregateExecutionHistory(history),
        realizedTrades: nextRealized,
        kpi: nextKpi,
      };
    }

    case "log": {
      const log: LogEntry = {
        ts: fmtTime(event.ts),
        level: event.data.level,
        msg: event.data.msg ?? (event.data as { message?: string }).message ?? "",
        logger: event.data.logger,
      };
      return { ...prev, logs: [log, ...prev.logs] };
    }

    case "breaker": {
      const incoming = toBreakerStatus(event.data);
      const others = prev.breakers.active.filter(
        (b) => b.code !== incoming.code || b.target !== incoming.target,
      );
      const active =
        incoming.state === "armed"
          ? others
          : [...others.filter((b) => b.code !== incoming.code), incoming];
      const history =
        incoming.state === "armed"
          ? [incoming, ...prev.breakers.history.filter((b) => b.code !== incoming.code)].slice(
              0,
              80,
            )
          : prev.breakers.history;
      const activeCodes = active.map((b) => b.code);
      return {
        ...prev,
        breakers: { ...prev.breakers, active, history },
        systemHealth: prev.systemHealth
          ? { ...prev.systemHealth, activeBreakers: activeCodes }
          : prev.systemHealth,
      };
    }

    case "tick":
    default:
      return prev;
  }
}
