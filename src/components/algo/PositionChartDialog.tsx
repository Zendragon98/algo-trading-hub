import { useEffect, useMemo, useState } from "react";
import { TrendingDown, TrendingUp } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { api, toKline } from "@/lib/api";
import type { Kline, Position } from "./types";

type TF = "1m" | "5m" | "15m" | "1h" | "4h";
const TIMEFRAMES: TF[] = ["1m", "5m", "15m", "1h", "4h"];

export function PositionChartDialog({
  position,
  open,
  onOpenChange,
}: {
  position: Position | null;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const [tf, setTf] = useState<TF>("15m");
  const [bars, setBars] = useState<Kline[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) setTf("15m");
  }, [open, position?.symbol]);

  // Fetch real OHLCV history every time the dialog opens or the
  // operator switches timeframe. The backend pulls fresh candles from
  // the active venue via `/api/klines`, so dev sees real prices too.
  useEffect(() => {
    if (!open || !position) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .klines(position.symbol, tf, 120)
      .then((rows) => {
        if (cancelled) return;
        setBars(rows.map(toKline));
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "klines fetch failed");
        setBars([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, position, tf]);

  const closes = useMemo(() => bars.map((b) => b.close), [bars]);

  if (!position) return null;

  const pnl = position.unrealizedPnl;
  const basis = position.entry * position.size;
  const pnlPct = basis > 1e-12 ? (position.unrealizedPnl / basis) * 100 : 0;
  const positive = pnl >= 0;
  const stroke = positive ? "var(--bull)" : "var(--bear)";

  // Need ≥2 points to draw a line; pad to two-of-the-same so the SVG
  // still renders a flat segment instead of erroring on the path math.
  const series =
    closes.length >= 2
      ? closes
      : closes.length === 1
        ? [closes[0]!, closes[0]!]
        : [position.mark, position.mark];
  const min = Math.min(...series, position.entry);
  const max = Math.max(...series, position.entry);
  const range = max - min || 1;
  const W = 100;
  const H = 100;
  const step = W / (series.length - 1);
  const yFor = (v: number) => H - ((v - min) / range) * H;
  const path = series
    .map((v, i) => `${i === 0 ? "M" : "L"}${(i * step).toFixed(2)},${yFor(v).toFixed(2)}`)
    .join(" ");
  const area = `${path} L${W},${H} L0,${H} Z`;
  const entryY = yFor(position.entry);
  const markY = yFor(position.mark);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl border-border bg-card p-0 sm:rounded-sm">
        <DialogHeader className="flex flex-row items-center justify-between gap-3 border-b border-border px-5 py-3 space-y-0">
          <div className="flex items-center gap-3">
            <DialogTitle className="font-mono text-base tracking-wide">
              {position.symbol}
            </DialogTitle>
            <span
              className={cn(
                "inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-[10px] uppercase",
                position.side === "long"
                  ? "border-bull/40 bg-bull/10 text-bull"
                  : "border-bear/40 bg-bear/10 text-bear",
              )}
            >
              {position.side === "long" ? (
                <TrendingUp className="size-3" />
              ) : (
                <TrendingDown className="size-3" />
              )}
              {position.side} · {position.size}
            </span>
          </div>
          <div className="flex items-center gap-3 pr-6">
            <div className="hidden gap-1 sm:flex">
              {TIMEFRAMES.map((t) => (
                <button
                  key={t}
                  onClick={() => setTf(t)}
                  className={cn(
                    "rounded-sm px-2 py-1 text-[11px] uppercase tracking-wider transition-colors",
                    tf === t
                      ? "bg-bull/15 text-bull"
                      : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
                  )}
                >
                  {t}
                </button>
              ))}
            </div>
          </div>
        </DialogHeader>

        {/* Stats strip */}
        <div className="grid grid-cols-2 gap-px border-b border-border bg-border md:grid-cols-4">
          <Stat label="Mark" value={position.mark.toLocaleString()} />
          <Stat label="Entry" value={position.entry.toLocaleString()} />
          <Stat
            label="PnL"
            value={`${positive ? "+" : ""}${pnl.toFixed(2)}`}
            tone={positive ? "bull" : "bear"}
          />
          <Stat
            label="PnL %"
            value={`${positive ? "+" : ""}${pnlPct.toFixed(2)}%`}
            tone={positive ? "bull" : "bear"}
          />
        </div>

        {/* Chart */}
        <div className="relative h-[340px] w-full bg-background/40 px-3 py-2">
          {loading && (
            <div className="absolute right-3 top-3 text-[10px] uppercase tracking-wider text-muted-foreground">
              loading {tf}…
            </div>
          )}
          {error && !loading && (
            <div className="absolute inset-0 grid place-items-center px-6 text-center text-xs text-bear">
              {error}
            </div>
          )}
          <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="h-full w-full">
            <defs>
              <linearGradient id="posFill" x1="0" x2="0" y1="0" y2="1">
                <stop offset="0%" stopColor={stroke} stopOpacity="0.3" />
                <stop offset="100%" stopColor={stroke} stopOpacity="0" />
              </linearGradient>
            </defs>
            {[20, 40, 60, 80].map((y) => (
              <line
                key={y}
                x1="0"
                x2="100"
                y1={y}
                y2={y}
                stroke="var(--border)"
                strokeWidth="0.15"
                strokeDasharray="0.6 0.6"
                vectorEffect="non-scaling-stroke"
              />
            ))}
            <path d={area} fill="url(#posFill)" />
            <path
              d={path}
              fill="none"
              stroke={stroke}
              strokeWidth="1.2"
              vectorEffect="non-scaling-stroke"
              strokeLinejoin="round"
              strokeLinecap="round"
            />
            <line
              x1="0"
              x2="100"
              y1={entryY}
              y2={entryY}
              stroke="var(--muted-foreground)"
              strokeWidth="0.4"
              strokeDasharray="1 1"
              vectorEffect="non-scaling-stroke"
            />
            <circle cx="100" cy={markY} r="0.9" fill={stroke} vectorEffect="non-scaling-stroke" />
          </svg>

          <Tag y={entryY} label={`ENTRY ${position.entry.toLocaleString()}`} tone="muted" />
          <Tag y={markY} label={`MARK ${position.mark.toLocaleString()}`} tone={positive ? "bull" : "bear"} />
        </div>
      </DialogContent>
    </Dialog>
  );
}

function Stat({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: "bull" | "bear" | "neutral";
}) {
  return (
    <div className="bg-card px-4 py-3">
      <div className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground">{label}</div>
      <div
        className={cn(
          "mt-1 font-mono text-base tabular-nums",
          tone === "bull" && "text-bull",
          tone === "bear" && "text-bear",
        )}
      >
        {value}
      </div>
    </div>
  );
}

function Tag({ y, label, tone }: { y: number; label: string; tone: "bull" | "bear" | "muted" }) {
  const color =
    tone === "bull" ? "bg-bull text-bull-foreground" : tone === "bear" ? "bg-bear text-bear-foreground" : "bg-muted text-muted-foreground";
  return (
    <div
      className={cn(
        "pointer-events-none absolute right-3 -translate-y-1/2 rounded-sm px-1.5 py-0.5 font-mono text-[10px] tabular-nums",
        color,
      )}
      style={{ top: `calc(${y}% + 8px)` }}
    >
      {label}
    </div>
  );
}
