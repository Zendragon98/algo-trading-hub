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
  toStrategyInfo,
  toSystemHealth,
  toTrade,
  toWorkingOrder,
  type StateDTO,
  type StatusEventData,
  type WsEvent,
} from "@/lib/api";
import type {
  AlgoStatus,
  BreakerList,
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

export type AlgoStream = {
  status: AlgoStatus;
  paperMode: boolean;
  uptimeSec: number;
  strategy: StrategyInfo | null;
  strategies: StrategyInfo[];
  equity: number[];
  positions: Position[];
  trades: Trade[];
  logs: LogEntry[];
  orders: WorkingOrder[];
  workingParents: ExecutionParent[];
  executionHistory: ExecutionParent[];
  executionAggregate: ExecutionAggregate;
  connected: boolean;
  error: string | null;
  systemHealth: SystemHealth | null;
  maxRiskPct: number;
  maxGrossNotional: number;
  breakers: BreakerList;
  replaySummary: string | null;
  refresh: () => Promise<void>;
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

const EMPTY: AlgoStream = {
  status: "stopped",
  paperMode: false,
  uptimeSec: 0,
  strategy: null,
  strategies: [],
  equity: [],
  positions: [],
  trades: [],
  logs: [],
  orders: [],
  workingParents: [],
  executionHistory: [],
  executionAggregate: EMPTY_AGG,
  connected: false,
  error: null,
  systemHealth: null,
  maxRiskPct: 0.35,
  maxGrossNotional: 50_000,
  breakers: EMPTY_BREAKERS,
  replaySummary: null,
  refresh: NOOP_REFRESH,
};

const TERMINAL_STATUSES: ReadonlyArray<WorkingOrder["status"]> = [
  "filled",
  "cancelled",
  "rejected",
  "expired",
];

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
        ...prev,
        status: state.status.status,
        paperMode: state.status.paper_mode,
        uptimeSec: Math.floor(state.status.uptime_sec),
        strategy: state.strategy ? toStrategyInfo(state.strategy) : null,
        strategies: state.strategies.map(toStrategyInfo),
        equity: state.equity.equity,
        positions: state.positions,
        trades: state.trades.map(toTrade),
        logs,
        orders: state.orders.working.map(toWorkingOrder),
        workingParents: state.execution.working.map(toExecutionParent),
        executionHistory: state.execution.history.map(toExecutionParent),
        executionAggregate: toExecutionAggregate(state.execution.aggregate),
        systemHealth: state.system_health ? toSystemHealth(state.system_health) : null,
        maxRiskPct: numSetting(settings, "max_risk_pct", 0.35),
        maxGrossNotional: numSetting(settings, "max_gross_notional", 50_000),
        breakers: toBreakerList(breakersDto),
        error: null,
      }));
    } catch (err) {
      setStream((prev) => ({ ...prev, error: (err as Error).message }));
      throw err;
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    let statusPoll: number | null = null;
    let equityPoll: number | null = null;

    const publishStatus = (next: { status: AlgoStatus; uptime_sec: number; paper_mode?: boolean }) => {
      startedAtRef.current = Date.now() / 1000 - next.uptime_sec;
      setStream((prev) => ({
        ...prev,
        status: next.status,
        uptimeSec: Math.floor(next.uptime_sec),
        paperMode: next.paper_mode ?? prev.paperMode,
      }));
    };

    const ensureStatusPoll = () => {
      if (statusPoll !== null) return;
      statusPoll = window.setInterval(async () => {
        if (cancelled) return;
        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) return;
        try {
          const st = await api.status();
          if (!cancelled) publishStatus(st);
        } catch (err) {
          if (!cancelled) {
            setStream((prev) => ({ ...prev, error: (err as Error).message }));
          }
        }
      }, 2500);
    };

    (async () => {
      try {
        await refresh();
        startEquityPoll();
      } catch {
        if (cancelled) return;
        try {
          const st = await api.status();
          if (!cancelled) publishStatus(st);
        } catch {
          // keep original error; the poll below will keep retrying
        }
        ensureStatusPoll();
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

    ws.onopen = () => {
      setStream((prev) => ({ ...prev, connected: true, error: null }));
      void syncEquityFromApi();
      startEquityPoll();
    };
    ws.onclose = () => {
      setStream((prev) => ({ ...prev, connected: false }));
      ensureStatusPoll();
    };
    ws.onerror = () => {
      setStream((prev) => ({ ...prev, error: "ws error" }));
      ensureStatusPoll();
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
      ws.close();
      wsRef.current = null;
      if (statusPoll !== null) {
        window.clearInterval(statusPoll);
        statusPoll = null;
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

  return { ...stream, refresh };
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
        };
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
      const next = [...prev.equity, point];
      return { ...prev, equity: next.length > 256 ? next.slice(-256) : next };
    }

    case "position": {
      const incoming = event.data;
      const others = prev.positions.filter((p) => p.symbol !== incoming.symbol);
      if ((incoming as { qty?: number }).qty === 0 || incoming.size === 0) {
        return { ...prev, positions: others };
      }
      return {
        ...prev,
        positions: [
          ...others,
          {
            symbol: incoming.symbol,
            side: (incoming.side === "long" ? "long" : "short") as Position["side"],
            size: incoming.size,
            entry: (incoming as { avg_entry_price?: number }).avg_entry_price ?? incoming.entry,
            mark: (incoming as { mark_price?: number }).mark_price ?? incoming.mark,
          },
        ],
      };
    }

    case "fill": {
      const d = event.data;
      const trade = toTrade({
        id: d.id ?? d.trade_id ?? d.child_id,
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
      return { ...prev, trades: [trade, ...prev.trades].slice(0, 60) };
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
        msg: event.data.msg,
      };
      return { ...prev, logs: [log, ...prev.logs].slice(0, 80) };
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
