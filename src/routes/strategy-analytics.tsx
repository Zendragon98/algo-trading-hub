import { createFileRoute, Link } from "@tanstack/react-router";
import { ArrowLeft, BarChart3 } from "lucide-react";
import { useEffect, useState } from "react";

import { StrategyAnalyticsView } from "@/components/algo/strategy-hub/StrategyAnalyticsView";
import { useAlgoStream } from "@/hooks/useAlgoStream";
import { api, toStrategyHub } from "@/lib/api";
import type { StrategyHubSnapshot } from "@/components/algo/types";

export const Route = createFileRoute("/strategy-analytics")({
  component: StrategyAnalyticsPage,
});

const LOG_POLL_MS = 5_000;

function StrategyAnalyticsPage() {
  const live = useAlgoStream();
  const [bootHub, setBootHub] = useState<StrategyHubSnapshot | null>(null);
  const hub = live.strategyHub ?? bootHub;
  const equityCurveDelta =
    live.equityCurve.length >= 2
      ? live.equityCurve[live.equityCurve.length - 1]!.equity - live.equityCurve[0]!.equity
      : null;
  const [logLines, setLogLines] = useState<Array<Record<string, unknown>>>([]);
  const [error, setError] = useState<string | null>(null);

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

    const refreshLogs = async () => {
      try {
        const logDto = await api.strategyHubLog(20);
        if (cancelled) return;
        setLogLines(logDto.lines);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load strategy analytics log");
      }
    };

    void refreshLogs();
    const timer = window.setInterval(() => void refreshLogs(), LOG_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    if (live.backendReachable && live.error) {
      setError(live.error);
    }
  }, [live.backendReachable, live.error]);

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
          <div className="hidden items-center gap-2 text-xs md:flex">
            <Link to="/backtesting" className="text-muted-foreground hover:text-foreground">
              Backtest
            </Link>
            <span className="text-border">·</span>
            <Link to="/settings" className="text-muted-foreground hover:text-foreground">
              Settings
            </Link>
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-[1600px] flex-1 px-4 py-6 lg:px-8">
        <StrategyAnalyticsView
          hub={hub}
          logLines={logLines}
          error={error}
          systemHealth={live.systemHealth}
          equityCurveDelta={equityCurveDelta}
        />
      </main>
    </div>
  );
}
