import { ChevronDown, Download } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";
import { CLOCK_SKEW_WARN_MS } from "@/components/algo/dashboard/constants";
import { EM_DASH } from "@/lib/algo-format";
import type { AlgoStatus, SystemHealth } from "@/components/algo/types";
export function latencyP95Display(
  latency: SystemHealth["latency"],
  key: string,
  warnMs: number,
): { value: string; ok: boolean } {
  const bucket = latency[key];
  if (!bucket || bucket.count < 1) {
    return { value: EM_DASH, ok: true };
  }
  const p95 = bucket.p95;
  return {
    value: `${p95.toFixed(0)} ms`,
    ok: p95 < warnMs,
  };
}

export function userDataAgeDisplay(health: SystemHealth): { value: string; ok: boolean } {
  if (health.userDataAgeSec < 0) {
    return { value: EM_DASH, ok: true };
  }
  const age = `${health.userDataAgeSec.toFixed(1)}s`;
  if (health.userDataStale) {
    return { value: age, ok: false };
  }
  if (health.userDataReconcileStale) {
    return { value: `${age} (REST)`, ok: false };
  }
  if (!health.userDataMonitored) {
    return { value: `${age} (idle)`, ok: true };
  }
  return { value: age, ok: true };
}

export function clockSkewDisplay(health: SystemHealth): { value: string; ok: boolean } {
  if (!health.clockSkewSynced) {
    return { value: EM_DASH, ok: true };
  }
  const ms = Math.abs(health.clockSkewMs);
  return {
    value: `${ms.toFixed(0)} ms`,
    ok: ms < CLOCK_SKEW_WARN_MS,
  };
}

export function SystemHealthPanel({
  health,
  maxGrossNotional,
  status,
  expanded,
  onExpandedChange,
  exportError,
  onExportReport,
}: {
  health: SystemHealth;
  maxGrossNotional: number;
  status: AlgoStatus;
  expanded: boolean;
  onExpandedChange: (open: boolean) => void;
  exportError: string | null;
  onExportReport: () => void | Promise<void>;
}) {
  const issueCount = [
    health.tickAgeSec < 0 || health.tickAgeSec >= 15,
    !userDataAgeDisplay(health).ok,
    health.orderReconcile.ok === false,
    health.activeBreakers.length > 0,
    !latencyP95Display(health.latency, "tick_to_submit_ms", 500).ok,
    !latencyP95Display(health.latency, "submit_to_ack_ms", 500).ok,
    !clockSkewDisplay(health).ok,
    health.grossNotional > maxGrossNotional,
  ].filter(Boolean).length;

  return (
    <Collapsible open={expanded} onOpenChange={onExpandedChange}>
      <div className="overflow-hidden rounded-sm border border-border bg-card/60">
        <CollapsibleTrigger asChild>
          <button
            type="button"
            className="flex w-full items-center justify-between border-b border-border px-4 py-2 text-left transition-colors hover:bg-muted/30"
          >
            <div className="flex items-center gap-2">
              <h2 className="text-[11px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">
                System health
              </h2>
              {issueCount > 0 ? (
                <Badge variant="outline" className="border-bear/40 text-[10px] text-bear">
                  {issueCount} issue{issueCount === 1 ? "" : "s"}
                </Badge>
              ) : (
                <Badge variant="outline" className="border-bull/40 text-[10px] text-bull">
                  OK
                </Badge>
              )}
            </div>
            <div className="flex items-center gap-2">
              <span className="text-[10px] text-muted-foreground">
                {expanded ? "Hide" : "Show"} diagnostics
              </span>
              <ChevronDown
                className={cn(
                  "size-4 text-muted-foreground transition-transform",
                  expanded && "rotate-180",
                )}
              />
            </div>
          </button>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="flex items-center justify-end gap-2 border-b border-border px-4 py-2">
            <Button
              variant="ghost"
              size="sm"
              className="h-7 gap-1 text-[11px]"
              onClick={() => void onExportReport()}
            >
              <Download className="size-3" /> export
            </Button>
            <LiveDot active={status === "running"} />
          </div>
          {exportError ? (
            <p className="mb-2 px-4 text-[11px] text-bear">{exportError}</p>
          ) : null}
          <div className="grid grid-cols-2 gap-3 px-4 pb-2 text-xs md:grid-cols-4 lg:grid-cols-8">
            <HealthChip
              label="Tick age"
              value={
                health.tickAgeSec >= 0 ? `${health.tickAgeSec.toFixed(1)}s` : EM_DASH
              }
              ok={health.tickAgeSec >= 0 && health.tickAgeSec < 15}
            />
            <HealthChip label="User-data age" {...userDataAgeDisplay(health)} />
            <HealthChip
              label="Order reconcile"
              value={
                health.orderReconcile.ok === false
                  ? `mismatch (${String(health.orderReconcile.venue_only ?? 0)}/${String(health.orderReconcile.local_only ?? 0)})`
                  : "OK"
              }
              ok={health.orderReconcile.ok !== false}
            />
            <HealthChip
              label="Breakers"
              value={
                health.activeBreakers.length
                  ? health.activeBreakers.join(", ")
                  : "none"
              }
              ok={health.activeBreakers.length === 0}
            />
            <HealthChip
              label="p95 tick\u2192submit"
              {...latencyP95Display(health.latency, "tick_to_submit_ms", 500)}
            />
            <HealthChip
              label="p95 submit\u2192ack"
              {...latencyP95Display(health.latency, "submit_to_ack_ms", 500)}
            />
            <HealthChip label="Clock skew" {...clockSkewDisplay(health)} />
            <HealthChip
              label="Gross notional"
              value={`$${health.grossNotional.toLocaleString(undefined, { maximumFractionDigits: 0 })}`}
              ok={health.grossNotional <= maxGrossNotional}
            />
          </div>
          {Object.keys(health.mdHealth).length > 0 ? (
            <div className="mt-3 flex flex-wrap gap-2 px-4 pb-4">
              {Object.entries(health.mdHealth).map(([sym, h]) => (
                <span
                  key={sym}
                  className={cn(
                    "rounded border px-2 py-0.5 text-[10px] tabular-nums",
                    h.crossed_count > 0 || h.last_diff_age_ms > 30_000
                      ? "border-bear/40 text-bear"
                      : "border-border text-muted-foreground",
                  )}
                >
                  {sym} gaps={h.sequence_gaps} age=
                  {h.last_diff_age_ms >= 0
                    ? `${(h.last_diff_age_ms / 1000).toFixed(0)}s`
                    : EM_DASH}
                </span>
              ))}
            </div>
          ) : null}
        </CollapsibleContent>
      </div>
    </Collapsible>
  );
}

export function HealthChip({
  label,
  value,
  ok,
}: {
  label: string;
  value: string;
  ok: boolean;
}) {
  return (
    <div
      className={cn(
        "rounded border px-2 py-1.5",
        ok ? "border-border" : "border-bear/40 bg-bear/5",
      )}
    >
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className={cn("mt-0.5 tabular-nums", ok ? "text-foreground" : "text-bear")}>{value}</div>
    </div>
  );
}

export function LiveDot({ active }: { active: boolean }) {
  return (
    <span className="flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-muted-foreground">
      <span className={cn("size-1.5 rounded-full", active ? "bg-bull pulse-dot" : "bg-muted-foreground")} />
      {active ? "live" : "idle"}
    </span>
  );
}
