import { useCallback, useEffect, useState } from "react";

import type {
  BacktestDataset,
  BacktestResult,
  BacktestSession,
} from "@/components/algo/types";
import { api, pollAnalyticsJob, toBacktestDataset, toBacktestResult } from "@/lib/api";

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
        const accepted = await api.backtestDownload({ symbols, interval: "1m", days });
        const job = await pollAnalyticsJob(accepted.job_id);
        if (job.status === "failed") {
          throw new Error(job.error ?? "Download failed");
        }
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
        const accepted = await api.backtestRun({
          strategy: params.strategy,
          dataset: params.dataset,
          settings_overrides: params.settingsOverrides,
        });
        const job = await pollAnalyticsJob(accepted.job_id);
        if (job.status === "failed") {
          throw new Error(job.error ?? "Backtest failed");
        }
        const runId = job.result?.run_id;
        if (typeof runId !== "string" || !runId) {
          throw new Error("Backtest finished without run_id");
        }
        const raw = await api.backtestRunById(runId);
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
