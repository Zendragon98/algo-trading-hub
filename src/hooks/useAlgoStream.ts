// Hook that hydrates the dashboard from the backend and keeps it live
// over the WebSocket. Exposes the same shape the existing index.tsx
// already binds to, so the swap is mechanical.

import { useEffect, useRef, useState } from "react";

import {
  api,
  toExecutionAggregate,
  toExecutionParent,
  toWorkingOrder,
  wsUrl,
  type StateDTO,
  type WsEvent,
} from "@/lib/api";
import type {
  AlgoStatus,
  ExecutionAggregate,
  ExecutionParent,
  LogEntry,
  Position,
  Trade,
  WorkingOrder,
} from "@/components/algo/mockData";

const fmtTime = (epoch?: number) => {
  const date = epoch ? new Date(epoch * 1000) : new Date();
  return date.toLocaleTimeString("en-GB", { hour12: false });
};

export type AlgoStream = {
  status: AlgoStatus;
  uptimeSec: number;
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
};

const EMPTY_AGG: ExecutionAggregate = {
  count: 0,
  avgSlippageBps: 0,
  avgImpactBps: 0,
  avgFillRatio: 0,
  avgDurationSec: 0,
  totalTradedNotional: 0,
};

const EMPTY: AlgoStream = {
  status: "stopped",
  uptimeSec: 0,
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
};

const TERMINAL_STATUSES: ReadonlyArray<WorkingOrder["status"]> = [
  "filled",
  "cancelled",
  "rejected",
];

export function useAlgoStream(): AlgoStream {
  const [stream, setStream] = useState<AlgoStream>(EMPTY);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const state: StateDTO = await api.state();
        const logs: LogEntry[] = await api.logs();
        if (cancelled) return;
        setStream((prev) => ({
          ...prev,
          status: state.status.status,
          uptimeSec: state.status.uptime_sec,
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
        if (!cancelled) {
          setStream((prev) => ({ ...prev, error: (err as Error).message }));
        }
      }
    })();

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;
    ws.onopen = () => setStream((prev) => ({ ...prev, connected: true, error: null }));
    ws.onclose = () => setStream((prev) => ({ ...prev, connected: false }));
    ws.onerror = () => setStream((prev) => ({ ...prev, error: "ws error" }));

    ws.onmessage = (msg) => {
      let event: WsEvent;
      try {
        event = JSON.parse(msg.data) as WsEvent;
      } catch {
        return;
      }
      setStream((prev) => applyEvent(prev, event));
    };

    return () => {
      cancelled = true;
      ws.close();
      wsRef.current = null;
    };
  }, []);

  return stream;
}

function applyEvent(prev: AlgoStream, event: WsEvent): AlgoStream {
  switch (event.type) {
    case "status":
      return { ...prev, status: event.data.status, uptimeSec: event.data.uptime_sec };

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
        id: event.data.child_id.slice(-8).toUpperCase(),
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
