import { useLayoutEffect, useRef, useState } from "react";
import { ChevronDown, TrendingDown, TrendingUp } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { EM_DASH, formatSignedRealizedPnl } from "@/lib/algo-format";
import type { LogEntry, Position, StrategyInfo, Trade } from "@/components/algo/types";
export function PositionsTable({
  positions,
  onOpen,
}: {
  positions: Position[];
  onOpen: (p: Position) => void;
}) {
  if (!positions.length) {
    return (
      <div className="px-4 py-10 text-center text-xs text-muted-foreground">
        No open positions.
      </div>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-[10px] uppercase tracking-wider text-muted-foreground">
            <th className="px-4 py-2 text-left font-normal">Symbol</th>
            <th className="px-2 py-2 text-left font-normal">Side</th>
            <th className="px-2 py-2 text-right font-normal">Size</th>
            <th className="px-2 py-2 text-right font-normal">Entry</th>
            <th className="px-2 py-2 text-right font-normal">Mark</th>
            <th className="px-4 py-2 text-right font-normal">PnL</th>
            <th className="px-2 py-2 text-right font-normal" />
          </tr>
        </thead>
        <tbody className="font-mono">
          {positions.map((p) => {
            const pnl = p.unrealizedPnl;
            const basis = p.entry * p.size;
            const pct = basis > 1e-12 ? (pnl / basis) * 100 : 0;
            const positive = pnl >= 0;
            return (
              <tr
                key={p.symbol}
                onClick={() => onOpen(p)}
                className="cursor-pointer border-t border-border/60 transition-colors hover:bg-accent/30"
              >
                <td className="px-4 py-2.5 font-semibold">{p.symbol}</td>
                <td className="px-2 py-2.5">
                  <span
                    className={cn(
                      "inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-[10px] uppercase",
                      p.side === "long"
                        ? "border-bull/40 bg-bull/10 text-bull"
                        : "border-bear/40 bg-bear/10 text-bear",
                    )}
                  >
                    {p.side === "long" ? <TrendingUp className="size-3" /> : <TrendingDown className="size-3" />}
                    {p.side}
                  </span>
                </td>
                <td className="px-2 py-2.5 text-right tabular-nums">{p.size}</td>
                <td className="px-2 py-2.5 text-right tabular-nums">{p.entry.toLocaleString()}</td>
                <td className="px-2 py-2.5 text-right tabular-nums">{p.mark.toLocaleString()}</td>
                <td className={cn("px-4 py-2.5 text-right tabular-nums", positive ? "text-bull" : "text-bear")}>
                  {positive ? "+" : ""}
                  {pnl.toFixed(2)}{" "}
                  <span className="text-[10px] opacity-70">({pct.toFixed(2)}%)</span>
                </td>
                <td className="px-3 py-2.5 text-right">
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onOpen(p);
                    }}
                    className="rounded-sm border border-border px-2 py-1 text-[10px] uppercase tracking-wider text-muted-foreground hover:border-bull/40 hover:text-bull"
                  >
                    Chart
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function tradeStrategyLabel(name: string, strategies: StrategyInfo[]): string {
  if (!name) return EM_DASH;
  if (name === "__netted__") return "Netted";
  const hit = strategies.find((s) => s.name === name);
  if (hit) return hit.label;
  const tag = logStrategyTag(`${name} `);
  return tag ?? name.replace(/_/g, " ");
}

export function TradesTable({
  trades,
  strategies = [],
}: {
  trades: Trade[];
  strategies?: StrategyInfo[];
}) {
  const fmtPrice = (v: number | null) =>
    v === null ? EM_DASH : v.toLocaleString(undefined, { maximumFractionDigits: 6 });

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-[10px] uppercase tracking-wider text-muted-foreground">
            <th className="px-4 py-2 text-left font-normal">Time</th>
            <th className="px-2 py-2 text-left font-normal">Type</th>
            <th className="px-2 py-2 text-left font-normal">Strategy</th>
            <th className="px-2 py-2 text-left font-normal">Symbol</th>
            <th className="px-2 py-2 text-left font-normal">Side</th>
            <th className="px-2 py-2 text-right font-normal">Qty</th>
            <th className="px-2 py-2 text-right font-normal">Entry</th>
            <th className="px-2 py-2 text-right font-normal">Exit</th>
            <th className="px-4 py-2 text-right font-normal">PnL</th>
          </tr>
        </thead>
        <tbody className="font-mono">
          {trades.slice(0, 12).map((t) => (
            <tr key={t.id} className="border-t border-border/60 hover:bg-accent/30">
              <td className="px-4 py-2 text-muted-foreground tabular-nums">{t.ts}</td>
              <td className="px-2 py-2">
                <span
                  className={cn(
                    "rounded-sm px-1.5 py-0.5 text-[10px] uppercase",
                    t.action === "open"
                      ? "bg-muted text-muted-foreground"
                      : "bg-warning/15 text-warning",
                  )}
                >
                  {t.action}
                </span>
              </td>
              <td
                className="max-w-[7rem] truncate px-2 py-2 text-[11px] text-muted-foreground"
                title={t.strategyName || undefined}
              >
                {tradeStrategyLabel(t.strategyName, strategies)}
              </td>
              <td className="px-2 py-2">{t.symbol}</td>
              <td className="px-2 py-2">
                <span
                  className={cn(
                    "rounded-sm px-1.5 py-0.5 text-[10px] uppercase",
                    t.side === "buy" ? "bg-bull/15 text-bull" : "bg-bear/15 text-bear",
                  )}
                >
                  {t.side}
                </span>
              </td>
              <td className="px-2 py-2 text-right tabular-nums">{t.qty}</td>
              <td className="px-2 py-2 text-right tabular-nums">{fmtPrice(t.entryPrice)}</td>
              <td className="px-2 py-2 text-right tabular-nums">{fmtPrice(t.exitPrice)}</td>
              <td
                className={cn(
                  "px-4 py-2 text-right tabular-nums",
                  t.action === "open"
                    ? "text-muted-foreground"
                    : (t.pnl ?? 0) >= 0
                      ? "text-bull"
                      : "text-bear",
                )}
              >
                {t.action === "open" ? EM_DASH : formatSignedRealizedPnl(t.pnl)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Newest log lines sit at the top; follow keeps scroll pinned there. */
const LOG_FOLLOW_TOP_PX = 16;

export function logStrategyTag(msg: string): string | null {
  if (msg.startsWith("ALL strategies")) return "ALL";
  if (msg.startsWith("SMA ")) return "SMA";
  if (msg.startsWith("BLEND ") || msg.startsWith("[blend]")) return "BLEND";
  if (msg.startsWith("FLOW ")) return "FLOW";
  if (msg.startsWith("MM2 ")) return "MM2";
  if (msg.startsWith("PAIRS ") || msg.includes("pairs_")) return "PAIRS";
  if (msg.startsWith("MM ")) return "MM2";
  if (msg.includes("flow_momentum")) return "FLOW";
  if (msg.includes("sma_cross")) return "SMA";
  if (msg.includes("blend_")) return "BLEND";
  return null;
}

export function LogStream({ logs, className }: { logs: LogEntry[]; className?: string }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const followRef = useRef(true);
  const [follow, setFollow] = useState(true);

  const setFollowEnabled = (enabled: boolean) => {
    followRef.current = enabled;
    setFollow(enabled);
  };

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    setFollowEnabled(el.scrollTop <= LOG_FOLLOW_TOP_PX);
  };

  const resumeFollow = () => {
    const el = scrollRef.current;
    if (!el) return;
    setFollowEnabled(true);
    el.scrollTop = 0;
  };

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el || !followRef.current) return;
    el.scrollTop = 0;
  }, [logs]);

  const color: Record<LogEntry["level"], string> = {
    debug: "text-muted-foreground/60",
    info: "text-muted-foreground",
    warn: "text-warning",
    error: "text-bear",
    signal: "text-bull",
  };
  const tag: Record<LogEntry["level"], string> = {
    debug: "DBG ",
    info: "INFO",
    warn: "WARN",
    error: "ERR ",
    signal: "SIG ",
  };
  return (
    <div className="relative">
      {!follow ? (
        <div className="pointer-events-none absolute inset-x-0 top-0 z-10 flex justify-center pt-2">
          <Button
            type="button"
            variant="secondary"
            size="sm"
            className="pointer-events-auto h-7 gap-1.5 border border-border/80 bg-card/95 px-2.5 text-[11px] shadow-sm backdrop-blur-sm"
            onClick={resumeFollow}
          >
            <ChevronDown className="size-3 rotate-180" />
            Resume follow
          </Button>
        </div>
      ) : null}
      <div
        ref={scrollRef}
        onScroll={onScroll}
        className={cn(
          "scrollbar-themed overflow-y-auto overflow-x-hidden",
          "h-[320px]",
          className,
        )}
      >
        <div className="space-y-1 px-3 py-2 font-mono text-[12px] leading-relaxed">
          {logs.map((l, i) => {
            const stratTag = logStrategyTag(l.msg);
            return (
              <div key={i} className="flex gap-2">
                <span className="shrink-0 text-muted-foreground/70 tabular-nums">{l.ts}</span>
                <span className={cn("shrink-0 font-semibold", color[l.level])}>{tag[l.level]}</span>
                {stratTag ? (
                  <span className="shrink-0 rounded-sm border border-border/60 bg-muted/40 px-1 text-[9px] font-semibold uppercase tracking-wider text-muted-foreground">
                    {stratTag}
                  </span>
                ) : null}
                {l.logger ? (
                  <span className="shrink-0 max-w-[8rem] truncate text-[10px] text-muted-foreground/60">
                    {l.logger.split(".").slice(-1)[0]}
                  </span>
                ) : null}
                <span className="min-w-0 break-words text-foreground/90">{l.msg}</span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
