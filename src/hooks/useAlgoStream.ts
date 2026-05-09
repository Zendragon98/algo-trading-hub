// Hook that hydrates the dashboard from the backend and keeps it live
// over the WebSocket. Exposes the same shape the existing index.tsx
// already binds to, so the swap is mechanical.

import { useCallback, useEffect, useRef, useState } from "react";

import {
  api,
  getAlgoWsUrl,
  toExecutionAggregate,
  toExecutionParent,
  toStrategyInfo,
  toWorkingOrder,
  type StateDTO,
  type WsEvent,
} from "@/lib/api";
import type {
  AlgoStatus,
  ExecutionAggregate,
  ExecutionParent,
  LogEntry,
  Position,
  StrategyInfo,
  Trade,
  WorkingOrder,
} from "@/components/algo/types";

const fmtTime = (epoch?: number) => {
  const date = epoch ? new Date(epoch * 1000) : new Date();
  return date.toLocaleTimeString("en-GB", { hour12: false });
};

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
  // Force a full /api/state re-hydrate. Used by the dashboard after a
  // strategy hot-swap so the panel reflects the new active strategy
  // without waiting for the next periodic status poll.
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
  refresh: NOOP_REFRESH,
};

const TERMINAL_STATUSES: ReadonlyArray<WorkingOrder["status"]> = [
  "filled",
  "cancelled",
  "rejected",
];

export function useAlgoStream(): AlgoStream {
  const [stream, setStream] = useState<AlgoStream>(EMPTY);
  const wsRef = useRef<WebSocket | null>(null);
  // Client-clock epoch (seconds) at which the engine reports itself as
  // having started. Computed once per authoritative status update as
  // `now - uptime_sec`; the local 1Hz ticker derives display uptime
  // from this so we never have to wait for the backend to republish.
  const startedAtRef = useRef<number | null>(null);

  // Pull /api/state + /api/logs and merge into the stream. Stable
  // identity via useCallback so consumers passing it to event handlers
  // don't burn extra renders.
  const refresh = useCallback(async () => {
    try {
      const state: StateDTO = await api.state();
      const logs: LogEntry[] = await api.logs();
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
        trades: state.trades,
        logs,
        orders: state.orders.working.map(toWorkingOrder),
        workingParents: state.execution.working.map(toExecutionParent),
        executionHistory: state.execution.history.map(toExecutionParent),
        executionAggregate: toExecutionAggregate(state.execution.aggregate),
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

    const publishStatus = (next: { status: AlgoStatus; uptime_sec: number; paper_mode?: boolean }) => {
      startedAtRef.current = Date.now() / 1000 - next.uptime_sec;
      setStream((prev) => ({
        ...prev,
        status: next.status,
        uptimeSec: Math.floor(next.uptime_sec),
        paperMode: next.paper_mode ?? prev.paperMode,
      }));
    };

    // Poll status as a fallback when /ws is unavailable. This keeps the control
    // buttons correct even if the websocket is blocked by network policy.
    const ensureStatusPoll = () => {
      if (statusPoll !== null) return;
      statusPoll = window.setInterval(async () => {
        if (cancelled) return;
        // If the websocket is healthy, let it be the source of truth.
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
      } catch {
        if (cancelled) return;
        // If the full hydrate fails (CORS, proxy, backend cold start), still
        // attempt to retrieve the lightweight status so controls remain usable.
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
    ws.onopen = () => setStream((prev) => ({ ...prev, connected: true, error: null }));
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
      if (event.type === "status") {
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
    };
  }, []);

  // Local 1Hz ticker so the topbar uptime updates every second without
  // the backend having to spam STATUS events. We only re-render when
  // the integer second actually changes to keep React work bounded.
  useEffect(() => {
    const id = setInterval(() => {
      const startedAt = startedAtRef.current;
      if (startedAt === null) return;
      const upt = Math.max(0, Math.floor(Date.now() / 1000 - startedAt));
      setStream((prev) => (prev.uptimeSec === upt ? prev : { ...prev, uptimeSec: upt }));
    }, 1000);
    return () => clearInterval(id);
  }, []);

  // Always re-bind the live ``refresh`` callback so consumers never see
  // the no-op fallback baked into ``EMPTY``.
  return { ...stream, refresh };
}

function applyEvent(prev: AlgoStream, event: WsEvent): AlgoStream {
  switch (event.type) {
    case "status":
      return {
        ...prev,
        status: event.data.status,
        uptimeSec: Math.floor(event.data.uptime_sec),
      };

    case "equity": {
      const point = event.data.equity;
      const next = [...prev.equity, point];
      // Keep ~256 samples to match the engine's curve buffer.
      return { ...prev, equity: next.length > 256 ? next.slice(-256) : next };
    }

    case "position": {
      const incoming = event.data;
      const others = prev.positions.filter((p) => p.symbol !== incoming.symbol);
      // The engine emits flat positions; drop them so the table stays clean.
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
      const trade: Trade = {
        id: event.data.child_id,
        ts: fmtTime(event.ts),
        symbol: event.data.symbol,
        side: event.data.side,
        qty: event.data.qty,
        price: event.data.price,
        pnl: null,
      };
      return { ...prev, trades: [trade, ...prev.trades].slice(0, 60) };
    }

    case "order": {
      const incoming = toWorkingOrder(event.data);
      // Drop terminal orders from the working set; let the audit trail
      // live in the trades panel and the parent's execution report.
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
      // Move from working -> history and recompute the rolling aggregate.
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

    case "tick":
    default:
      return prev;
  }
}

// Lightweight client-side aggregate so the panel updates instantly when
// a new execution report arrives, without waiting for the next REST poll.
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
