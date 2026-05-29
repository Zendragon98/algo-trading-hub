import { useLayoutEffect, useRef, useState } from "react";
import { Link } from "@tanstack/react-router";
import {
  Activity,
  AlertTriangle,
  ChevronDown,
  CircleDot,
  Cpu,
  Download,
  Gauge,
  ListOrdered,
  Loader2,
  Pause,
  Play,
  Power,
  RefreshCcw,
  Settings2,
  ShieldAlert,
  Square,
  Target,
  TrendingDown,
  TrendingUp,
  Wallet,
  Wifi,
  Zap,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "@/lib/utils";
import {
  type ClosedTradePerfVm,
  formatSignedRealizedPnl,
  formatUsdPayoffCell,
} from "@/lib/algo-format";
import type {
  AlgoStatus,
  BreakerList,
  BreakerStatus,
  ExecutionAggregate,
  ExecutionParent,
  LogEntry,
  Position,
  StartupProgress,
  StrategyInfo,
  SystemHealth,
  Trade,
  WorkingOrder,
} from "@/components/algo/types";
export function OmsTable({
  parents,
  children,
}: {
  parents: ExecutionParent[];
  children: WorkingOrder[];
}) {
  if (!parents.length && !children.length) {
    return (
      <div className="px-4 py-10 text-center text-xs text-muted-foreground">
        No working orders. The OMS lights up the moment a parent VWAP is in flight.
      </div>
    );
  }

  // Group children by parent for the nested rendering. Anything orphaned
  // (e.g. an operator-side cancel from another UI) is shown under "manual".
  const childrenByParent = new Map<string, WorkingOrder[]>();
  for (const child of children) {
    const key = child.parentId ?? "manual";
    const existing = childrenByParent.get(key) ?? [];
    existing.push(child);
    childrenByParent.set(key, existing);
  }

  return (
    <ScrollArea className="h-[320px]">
      <div className="divide-y divide-border/60">
        {parents.map((parent) => (
          <ParentRow
            key={parent.parentId}
            parent={parent}
            children={childrenByParent.get(parent.parentId) ?? []}
          />
        ))}
        {(childrenByParent.get("manual") ?? []).map((child) => (
          <div key={child.id} className="px-4 py-2.5 text-xs">
            <div className="flex items-center gap-2 text-muted-foreground">
              <ListOrdered className="size-3" />
              <span className="uppercase tracking-wider">manual order</span>
            </div>
            <ChildRow child={child} />
          </div>
        ))}
      </div>
    </ScrollArea>
  );
}

export function ParentRow({
  parent,
  children,
}: {
  parent: ExecutionParent;
  children: WorkingOrder[];
}) {
  const pct = Math.min(100, Math.max(0, parent.fillRatio * 100));
  const sideClass =
    parent.side === "buy"
      ? "border-bull/40 bg-bull/10 text-bull"
      : "border-bear/40 bg-bear/10 text-bear";
  return (
    <div className="px-4 py-3">
      <div className="flex flex-wrap items-center gap-3 text-xs">
        <span className="font-mono text-foreground/90">{parent.parentId}</span>
        <span
          className={cn(
            "rounded-sm border px-1.5 py-0.5 text-[10px] uppercase",
            sideClass,
          )}
        >
          {parent.side}
        </span>
        <span className="font-semibold">{parent.symbol}</span>
        {parent.algoMode && (
          <Badge variant="outline" className="border-border text-[10px] uppercase tracking-wider">
            {parent.algoMode}
          </Badge>
        )}
        <span className="ml-auto tabular-nums text-muted-foreground">
          {parent.filledQty.toFixed(4)} / {parent.requestedQty.toFixed(4)}
        </span>
      </div>

      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-muted/60">
        <div
          className={cn(
            "h-full transition-all",
            parent.side === "buy" ? "bg-bull" : "bg-bear",
          )}
          style={{ width: `${pct}%` }}
        />
      </div>

      <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
        <span>arrival <span className="tabular-nums text-foreground/80">{parent.arrivalPrice.toFixed(4)}</span></span>
        <span>vwap <span className="tabular-nums text-foreground/80">{parent.vwapPrice.toFixed(4)}</span></span>
        <span className={cn("tabular-nums", parent.slippageBps > 0 ? "text-bear" : "text-bull")}>
          slippage {parent.slippageBps >= 0 ? "+" : ""}{parent.slippageBps.toFixed(1)} bps
        </span>
        <span className={cn("tabular-nums", parent.feeAdjustedSlippageBps > 0 ? "text-bear" : "text-bull")}>
          fee-adj {parent.feeAdjustedSlippageBps >= 0 ? "+" : ""}
          {parent.feeAdjustedSlippageBps.toFixed(1)} bps
        </span>
        <span className="tabular-nums">
          impact {parent.impactBps.toFixed(1)} bps
        </span>
        <span className="tabular-nums">{parent.durationSec.toFixed(1)}s</span>
        {parent.signalScore > 0 ? (
          <span className="tabular-nums">score {parent.signalScore.toFixed(2)}</span>
        ) : null}
      </div>

      {parent.notes ? (
        <p className="mt-1.5 break-words font-mono text-[10px] leading-snug text-muted-foreground">
          {parent.notes}
        </p>
      ) : null}

      {children.length > 0 && (
        <div className="mt-2 space-y-1 border-l border-border/60 pl-3">
          {children.map((c) => (
            <ChildRow key={c.id} child={c} />
          ))}
        </div>
      )}
    </div>
  );
}

export function ChildRow({ child }: { child: WorkingOrder }) {
  const sideClass =
    child.side === "buy"
      ? "border-bull/30 bg-bull/5 text-bull"
      : "border-bear/30 bg-bear/5 text-bear";
  return (
    <div className="flex flex-wrap items-center gap-2 text-[11px] font-mono text-muted-foreground">
      <span className="text-foreground/80">{child.id.slice(-10)}</span>
      <span
        className={cn(
          "rounded-sm border px-1 py-0.5 text-[10px] uppercase",
          sideClass,
        )}
      >
        {child.side}
      </span>
      <span className="uppercase tracking-wider">{child.orderType}</span>
      <span className="tabular-nums">
        {child.filledQty.toFixed(4)} / {child.qty.toFixed(4)}
      </span>
      <span className="tabular-nums">
        @ {child.price !== null ? child.price.toFixed(4) : "mkt"}
      </span>
      <span className="ml-auto rounded-sm border border-border px-1 py-0.5 text-[10px] uppercase">
        {child.status}
      </span>
    </div>
  );
}

export function ExecutionQualityPanel({
  aggregate,
  history,
}: {
  aggregate: ExecutionAggregate;
  history: ExecutionParent[];
}) {
  return (
    <div className="space-y-4 p-4">
      <div className="grid grid-cols-2 gap-3">
        <Stat
          label="AVG SLIPPAGE"
          value={`${aggregate.avgSlippageBps >= 0 ? "+" : ""}${aggregate.avgSlippageBps.toFixed(2)} bps`}
          tone={aggregate.avgSlippageBps > 0 ? "bear" : "bull"}
        />
        <Stat
          label="AVG IMPACT (sim)"
          value={`${aggregate.avgImpactBps.toFixed(2)} bps`}
          tone="neutral"
        />
        <Stat
          label="FILL RATE"
          value={`${(aggregate.avgFillRatio * 100).toFixed(1)}%`}
          tone={aggregate.avgFillRatio >= 0.95 ? "bull" : "neutral"}
        />
        <Stat
          label="AVG DURATION"
          value={`${aggregate.avgDurationSec.toFixed(1)}s`}
          tone="neutral"
        />
      </div>

      <Separator />

      <div className="space-y-1">
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
          Recent parent orders
        </div>
        {history.length === 0 ? (
          <div className="py-6 text-center text-[11px] text-muted-foreground">
            No completed parents yet.
          </div>
        ) : (
          <ScrollArea className="h-[180px]">
            <div className="space-y-1 font-mono text-[11px]">
              {history.slice(0, 12).map((r) => (
                <div
                  key={r.parentId}
                  className="flex items-center gap-2 border-b border-border/40 py-1"
                >
                  <span className="text-muted-foreground">{r.symbol}</span>
                  <span
                    className={cn(
                      "rounded-sm px-1 text-[10px] uppercase",
                      r.side === "buy" ? "bg-bull/15 text-bull" : "bg-bear/15 text-bear",
                    )}
                  >
                    {r.side}
                  </span>
                  <span className="tabular-nums text-muted-foreground">
                    {r.filledQty.toFixed(4)}
                  </span>
                  <span
                    className={cn(
                      "tabular-nums",
                      r.slippageBps > 0 ? "text-bear" : "text-bull",
                    )}
                  >
                    {r.slippageBps >= 0 ? "+" : ""}
                    {r.slippageBps.toFixed(1)}
                  </span>
                  <span
                    className={cn(
                      "tabular-nums",
                      r.feeAdjustedSlippageBps > 0 ? "text-bear" : "text-bull",
                    )}
                  >
                    fee {r.feeAdjustedSlippageBps >= 0 ? "+" : ""}
                    {r.feeAdjustedSlippageBps.toFixed(1)} bps
                  </span>
                  <span className="tabular-nums text-muted-foreground">
                    {r.durationSec.toFixed(1)}s
                  </span>
                  {r.notes ? (
                    <span
                      className="min-w-0 max-w-[12rem] truncate text-muted-foreground/80"
                      title={r.notes}
                    >
                      {r.notes}
                    </span>
                  ) : null}
                </div>
              ))}
            </div>
          </ScrollArea>
        )}
      </div>
    </div>
  );
}

export function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "bull" | "bear" | "neutral";
}) {
  const color =
    tone === "bull" ? "text-bull" : tone === "bear" ? "text-bear" : "text-foreground/90";
  return (
    <div className="rounded-sm border border-border/60 bg-card/40 p-2.5">
      <div className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground">{label}</div>
      <div className={cn("mt-1 font-mono text-base font-semibold tabular-nums", color)}>
        {value}
      </div>
    </div>
  );
}