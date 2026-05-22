import { useCallback, useEffect, useState } from "react";

import type {
  BacktestDataset,
  BacktestResult,
  BacktestSession,
} from "@/components/algo/types";
import { api, toBacktestDataset, toBacktestResult } from "@/lib/api";

export function useBacktest() {
  const [datasets, setDatasets] = useState<BacktestDataset[]>([]);
  const [sessions, setSessions] = useState<BacktestSession[]>([]);
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const [ds, sess] = await Promise.all([
        api.backtestDatasets().then((rows) => rows.map(toBacktestDataset)),
        api.backtestSessions().then((rows) =>
          rows.map((r) => ({ runId: r.run_id, label: r.label })),
        ),
      ]);
      setDatasets(ds);
      setSessions(sess);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load datasets");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const download = useCallback(
    async (symbols: string[], days: number) => {
      setLoading(true);
      setError(null);
      try {
        await api.backtestDownload({ symbols, interval: "1m", days });
        await refresh();
      } catch (e) {
        setError(e instanceof Error ? e.message : "Download failed");
      } finally {
        setLoading(false);
      }
    },
    [refresh],
  );

  const run = useCallback(
    async (params: {
      strategy: string;
      dataset: string;
      settingsOverrides?: Record<string, unknown>;
    }) => {
      setLoading(true);
      setError(null);
      try {
        const raw = await api.backtestRun({
          strategy: params.strategy,
          dataset: params.dataset,
          settings_overrides: params.settingsOverrides,
        });
        setResult(toBacktestResult(raw));
      } catch (e) {
        setError(e instanceof Error ? e.message : "Backtest failed");
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  return {
    datasets,
    sessions,
    result,
    loading,
    error,
    refresh,
    download,
    run,
  };
}
