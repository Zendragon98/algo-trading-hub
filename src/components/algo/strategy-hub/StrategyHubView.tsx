import { cn } from "@/lib/utils";
import { AnalyticsRows } from "@/components/algo/dashboard/strategy-analytics";
import type { StrategyHubSnapshot } from "@/components/algo/types";

function pnlTone(value: number): string {
  if (value > 0) return "text-bull";
  if (value < 0) return "text-bear";
  return "text-muted-foreground";
}

function formatUsd(value: number): string {
  const sign = value >= 0 ? "+" : "";
  return `${sign}$${value.toFixed(4)}`;
}

function StrategyCard({
  row,
  analytics,
}: {
  row: StrategyHubSnapshot["strategies"][number];
  analytics: Record<string, string | number | boolean | null> | undefined;
}) {
  return (
    <div className="overflow-hidden rounded-sm border border-border bg-card/60">
      <div className="border-b border-border px-4 py-3">
        <div className="text-sm font-semibold tracking-tight">{row.label}</div>
        <div className="mt-2 grid grid-cols-3 gap-2 text-[10px] uppercase tracking-wider text-muted-foreground">
          <div>
            <div>Total</div>
            <div className={cn("mt-0.5 font-mono text-sm tabular-nums", pnlTone(row.totalPnl))}>
              {formatUsd(row.totalPnl)}
            </div>
          </div>
          <div>
            <div>Realized</div>
            <div className={cn("mt-0.5 font-mono text-sm tabular-nums", pnlTone(row.realizedPnl))}>
              {formatUsd(row.realizedPnl)}
            </div>
          </div>
          <div>
            <div>Open</div>
            <div className={cn("mt-0.5 font-mono text-sm tabular-nums", pnlTone(row.unrealizedPnl))}>
              {formatUsd(row.unrealizedPnl)}
            </div>
          </div>
        </div>
      </div>
      {analytics ? (
        <AnalyticsRows rows={analytics} />
      ) : (
        <p className="px-4 py-4 text-xs text-muted-foreground">No analytics telemetry yet.</p>
      )}
      {row.openLegs.length > 0 ? (
        <div className="border-t border-border">
          <div className="bg-muted/20 px-4 py-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            Attributed legs
          </div>
          <div className="divide-y divide-border">
            {row.openLegs.map((leg) => (
              <div
                key={`${row.name}-${leg.symbol}`}
                className="grid grid-cols-[1fr_auto_auto] gap-3 px-4 py-2 text-xs"
              >
                <span className="font-mono">{leg.symbol}</span>
                <span className="uppercase text-muted-foreground">{leg.side}</span>
                <span className={cn("font-mono tabular-nums", pnlTone(leg.unrealizedPnl))}>
                  {formatUsd(leg.unrealizedPnl)}
                </span>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function StrategyHubView({
  hub,
  logLines,
  error,
}: {
  hub: StrategyHubSnapshot | null;
  logLines: Array<Record<string, unknown>>;
  error: string | null;
}) {
  if (error) {
    return (
      <div className="rounded-sm border border-bear/40 bg-bear/10 px-4 py-3 text-sm text-bear">
        {error}
      </div>
    );
  }
  if (!hub) {
    return (
      <div className="rounded-sm border border-border bg-card/60 px-4 py-8 text-center text-sm text-muted-foreground">
        Loading strategy hub…
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
        <span>
          Mode: <span className="font-mono text-foreground">{hub.mode}</span>
        </span>
        <span>
          Strategies: <span className="font-mono text-foreground">{hub.strategies.length}</span>
        </span>
        {hub.runDir ? (
          <span className="truncate">
            Run: <span className="font-mono text-foreground/80">{hub.runDir}</span>
          </span>
        ) : null}
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {hub.strategies.map((row) => (
          <StrategyCard
            key={row.name}
            row={row}
            analytics={hub.analytics[row.name]}
          />
        ))}
      </div>

      <div className="overflow-hidden rounded-sm border border-border bg-card/60">
        <div className="border-b border-border px-4 py-3 text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
          Strategy hub log
          {hub.logPath ? (
            <span className="ml-2 font-mono normal-case tracking-normal text-foreground/70">
              {hub.logPath}
            </span>
          ) : null}
        </div>
        {logLines.length === 0 ? (
          <p className="px-4 py-6 text-xs text-muted-foreground">
            No snapshots written yet. Log entries appear when PnL or analytics change materially.
          </p>
        ) : (
          <pre className="max-h-80 overflow-auto p-4 font-mono text-[11px] leading-relaxed text-foreground/90">
            {logLines.map((line, i) => (
              <div key={i} className="border-b border-border/40 py-2 last:border-0">
                {JSON.stringify(line, null, 2)}
              </div>
            ))}
          </pre>
        )}
      </div>
    </div>
  );
}
