import { BarChart3 } from "lucide-react";

import { cn } from "@/lib/utils";
import type { StrategyAnalytics } from "@/components/algo/types";

function formatValue(value: string | number | boolean | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(4);
  return String(value);
}

function signalTone(signal: string | undefined): string {
  if (!signal) return "text-foreground";
  const upper = signal.toUpperCase();
  if (upper.includes("LONG") || upper.includes("BUY")) return "text-bull";
  if (upper.includes("SHORT") || upper.includes("SELL")) return "text-bear";
  if (upper === "HOLD" || upper === "WARMUP") return "text-muted-foreground";
  return "text-foreground";
}

export function AnalyticsRows({ rows }: { rows: Record<string, string | number | boolean | null> }) {
  const entries = Object.entries(rows).filter(([key]) => key !== "STRATEGY");
  if (!entries.length) {
    return (
      <p className="px-4 py-6 text-xs text-muted-foreground">
        No live strategy diagnostics yet.
      </p>
    );
  }
  return (
    <dl className="divide-y divide-border">
      {entries.map(([key, value]) => (
        <div key={key} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)] gap-3 px-4 py-2.5">
          <dt className="text-[10px] uppercase tracking-[0.16em] text-muted-foreground">{key}</dt>
          <dd
            className={cn(
              "font-mono text-sm tabular-nums",
              key === "SIGNAL" ? signalTone(String(value ?? "")) : "text-foreground",
            )}
          >
            {formatValue(value)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function StrategyAnalyticsPanel({
  analytics,
  className,
}: {
  analytics: StrategyAnalytics;
  className?: string;
}) {
  const groups = Object.entries(analytics);
  const primary = groups.length === 1 ? groups[0] : null;

  return (
    <div
      className={cn(
        "overflow-hidden rounded-sm border border-border bg-card/60",
        className,
      )}
    >
      <div className="flex items-center justify-between border-b border-border px-4 py-3 text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
        <span className="flex items-center gap-1.5 font-mono">
          <BarChart3 className="size-4" strokeWidth={2} />
          Strategy analytics
        </span>
      </div>
      {groups.length === 0 ? (
        <p className="px-4 py-6 text-xs text-muted-foreground">
          Waiting for strategy telemetry…
        </p>
      ) : primary ? (
        <AnalyticsRows rows={primary[1]} />
      ) : (
        <div className="divide-y divide-border">
          {groups.map(([name, rows]) => (
            <div key={name}>
              <div className="bg-muted/20 px-4 py-2 text-[10px] font-semibold uppercase tracking-wider text-foreground/80">
                {String(rows.STRATEGY ?? name)}
              </div>
              <AnalyticsRows rows={rows} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
