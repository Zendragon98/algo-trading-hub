// Hook that hydrates the dashboard from the backend and keeps it live
// over the WebSocket. Exposes the same shape the existing index.tsx
// already binds to, so the swap is mechanical.

import { useCallback, useEffect, useRef, useState } from "react";

import {
  api,
  getAlgoWsUrl,
  toBreakerList,
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

const EMPTY_BREAKERS: BreakerList = { active: [], history: [] };

/** Must match ``PerformanceTracker`` default ``history_size`` (engine). */
export const PERFORMANCE_TRADE_HISTORY_CAP = 200;

export type AlgoStream = {
  status: AlgoStatus;
  /** Engine boot progress (status === "starting"). */
  startupProgress: StartupProgress | null;
  /** L2 book REST resync after market WS reconnect (engine may still be running). */
  bookResyncProgress: StartupProgress | null;
  paperMode: boolean;
  uptimeSec: number;
  strategy: StrategyInfo | null;
  strategies: StrategyInfo[];
  equity: number[];
  positions: Position[];
  trades: Trade[];
  /** Closes with realized PnL — rolling window for win rate (engine-backed). */
  realizedTrades: Trade[];
  logs: LogEntry[];
  orders: WorkingOrder[];
  workingParents: ExecutionParent[];
  executionHistory: ExecutionParent[];
  executionAggregate: ExecutionAggregate;
  connected: boolean;
  /** False when REST hydrate fails or the stream socket is down (e.g. after Kill). */
  backendReachable: boolean;
  error: string | null;
  systemHealth: SystemHealth | null;
  maxRiskPct: number;
  maxGrossNotional: number;
  breakers: BreakerList;
  replaySummary: string | null;
  refresh: () => Promise<void>;
  /** Call after Kill / confirmed process exit so controls are not stuck on stale status. */
  markBackendOffline: (message?: string) => void;
  kpi: KpiDTO;
  /** Backend run directory for `events.wal.jsonl` + JSONL exports. */
  eventArchiveRunDir: string | null;
};

const EMPTY_AGG: ExecutionAggregate = {
  count: 0,
  avgSlippageBps: 0,
  avgImpactBps: 0,
  avgFillRatio: 0,
  avgDurationSec: 0,
  totalTradedNotional: 0,
};

const NOOP_REFRESH = async () => {};

const EMPTY_KPI: KpiDTO = {
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

function toStartupProgress(d: StartupProgressDTO): StartupProgress {
  return {
    phase: d.phase,
    label: d.label,
    done: d.done,
    total: d.total,
    symbol: d.symbol,
  };
}

const EMPTY: AlgoStream = {
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
  maxGrossNotional: 50_000,
  breakers: EMPTY_BREAKERS,
  replaySummary: null,
  refresh: NOOP_REFRESH,
  markBackendOffline: () => {},
  kpi: EMPTY_KPI,
  eventArchiveRunDir: null,
};

const TERMINAL_STATUSES: ReadonlyArray<WorkingOrder["status"]> = [
  "filled",
  "cancelled",
  "rejected",
  "expired",
];

/** Periodic REST hydrate so positions/orders stay aligned with the engine. */
const TRADING_STATE_SYNC_MS = 5_000;
/** Debounce rapid WS reconnects before pulling a full snapshot. */
const WS_RESYNC_DEBOUNCE_MS = 250;

const LATENCY_KEYS = new Set([
  "tick_to_signal_ms",
  "signal_to_risk_ms",
  "risk_to_submit_ms",
  "tick_to_submit_ms",
  "submit_to_ack_ms",
  "tick_to_ack_ms",
]);

function numSetting(settings: Record<string, unknown>, key: string, fallback: number): number {
  const v = settings[key];
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}

function formatReplaySummary(summary: NonNullable<StatusEventData["replay_summary"]>): string {
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

const BACKEND_OFFLINE_MSG =
  "Backend unreachable. After Kill, restart the API (e.g. python backend/main.py).";

function applyBackendOffline(prev: AlgoStream, message?: string): AlgoStream {
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

function applyTradingState(prev: AlgoStream, state: StateDTO): AlgoStream {
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

function systemHealthFromStatus(
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

export function useAlgoStream(): AlgoStream {
  const [stream, setStream] = useState<AlgoStream>(EMPTY);
  const wsRef = useRef<WebSocket | null>(null);
  const startedAtRef = useRef<number | null>(null);
  const syncTradingStateRef = useRef<(() => Promise<void>) | null>(null);
  const wsResyncTimerRef = useRef<number | null>(null);

  const markBackendOffline = useCallback((message?: string) => {
    setStream((prev) => applyBackendOffline(prev, message));
  }, []);

  const syncTradingState = useCallback(async () => {
    const state = await api.state();
    startedAtRef.current = Date.now() / 1000 - state.status.uptime_sec;
    setStream((prev) => applyTradingState(prev, state));
  }, []);

  syncTradingStateRef.current = syncTradingState;

  const refresh = useCallback(async () => {
    try {
      const [state, logs, settingsPayload, breakersDto] = await Promise.all([
        api.state(),
        api.logs(),
        api.getSettings(),
        api.listBreakers().catch(() => ({ active: [], history: [] })),
      ]);
      const settings = settingsPayload.settings;
      startedAtRef.current = Date.now() / 1000 - state.status.uptime_sec;
      setStream((prev) => ({
        ...applyTradingState(prev, state),
        logs,
        maxRiskPct: numSetting(settings, "max_risk_pct", 0.35),
        maxGrossNotional: numSetting(settings, "max_gross_notional", 50_000),
        breakers: toBreakerList(breakersDto),
        connected: prev.connected,
      }));
    } catch (err) {
      setStream((prev) => applyBackendOffline(prev, (err as Error).message));
      throw err;
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    let fallbackPoll: number | null = null;
    let tradingStatePoll: number | null = null;
    let equityPoll: number | null = null;

    const scheduleWsResync = () => {
      if (wsResyncTimerRef.current !== null) {
        window.clearTimeout(wsResyncTimerRef.current);
      }
      wsResyncTimerRef.current = window.setTimeout(() => {
        wsResyncTimerRef.current = null;
        if (cancelled) return;
        void syncTradingStateRef.current?.().catch((err: Error) => {
          if (!cancelled) {
            setStream((prev) => applyBackendOffline(prev, err.message));
          }
        });
      }, WS_RESYNC_DEBOUNCE_MS);
    };

    const ensureFallbackPoll = () => {
      if (fallbackPoll !== null) return;
      fallbackPoll = window.setInterval(() => {
        if (cancelled) return;
        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) return;
        void syncTradingStateRef.current?.().catch((err: Error) => {
          if (!cancelled) {
            setStream((prev) => applyBackendOffline(prev, err.message));
          }
        });
      }, TRADING_STATE_SYNC_MS);
    };

    const startTradingStatePoll = () => {
      if (tradingStatePoll !== null) return;
      tradingStatePoll = window.setInterval(() => {
        if (cancelled) return;
        void syncTradingStateRef.current?.().catch(() => {
          // WS may still be delivering; ignore transient poll errors
        });
      }, TRADING_STATE_SYNC_MS);
    };

    (async () => {
      try {
        await refresh();
        startTradingStatePoll();
      } catch {
        if (cancelled) return;
        try {
          await syncTradingStateRef.current?.();
        } catch {
          // keep original error; polls below will keep retrying
        }
        ensureFallbackPoll();
        startTradingStatePoll();
      }
    })();

    const ws = new WebSocket(getAlgoWsUrl());
    wsRef.current = ws;
    const syncEquityFromApi = async () => {
      if (cancelled) return;
      try {
        const eq = await api.equity();
        if (!eq.equity.length) return;
        const curve = eq.equity.length > 256 ? eq.equity.slice(-256) : eq.equity;
        setStream((prev) => {
          const prevLast = prev.equity[prev.equity.length - 1];
          const nextLast = curve[curve.length - 1];
          if (prev.equity.length === curve.length && prevLast === nextLast) {
            return prev;
          }
          return { ...prev, equity: curve };
        });
      } catch {
        // keep WS-driven updates; poll is a safety net
      }
    };

    const startEquityPoll = () => {
      if (equityPoll !== null) return;
      equityPoll = window.setInterval(() => void syncEquityFromApi(), 2000);
    };

    const onVisibility = () => {
      if (document.visibilityState !== "visible" || cancelled) return;
      scheduleWsResync();
      void syncEquityFromApi();
    };
    document.addEventListener("visibilitychange", onVisibility);

    ws.onopen = () => {
      setStream((prev) => ({ ...prev, connected: true, error: null, backendReachable: true }));
      scheduleWsResync();
      void syncEquityFromApi();
      startEquityPoll();
      startTradingStatePoll();
    };
    ws.onclose = () => {
      setStream((prev) => ({ ...prev, connected: false }));
      ensureFallbackPoll();
    };
    ws.onerror = () => {
      setStream((prev) => ({ ...prev, connected: false, error: prev.error ?? "ws error" }));
      ensureFallbackPoll();
    };

    ws.onmessage = (msg) => {
      let event: WsEvent;
      try {
        event = JSON.parse(msg.data) as WsEvent;
      } catch {
        return;
      }
      if (event.type === "status" && event.data.uptime_sec !== undefined) {
        startedAtRef.current = Date.now() / 1000 - event.data.uptime_sec;
      }
      setStream((prev) => applyEvent(prev, event));
    };

    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", onVisibility);
      ws.close();
      wsRef.current = null;
      if (wsResyncTimerRef.current !== null) {
        window.clearTimeout(wsResyncTimerRef.current);
        wsResyncTimerRef.current = null;
      }
      if (fallbackPoll !== null) {
        window.clearInterval(fallbackPoll);
        fallbackPoll = null;
      }
      if (tradingStatePoll !== null) {
        window.clearInterval(tradingStatePoll);
        tradingStatePoll = null;
      }
      if (equityPoll !== null) {
        window.clearInterval(equityPoll);
        equityPoll = null;
      }
    };
  }, [refresh]);

  useEffect(() => {
    const id = setInterval(() => {
      const startedAt = startedAtRef.current;
      if (startedAt === null) return;
      const upt = Math.max(0, Math.floor(Date.now() / 1000 - startedAt));
      setStream((prev) => (prev.uptimeSec === upt ? prev : { ...prev, uptimeSec: upt }));
    }, 1000);
    return () => clearInterval(id);
  }, []);

  return { ...stream, refresh, markBackendOffline };
}

function applyEvent(prev: AlgoStream, event: WsEvent): AlgoStream {
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
      // Match backend semantics: only treat as flat when qty/size are *explicitly*
      // zero-ish. Do not coerce missing ``size`` to 0 (that used to drop open rows).
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
      const d = event.data;
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
      const nextRealized = isRealizedClose
        ? [trade, ...prev.realizedTrades].slice(0, PERFORMANCE_TRADE_HISTORY_CAP)
        : prev.realizedTrades;
      let nextKpi = prev.kpi;
      if (isRealizedClose) {
        nextKpi = bumpKpiOnRealizedClose(prev.kpi, trade.pnl ?? 0);
        // Keep rolling KPI fields aligned with the realized close ring.
        const wins = nextRealized.filter((t) => (t.pnl ?? 0) > 0).length;
        const grossWin = nextRealized.reduce((a, t) => a + ((t.pnl ?? 0) > 0 ? (t.pnl ?? 0) : 0), 0);
        const grossLoss = nextRealized.reduce(
          (a, t) => a + ((t.pnl ?? 0) < 0 ? -(t.pnl ?? 0) : 0),
          0,
        );
        nextKpi = {
          ...nextKpi,
          win_rate: nextRealized.length > 0 ? (wins / nextRealized.length) * 100 : 0,
          gross_win_pnl: grossWin,
          gross_loss_pnl: grossLoss,
          profit_factor: grossLoss > 0 ? grossWin / grossLoss : null,
        };
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
      const orders = TERMINAL_STATUSES.includes(incoming.status)
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
      return {
        ...prev,
        workingParents: working,
        executionHistory: history,
        executionAggregate: aggregate(history),
      };
    }

    case "log": {
      const log: LogEntry = {
        ts: fmtTime(event.ts),
        level: event.data.level,
        msg: event.data.msg ?? (event.data as { message?: string }).message ?? "",
        logger: event.data.logger,
      };
      return { ...prev, logs: [log, ...prev.logs].slice(0, 200) };
    }

    case "breaker": {
      const incoming = toBreakerStatus(event.data);
      const others = prev.breakers.active.filter(
        (b) => b.code !== incoming.code || b.target !== incoming.target,
      );
      const active =
        incoming.state === "armed" ? others : [...others.filter((b) => b.code !== incoming.code), incoming];
      const history =
        incoming.state === "armed"
          ? [incoming, ...prev.breakers.history.filter((b) => b.code !== incoming.code)].slice(0, 80)
          : prev.breakers.history;
      const activeCodes = active.map((b) => b.code);
      return {
        ...prev,
        breakers: { active, history },
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

function bumpKpiOnRealizedClose(kpi: KpiDTO, pnl: number): KpiDTO {
  const wins = kpi.session_close_wins + (pnl > 0 ? 1 : 0);
  const losses = kpi.session_close_losses + (pnl < 0 ? 1 : 0);
  const breakevens = kpi.session_close_breakevens + (pnl === 0 ? 1 : 0);
  const closed = wins + losses + breakevens;
  const grossWin = kpi.gross_win_pnl_session + (pnl > 0 ? pnl : 0);
  const grossLoss = kpi.gross_loss_pnl_session + (pnl < 0 ? -pnl : 0);
  return {
    ...kpi,
    session_close_wins: wins,
    session_close_losses: losses,
    session_close_breakevens: breakevens,
    win_rate_session: closed > 0 ? (wins / closed) * 100 : 0,
    gross_win_pnl_session: grossWin,
    gross_loss_pnl_session: grossLoss,
    profit_factor_session: grossLoss > 0 ? grossWin / grossLoss : null,
  };
}

function aggregate(history: ExecutionParent[]): ExecutionAggregate {
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
