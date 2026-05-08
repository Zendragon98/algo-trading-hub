import { useEffect, useMemo, useState } from "react";
import { TrendingDown, TrendingUp, X } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { Position } from "./mockData";

function seeded(seed: number) {
  let s = seed;
  return () => {
    s = (s * 9301 + 49297) % 233280;
    return s / 233280;
  };
}

function hash(str: string) {
  let h = 0;
  for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) | 0;
  return Math.abs(h);
}

type TF = "1m" | "5m" | "15m" | "1h" | "4h";
const TIMEFRAMES: TF[] = ["1m", "5m", "15m", "1h", "4h"];

function makeSeries(symbol: string, tf: TF, mark: number, n = 120) {
  const r = seeded(hash(symbol + tf));
  const vol = mark * 0.012 * (tf === "1m" ? 0.4 : tf === "5m" ? 0.7 : tf === "15m" ? 1 : tf === "1h" ? 1.6 : 2.4);
  const out: number[] = [];
  let v = mark - vol * (n / 8);
  for (let i = 0; i < n; i++) {
    v += (r() - 0.48) * vol;
    out.push(v);
  }
  // Anchor the last point near current mark
  const drift = mark - out[out.length - 1];
  return out.map((p, i) => p + (drift * i) / (n - 1));
}

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

  useEffect(() => {
    if (open) setTf("15m");
  }, [open, position?.symbol]);

  const data = useMemo(
    () => (position ? makeSeries(position.symbol, tf, position.mark) : []),
    [position, tf],
  );

  if (!position) return null;

  const dir = position.side === "long" ? 1 : -1;
  const pnl = (position.mark - position.entry) * position.size * dir;
  const pnlPct = ((position.mark - position.entry) / position.entry) * 100 * dir;
  const positive = pnl >= 0;

  const min = Math.min(...data, position.entry);
  const max = Math.max(...data, position.entry);
  const range = max - min || 1;
  const W = 100;
  const H = 100;
  const step = W / (data.length - 1);
  const yFor = (v: number) => H - ((v - min) / range) * H;
  const path = data
    .map((v, i) => `${i === 0 ? "M" : "L"}${(i * step).toFixed(2)},${yFor(v).toFixed(2)}`)
    .join(" ");
  const area = `${path} L${W},${H} L0,${H} Z`;
  const stroke = positive ? "var(--bull)" : "var(--bear)";
  const entryY = yFor(position.entry);
  const markY = yFor(position.mark);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        className="max-w-3xl border-border bg-card p-0 sm:rounded-sm"
      >
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
          <div className="flex items-center gap-3">
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
            <Button
              variant="ghost"
              size="icon"
              className="size-7"
              onClick={() => onOpenChange(false)}
            >
              <X className="size-4" />
            </Button>
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
            {/* Entry line */}
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
            {/* Mark dot */}
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