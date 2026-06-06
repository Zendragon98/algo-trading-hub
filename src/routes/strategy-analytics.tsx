import { createFileRoute, Link } from "@tanstack/react-router";
import { ArrowLeft, BarChart3 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { StreamStatus, useLiveSystemHealth } from "@/components/algo/dashboard/health";
import { StrategyAnalyticsView } from "@/components/algo/strategy-hub/StrategyAnalyticsView";
import { useAlgoStream } from "@/hooks/useAlgoStream";
import { api, toStrategyHub } from "@/lib/api";
import type { StrategyHubSnapshot } from "@/components/algo/types";

export const Route = createFileRoute("/strategy-analytics")({
  component: StrategyAnalyticsPage,
});

const LOG_FALLBACK_POLL_MS = 5_000;

function StrategyAnalyticsPage() {
  const live = useAlgoStream();
  const { connected, loadStrategyHubLogs } = live;
  const [bootHub, setBootHub] = useState<StrategyHubSnapshot | null>(null);
  const hub = useMemo(() => {
    const base = live.strategyHub ?? bootHub;
    if (!base) return null;
    if (!base.logPath && bootHub?.logPath) {
      return { ...base, logPath: bootHub.logPath };
    }
    return base;
  }, [live.strategyHub, bootHub]);
  const systemHealth = useLiveSystemHealth(live.systemHealth, live.systemHealthAsOf);
  const equityCurveDelta =
    live.equityCurve.length >= 2
      ? live.equityCurve[live.equityCurve.length - 1]!.equity - live.equityCurve[0]!.equity
      : null;
  const [logError, setLogError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void api
      .strategyHub()
      .then((dto) => {
        if (!cancelled) setBootHub(toStrategyHub(dto));
      })
      .catch(() => {
        // WS + main-console stream may still populate hub
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    const loadLogs = async () => {
      try {
        await loadStrategyHubLogs();
        if (!cancelled) setLogError(null);
      } catch (err) {
        if (!cancelled) {
          setLogError(err instanceof Error ? err.message : "Failed to load strategy analytics log");
        }
      }
    };

    void loadLogs();
    if (connected)
      return () => {
        cancelled = true;
      };

    const timer = window.setInterval(() => void loadLogs(), LOG_FALLBACK_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [connected, loadStrategyHubLogs]);

  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground">
      <header className="sticky top-0 z-20 shrink-0 border-b border-border bg-background/90 backdrop-blur">
        <div className="mx-auto flex max-w-[1600px] items-center justify-between gap-4 px-4 py-3 lg:px-8">
          <div className="flex items-center gap-3">
            <Link
              to="/"
              className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
            >
              <ArrowLeft className="size-3" /> Live console
            </Link>
            <div className="flex items-center gap-2">
              <BarChart3 className="size-4 text-bull" />
              <span className="text-sm font-semibold tracking-wide">Strategy analytics</span>
            </div>
          </div>
          <div className="flex items-center gap-3 text-xs">
            <StreamStatus connected={live.connected} backendReachable={live.backendReachable} />
            <span className="hidden text-border md:inline">·</span>
            <div className="hidden items-center gap-2 md:flex">
              <Link to="/backtesting" className="text-muted-foreground hover:text-foreground">
                Backtest
              </Link>
              <span className="text-border">·</span>
              <Link to="/settings" className="text-muted-foreground hover:text-foreground">
                Settings
              </Link>
            </div>
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-[1600px] flex-1 px-4 py-6 lg:px-8">
        <StrategyAnalyticsView
          hub={hub}
          logLines={live.strategyHubLogLines}
          logError={logError}
          systemHealth={systemHealth}
          equityCurveDelta={equityCurveDelta}
        />
      </main>
    </div>
  );
}
