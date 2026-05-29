// WebSocket + polling hook for the live console. Pure state reducers live in
// `lib/algoStreamState.ts`.

import { useCallback, useEffect, useRef, useState } from "react";

import { api, getAlgoWsUrl, toBreakerList, type WsEvent } from "@/lib/api";
import {
  applyBackendOffline,
  applyTradingState,
  applyWsEvent,
  createEmptyAlgoStream,
  numSetting,
  TRADING_STATE_SYNC_MS,
  WS_RESYNC_DEBOUNCE_MS,
  type AlgoStream,
} from "@/lib/algoStreamState";
import type { PendingParentClose } from "@/lib/parentCloseKpi";

export type { AlgoStream } from "@/lib/algoStreamState";
export { PERFORMANCE_TRADE_HISTORY_CAP } from "@/lib/algoStreamState";

export function useAlgoStream(): AlgoStream {
  const [stream, setStream] = useState<AlgoStream>(createEmptyAlgoStream);
  const wsRef = useRef<WebSocket | null>(null);
  const startedAtRef = useRef<number | null>(null);
  const syncTradingStateRef = useRef<(() => Promise<void>) | null>(null);
  const wsResyncTimerRef = useRef<number | null>(null);
  const parentClosePendingRef = useRef(new Map<string, PendingParentClose>());

  const markBackendOffline = useCallback((message?: string) => {
    setStream((prev) => applyBackendOffline(prev, message));
  }, []);

  const syncTradingState = useCallback(async () => {
    const state = await api.state();
    startedAtRef.current = Date.now() / 1000 - state.status.uptime_sec;
    setStream((prev) => applyTradingState(prev, state, parentClosePendingRef.current));
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
        ...applyTradingState(prev, state, parentClosePendingRef.current),
        logs,
        maxRiskPct: numSetting(settings, "max_risk_pct", 0.35),
        maxGrossNotional: numSetting(settings, "max_gross_notional", 100_000),
        settingsSnapshot: settings,
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
      setStream((prev) => applyWsEvent(prev, event, parentClosePendingRef.current));
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
