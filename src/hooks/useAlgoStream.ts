// WebSocket + polling hook for the live console. Pure state reducers live in
// `lib/algoStreamState.ts`. Mount once at the app root via `AlgoStreamProvider`.

import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  startTransition,
  type ReactNode,
} from "react";

import { api, getAlgoWsUrl, toBreakerList, type WsEvent } from "@/lib/api";
import { mergeStrategyHubLogLines, STRATEGY_HUB_LOG_CAP } from "@/lib/strategyHubLog";
import {
  applyBackendOffline,
  applyTradingState,
  applyWsEvents,
  createEmptyAlgoStream,
  isUrgentWsEvent,
  numSetting,
  TRADING_STATE_SYNC_MS,
  WS_EVENT_BATCH_MS,
  WS_RESYNC_DEBOUNCE_MS,
  type AlgoStream,
} from "@/lib/algoStreamState";
import type { PendingParentClose } from "@/lib/parentCloseKpi";

export type { AlgoStream } from "@/lib/algoStreamState";
export { PERFORMANCE_TRADE_HISTORY_CAP } from "@/lib/algoStreamState";

/** Skip redundant GET /api/state right after a full hydrate. */
const HYDRATE_RESYNC_GRACE_MS = 4_000;

const AlgoStreamContext = createContext<AlgoStream | null>(null);

function useAlgoStreamInternal(): AlgoStream {
  const [stream, setStream] = useState<AlgoStream>(createEmptyAlgoStream);
  const wsRef = useRef<WebSocket | null>(null);
  const lastHydrateRef = useRef(0);
  const syncTradingStateRef = useRef<(() => Promise<void>) | null>(null);
  const wsResyncTimerRef = useRef<number | null>(null);
  const parentClosePendingRef = useRef(new Map<string, PendingParentClose>());

  const markBackendOffline = useCallback((message?: string) => {
    setStream((prev) => applyBackendOffline(prev, message));
  }, []);

  const loadStrategyHubLogs = useCallback(async () => {
    const logDto = await api.strategyHubLog(STRATEGY_HUB_LOG_CAP);
    setStream((prev) => {
      const lines = mergeStrategyHubLogLines(prev.strategyHubLogLines, logDto.lines);
      if (
        prev.strategyHubLogLines.length === lines.length &&
        prev.strategyHubLogLines[0]?.ts === lines[0]?.ts &&
        prev.strategyHubLogLines[lines.length - 1]?.ts === lines[lines.length - 1]?.ts
      ) {
        return prev;
      }
      return { ...prev, strategyHubLogLines: lines };
    });
  }, []);

  const syncTradingState = useCallback(async () => {
    const [state, logs] = await Promise.all([api.state(), api.logs(80)]);
    lastHydrateRef.current = Date.now();
    setStream((prev) => {
      const next = applyTradingState(
        { ...prev, hydrated: true },
        state,
        parentClosePendingRef.current,
      );
      const logsFingerprint = logs.length
        ? `${logs.length}:${logs[0]?.ts}:${logs[logs.length - 1]?.ts}`
        : "";
      const prevLogsFingerprint = prev.logs.length
        ? `${prev.logs.length}:${prev.logs[0]?.ts}:${prev.logs[prev.logs.length - 1]?.ts}`
        : "";
      if (next === prev && logsFingerprint === prevLogsFingerprint) return prev;
      return { ...next, logs };
    });
  }, []);

  syncTradingStateRef.current = syncTradingState;

  const refresh = useCallback(async () => {
    try {
      const state = await api.state();
      lastHydrateRef.current = Date.now();

      startTransition(() => {
        setStream((prev) => ({
          ...applyTradingState(
            { ...prev, hydrated: true },
            state,
            parentClosePendingRef.current,
          ),
          connected: prev.connected,
        }));
      });

      const [logs, settingsPayload, breakersDto] = await Promise.all([
        api.logs(),
        api.getSettings(),
        api.listBreakers().catch(() => ({ active: [], history: [] })),
      ]);
      const settings = settingsPayload.settings;

      startTransition(() => {
        setStream((prev) => ({
          ...prev,
          logs,
          maxRiskPct: numSetting(settings, "max_risk_pct", 0.35),
          maxGrossNotional: numSetting(settings, "max_gross_notional", 100_000),
          settingsSnapshot: settings,
          breakers: toBreakerList(breakersDto),
        }));
      });
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
      if (Date.now() - lastHydrateRef.current < HYDRATE_RESYNC_GRACE_MS) {
        return;
      }
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
        if (!cancelled) startTradingStatePoll();
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
      // WS down: pull full state (not just the curve) so KPI/positions/OMS stay aligned.
      try {
        await syncTradingStateRef.current?.();
      } catch {
        // fallback poll will keep retrying
      }
    };

    const stopEquityPoll = () => {
      if (equityPoll === null) return;
      window.clearInterval(equityPoll);
      equityPoll = null;
    };

    const startEquityPoll = () => {
      if (equityPoll !== null) return;
      equityPoll = window.setInterval(() => void syncEquityFromApi(), 5000);
    };

    const onVisibility = () => {
      if (document.visibilityState !== "visible" || cancelled) return;
      scheduleWsResync();
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
        void syncEquityFromApi();
      }
    };
    document.addEventListener("visibilitychange", onVisibility);

    ws.onopen = () => {
      setStream((prev) => ({ ...prev, connected: true, error: null, backendReachable: true }));
      scheduleWsResync();
      stopEquityPoll();
      startTradingStatePoll();
    };
    ws.onclose = () => {
      setStream((prev) => ({ ...prev, connected: false }));
      ensureFallbackPoll();
      startEquityPoll();
    };
    ws.onerror = () => {
      setStream((prev) => ({ ...prev, connected: false, error: prev.error ?? "ws error" }));
      ensureFallbackPoll();
      startEquityPoll();
    };

    const wsQueueRef: WsEvent[] = [];
    let wsFlushRaf: number | null = null;
    let wsFlushTimer: number | null = null;

    const flushWsQueue = () => {
      wsFlushRaf = null;
      if (wsFlushTimer !== null) {
        window.clearTimeout(wsFlushTimer);
        wsFlushTimer = null;
      }
      if (cancelled || wsQueueRef.length === 0) return;
      const batch = wsQueueRef.splice(0);
      startTransition(() => {
        setStream((prev) => applyWsEvents(prev, batch, parentClosePendingRef.current));
      });
    };

    const scheduleWsFlush = (urgent: boolean) => {
      if (cancelled) return;
      if (urgent) {
        if (wsFlushTimer !== null) {
          window.clearTimeout(wsFlushTimer);
          wsFlushTimer = null;
        }
        if (wsFlushRaf === null) {
          wsFlushRaf = window.requestAnimationFrame(flushWsQueue);
        }
        return;
      }
      if (wsFlushRaf !== null || wsFlushTimer !== null) return;
      wsFlushTimer = window.setTimeout(flushWsQueue, WS_EVENT_BATCH_MS);
    };

    const enqueueWsEvent = (event: WsEvent) => {
      wsQueueRef.push(event);
      scheduleWsFlush(isUrgentWsEvent(event));
    };

    ws.onmessage = (msg) => {
      let event: WsEvent;
      try {
        event = JSON.parse(msg.data) as WsEvent;
      } catch {
        return;
      }
      enqueueWsEvent(event);
    };

    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", onVisibility);
      ws.close();
      wsRef.current = null;
      wsQueueRef.length = 0;
      if (wsFlushRaf !== null) {
        window.cancelAnimationFrame(wsFlushRaf);
        wsFlushRaf = null;
      }
      if (wsFlushTimer !== null) {
        window.clearTimeout(wsFlushTimer);
        wsFlushTimer = null;
      }
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
      stopEquityPoll();
    };
  }, [refresh]);

  return { ...stream, refresh, markBackendOffline, loadStrategyHubLogs };
}

export function AlgoStreamProvider({ children }: { children: ReactNode }) {
  const stream = useAlgoStreamInternal();
  return createElement(AlgoStreamContext.Provider, { value: stream }, children);
}

export function useAlgoStream(): AlgoStream {
  const stream = useContext(AlgoStreamContext);
  if (!stream) {
    throw new Error("useAlgoStream must be used within AlgoStreamProvider");
  }
  return stream;
}
