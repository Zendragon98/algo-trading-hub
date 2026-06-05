import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  BarChart3,
  Minus,
  TrendingDown,
  TrendingUp,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Panel } from "@/components/algo/dashboard/primitives";
import type { StrategyAnalytics, StrategyHubSnapshot, SystemHealth } from "@/components/algo/types";
import { EM_DASH, formatSignedRealizedPnl } from "@/lib/algo-format";
import { cn } from "@/lib/utils";

type MetricGroup = "signal" | "market" | "scan" | "execution";

const METRIC_LABELS: Record<string, string> = {
  SIGNAL: "Signal",
  Z_SCORE: "Z-score",
  PAIR: "Pair",
  BASE_MID: "Base mid",
  HEDGE_MID: "Hedge mid",
  SPREAD: "Spread",
  BASIS: "Basis",
  REFERENCE: "Reference",
  PAIR_STATE: "Position side",
  WINDOW: "Sample window",
  UNIVERSE: "Universe",
  QUOTED: "Quoted",
  READY: "Ready",
  WARMING: "Warming",
  BULLISH: "Bullish",
  BEARISH: "Bearish",
  IN_POSITION: "In position",
  ENTRIES: "Entry candidates",
  EXITS: "Exit candidates",
  SIGNALS: "Signals (tick)",
  OPEN_POSITIONS: "Open positions",
  QUOTE_INTENTS: "Quote intents",
  ACTIVE_QUOTES: "Active quotes",
  INVENTORY_NOTIONAL: "Inventory (USD)",
};

const METRIC_GROUPS: Record<string, MetricGroup> = {
  SIGNAL: "signal",
  Z_SCORE: "signal",
  PAIR_STATE: "signal",
  PAIR: "market",
  BASE_MID: "market",
  HEDGE_MID: "market",
  SPREAD: "market",
  BASIS: "market",
  REFERENCE: "market",
  WINDOW: "market",
  UNIVERSE: "scan",
  QUOTED: "scan",
  READY: "scan",
  WARMING: "scan",
  BULLISH: "scan",
  BEARISH: "scan",
  IN_POSITION: "execution",
  ENTRIES: "execution",
  EXITS: "execution",
  SIGNALS: "execution",
  OPEN_POSITIONS: "execution",
  QUOTE_INTENTS: "execution",
  ACTIVE_QUOTES: "execution",
  INVENTORY_NOTIONAL: "execution",
};

const GROUP_TITLES: Record<MetricGroup, string> = {
  signal: "Signal state",
  market: "Market context",
  scan: "Universe scan",
  execution: "Execution activity",
};

const GROUP_ORDER: MetricGroup[] = ["signal", "market", "scan", "execution"];

function pnlTone(value: number): string {
  if (value > 0) return "text-bull";
  if (value < 0) return "text-bear";
  return "text-muted-foreground";
}

function formatMetricValue(key: string, value: string | number | boolean | null | undefined): string {
  if (value === null || value === undefined) return EM_DASH;
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (key === "INVENTORY_NOTIONAL" && typeof value === "number") {
    return `$${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
  }
  if (typeof value === "number") {
    if (Number.isInteger(value)) return String(value);
    const abs = Math.abs(value);
    if (abs >= 1000) return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
    if (abs >= 1) return value.toFixed(4);
    return value.toFixed(6);
  }
  return String(value);
}

function signalTone(signal: string): string {
  const upper = signal.toUpperCase();
  if (upper.includes("LONG") || upper.includes("BUY")) return "border-bull/40 bg-bull/10 text-bull";
  if (upper.includes("SHORT") || upper.includes("SELL")) return "border-bear/40 bg-bear/10 text-bear";
  if (upper === "IN_POSITION") return "border-warning/40 bg-warning/10 text-warning";
  if (upper === "WARMUP" || upper === "HOLD") return "border-border bg-muted/30 text-muted-foreground";
  return "border-border bg-card text-foreground";
}

function signalIcon(signal: string) {
  const upper = signal.toUpperCase();
  if (upper.includes("LONG") || upper.includes("BUY")) return <ArrowUpRight className="size-4" />;
  if (upper.includes("SHORT") || upper.includes("SELL")) return <ArrowDownRight className="size-4" />;
  if (upper === "IN_POSITION") return <Activity className="size-4" />;
  return <Minus className="size-4" />;
}

function groupMetrics(rows: Record<string, string | number | boolean | null>) {
  const grouped: Partial<Record<MetricGroup, Array<[string, string | number | boolean | null]>>> = {};
  for (const [key, value] of Object.entries(rows)) {
    if (key === "STRATEGY") continue;
    const group = METRIC_GROUPS[key] ?? "execution";
    grouped[group] ??= [];
    grouped[group]!.push([key, value]);
  }
  return grouped;
}

function MetricGrid({
  entries,
}: {
  entries: Array<[string, string | number | boolean | null]>;
}) {
  if (!entries.length) return null;
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
      {entries.map(([key, value]) => (
        <div key={key}>
          <dt className="text-[10px] uppercase tracking-wider text-muted-foreground">
            {METRIC_LABELS[key] ?? key.replaceAll("_", " ").toLowerCase()}
          </dt>
          <dd className="mt-0.5 font-mono text-sm tabular-nums text-foreground">
            {formatMetricValue(key, value)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function StrategyTelemetry({
  analytics,
}: {
  analytics: Record<string, string | number | boolean | null> | undefined;
}) {
  if (!analytics || Object.keys(analytics).length <= 1) {
    return (
      <p className="px-4 py-5 text-xs text-muted-foreground">
        No live telemetry yet — metrics appear once the engine ticks this strategy.
      </p>
    );
  }

  const signal = analytics.SIGNAL != null ? String(analytics.SIGNAL) : null;
  const grouped = groupMetrics(analytics);

  return (
    <div className="space-y-4 p-4">
      {signal ? (
        <div
          className={cn(
            "flex items-center gap-3 rounded-md border px-4 py-3",
            signalTone(signal),
          )}
        >
          {signalIcon(signal)}
          <div>
            <div className="text-[10px] uppercase tracking-wider opacity-80">Current signal</div>
            <div className="text-lg font-semibold tracking-tight">{signal}</div>
          </div>
          {analytics.Z_SCORE != null ? (
            <div className="ml-auto text-right">
              <div className="text-[10px] uppercase tracking-wider opacity-80">Z-score</div>
              <div className="font-mono text-sm tabular-nums">{formatMetricValue("Z_SCORE", analytics.Z_SCORE)}</div>
            </div>
          ) : null}
        </div>
      ) : null}

      {GROUP_ORDER.map((group) => {
        const entries = grouped[group];
        if (!entries?.length) return null;
        const filtered = entries.filter(([key]) => key !== "SIGNAL" && key !== "Z_SCORE");
        if (!filtered.length) return null;
        return (
          <div key={group}>
            <h4 className="mb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
              {GROUP_TITLES[group]}
            </h4>
            <MetricGrid entries={filtered} />
          </div>
        );
      })}
    </div>
  );
}

function PnlStat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border border-border/80 bg-background/40 px-3 py-2.5">
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className={cn("mt-1 font-mono text-base font-medium tabular-nums", pnlTone(value))}>
        {formatSignedRealizedPnl(value)}
      </div>
    </div>
  );
}

function StrategyCard({
  row,
  analytics,
}: {
  row: StrategyHubSnapshot["strategies"][number];
  analytics: StrategyAnalytics[string] | undefined;
}) {
  return (
    <Panel
      title={row.label}
      right={
        <span className="font-mono text-[10px] normal-case tracking-normal text-muted-foreground">
          {row.name}
          {row.openLegs.length > 0 ? ` · ${row.openLegs.length} leg${row.openLegs.length === 1 ? "" : "s"}` : ""}
        </span>
      }
    >
      <div className="grid grid-cols-3 gap-2 border-b border-border p-4">
        <PnlStat label="Attributed" value={row.totalPnl} />
        <PnlStat label="Realized (attr.)" value={row.realizedPnl} />
        <PnlStat label="Open (attr.)" value={row.unrealizedPnl} />
      </div>

      <StrategyTelemetry analytics={analytics} />

      {row.openLegs.length > 0 ? (
        <div className="border-t border-border">
          <div className="bg-muted/20 px-4 py-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            Attributed open legs
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border text-left text-[10px] uppercase tracking-wider text-muted-foreground">
                  <th className="px-4 py-2 font-medium">Symbol</th>
                  <th className="px-4 py-2 font-medium">Side</th>
                  <th className="px-4 py-2 text-right font-medium">Size</th>
                  <th className="px-4 py-2 text-right font-medium">Entry</th>
                  <th className="px-4 py-2 text-right font-medium">Mark</th>
                  <th className="px-4 py-2 text-right font-medium">uPnL</th>
                </tr>
              </thead>
              <tbody>
                {row.openLegs.map((leg) => (
                  <tr key={`${row.name}-${leg.symbol}`} className="border-b border-border/50 last:border-0">
                    <td className="px-4 py-2 font-mono">{leg.symbol}</td>
                    <td className="px-4 py-2">
                      <Badge
                        variant="outline"
                        className={cn(
                          "text-[10px] uppercase",
                          leg.side === "long" ? "border-bull/40 text-bull" : "border-bear/40 text-bear",
                        )}
                      >
                        {leg.side}
                      </Badge>
                    </td>
                    <td className="px-4 py-2 text-right font-mono tabular-nums">{leg.size.toFixed(6)}</td>
                    <td className="px-4 py-2 text-right font-mono tabular-nums">{leg.entry.toFixed(4)}</td>
                    <td className="px-4 py-2 text-right font-mono tabular-nums">{leg.mark.toFixed(4)}</td>
                    <td className={cn("px-4 py-2 text-right font-mono tabular-nums", pnlTone(leg.unrealizedPnl))}>
                      {formatSignedRealizedPnl(leg.unrealizedPnl)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </Panel>
  );
}

function formatLogTimestamp(ts: unknown): string {
  if (typeof ts !== "number" || !Number.isFinite(ts)) return EM_DASH;
  return new Date(ts * 1000).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function ActivityLogEntry({ line }: { line: Record<string, unknown> }) {
  const strategies = Array.isArray(line.strategies) ? line.strategies : [];
  const analytics =
    line.analytics && typeof line.analytics === "object"
      ? (line.analytics as Record<string, Record<string, unknown>>)
      : {};

  return (
    <div className="border-b border-border/50 px-4 py-3 last:border-0">
      <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
        <span className="font-mono text-foreground">{formatLogTimestamp(line.ts)}</span>
        {typeof line.mode === "string" ? (
          <Badge variant="outline" className="text-[10px] uppercase">
            {line.mode}
          </Badge>
        ) : null}
      </div>
      <div className="mt-2 space-y-1.5">
        {strategies.map((raw, i) => {
          if (!raw || typeof raw !== "object") return null;
          const row = raw as Record<string, unknown>;
          const name = String(row.name ?? `strategy-${i}`);
          const label = String(row.label ?? name);
          const total = typeof row.total_pnl === "number" ? row.total_pnl : 0;
          const signal = analytics[name]?.SIGNAL;
          return (
            <div key={`${name}-${i}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
              <span className="font-medium">{label}</span>
              <span className={cn("font-mono tabular-nums", pnlTone(total))}>
                {formatSignedRealizedPnl(total)}
              </span>
              {signal != null ? (
                <span className="font-mono text-muted-foreground">signal {String(signal)}</span>
              ) : null}
              {Array.isArray(row.open_legs) && row.open_legs.length > 0 ? (
                <span className="text-muted-foreground">{row.open_legs.length} open leg(s)</span>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function resolvePortfolioPnl(
  hub: StrategyHubSnapshot,
  systemHealth: SystemHealth | null,
): {
  realized: number;
  unrealized: number;
  total: number;
  equity: number;
  sessionStartEquity: number;
  sessionEquityDelta: number;
} {
  const realized = systemHealth?.realizedPnl ?? hub.portfolio.realizedPnl;
  const unrealized = systemHealth?.unrealizedPnl ?? hub.portfolio.unrealizedPnl;
  const equity = systemHealth?.equity ?? hub.portfolio.equity;
  const sessionStartEquity = hub.portfolio.sessionStartEquity;
  const sessionEquityDelta =
    sessionStartEquity > 0 ? equity - sessionStartEquity : realized + unrealized;
  return {
    realized,
    unrealized,
    total: realized + unrealized,
    equity,
    sessionStartEquity,
    sessionEquityDelta,
  };
}

function SummaryStrip({
  hub,
  systemHealth,
  equityCurveDelta,
}: {
  hub: StrategyHubSnapshot;
  systemHealth: SystemHealth | null;
  equityCurveDelta: number | null;
}) {
  const portfolio = resolvePortfolioPnl(hub, systemHealth);
  const attributed = hub.strategies.reduce(
    (acc, row) => ({
      realized: acc.realized + row.realizedPnl,
      unrealized: acc.unrealized + row.unrealizedPnl,
      total: acc.total + row.totalPnl,
      legs: acc.legs + row.openLegs.length,
    }),
    { realized: 0, unrealized: 0, total: 0, legs: 0 },
  );
  const attributionGap = portfolio.total - attributed.total;
  const showGap = Math.abs(attributionGap) >= 0.05;

  const updated =
    hub.ts > 0
      ? new Date(hub.ts * 1000).toLocaleString(undefined, {
          month: "short",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        })
      : null;

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <div className="rounded-sm border border-border bg-card/60 p-4">
        <div className="flex items-center gap-2 text-[10px] uppercase tracking-wider text-muted-foreground">
          {portfolio.total >= 0 ? (
            <TrendingUp className="size-3.5 text-bull" />
          ) : (
            <TrendingDown className="size-3.5 text-bear" />
          )}
          Portfolio P&L (venue)
        </div>
        <div className={cn("mt-2 font-mono text-2xl font-semibold tabular-nums", pnlTone(portfolio.total))}>
          {formatSignedRealizedPnl(portfolio.total)}
        </div>
        <div className="mt-1 text-[11px] text-muted-foreground">
          Realized {formatSignedRealizedPnl(portfolio.realized)} · Open{" "}
          {formatSignedRealizedPnl(portfolio.unrealized)}
        </div>
        <div className="mt-1 text-[11px] text-muted-foreground">
          Matches live console · open P&L uses Binance <span className="font-mono">up</span>
        </div>
      </div>

      <div className="rounded-sm border border-border bg-card/60 p-4">
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Session equity Δ</div>
        <div
          className={cn(
            "mt-2 font-mono text-2xl font-semibold tabular-nums",
            pnlTone(portfolio.sessionEquityDelta),
          )}
        >
          {formatSignedRealizedPnl(portfolio.sessionEquityDelta)}
        </div>
        <div className="mt-1 text-[11px] text-muted-foreground">
          Since engine start · equity $
          {portfolio.equity.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
        </div>
        {equityCurveDelta != null ? (
          <div className="mt-1 text-[11px] text-muted-foreground">
            Curve window {formatSignedRealizedPnl(equityCurveDelta)} (same as main EQUITY sub-label)
          </div>
        ) : null}
      </div>

      <div className="rounded-sm border border-border bg-card/60 p-4">
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Attributed breakdown</div>
        <div className={cn("mt-2 font-mono text-2xl font-semibold tabular-nums", pnlTone(attributed.total))}>
          {formatSignedRealizedPnl(attributed.total)}
        </div>
        <div className="mt-1 text-[11px] text-muted-foreground">
          {hub.strategies.length} strateg{hub.strategies.length === 1 ? "y" : "ies"} · mode{" "}
          <span className="font-mono text-foreground">{hub.mode}</span>
          {attributed.legs > 0 ? ` · ${attributed.legs} leg${attributed.legs === 1 ? "" : "s"}` : ""}
        </div>
        {showGap ? (
          <div className="mt-1 text-[11px] text-warning">
            Gap vs venue {formatSignedRealizedPnl(attributionGap)} — unattributed closes or ledger drift
          </div>
        ) : null}
      </div>

      <div className="rounded-sm border border-border bg-card/60 p-4">
        <div className="flex items-center gap-2 text-[10px] uppercase tracking-wider text-muted-foreground">
          <BarChart3 className="size-3.5" />
          How to read this
        </div>
        <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
          <strong className="text-foreground">Portfolio P&L</strong> is the venue truth used on the live
          console. <strong className="text-foreground">Attributed</strong> splits that activity across
          strategies from the ledger and close tape — it will not always sum exactly. Per-strategy realized
          counts attributed closes since engine start.
        </p>
        {updated ? (
          <p className="mt-2 text-[11px] text-muted-foreground">
            Last update <span className="font-mono text-foreground">{updated}</span>
          </p>
        ) : null}
      </div>
    </div>
  );
}

export function StrategyAnalyticsView({
  hub,
  logLines,
  error,
  systemHealth = null,
  equityCurveDelta = null,
}: {
  hub: StrategyHubSnapshot | null;
  logLines: Array<Record<string, unknown>>;
  error: string | null;
  systemHealth?: SystemHealth | null;
  equityCurveDelta?: number | null;
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
        Loading strategy analytics…
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <SummaryStrip hub={hub} systemHealth={systemHealth} equityCurveDelta={equityCurveDelta} />

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {hub.strategies.map((row) => (
          <StrategyCard key={row.name} row={row} analytics={hub.analytics[row.name]} />
        ))}
      </div>

      {hub.strategies.length === 0 ? (
        <div className="rounded-sm border border-border bg-card/60 px-4 py-8 text-center text-sm text-muted-foreground">
          No strategies are active in the current engine mode. Start the engine or switch to multi-strategy
          mode to see attribution.
        </div>
      ) : null}

      <Panel
        title="Activity log"
        right={
          hub.logPath ? (
            <span className="max-w-[280px] truncate font-mono text-[10px] normal-case tracking-normal text-muted-foreground">
              {hub.logPath}
            </span>
          ) : (
            <span className="text-[10px] normal-case tracking-normal text-muted-foreground">
              material changes only
            </span>
          )
        }
      >
        {logLines.length === 0 ? (
          <p className="px-4 py-6 text-xs text-muted-foreground">
            No snapshots yet. Entries appear when attributed PnL or strategy telemetry changes materially.
          </p>
        ) : (
          <div className="max-h-96 overflow-auto">
            {logLines.map((line, i) => (
              <ActivityLogEntry key={i} line={line} />
            ))}
          </div>
        )}
      </Panel>
    </div>
  );
}
