import { useMemo } from "react";

import { downsampleSeriesPreserveExtrema } from "@/lib/series";

const MAX_DISPLAY_POINTS = 256;

function normalizeSeries(values: number[]): number[] {
  if (values.length >= 2) return values;
  if (values.length === 1) return [values[0]!, values[0]!];
  return [0, 0];
}

export function EquityChart({ data }: { data: number[] }) {
  const series = useMemo(
    () => normalizeSeries(downsampleSeriesPreserveExtrema(data, MAX_DISPLAY_POINTS)),
    [data],
  );

  const { path, area, startY, lastY, start, last } = useMemo(() => {
    const start = series[0]!;
    const last = series[series.length - 1]!;
    const minVal = Math.min(...series);
    const maxVal = Math.max(...series);
    const span = maxVal - minVal;
    const pad = Math.max(span * 0.12, Math.abs(start) * 0.001, 1);
    const min = Math.min(minVal, start) - pad;
    const max = Math.max(maxVal, start) + pad;
    const range = max - min || 1;
    const w = 100;
    const h = 100;
    const step = w / (series.length - 1);
    const yFor = (v: number) => h - ((v - min) / range) * h;
    const pts = series.map((v, i) => [i * step, yFor(v)] as const);
    const path = pts.map(([x, y], i) => (i === 0 ? `M${x},${y}` : `L${x},${y}`)).join(" ");
    const area = `${path} L${w},${h} L0,${h} Z`;
    return {
      path,
      area,
      startY: yFor(start),
      lastY: yFor(last),
      start,
      last,
    };
  }, [series]);

  const up = last >= start;
  const stroke = up ? "var(--bull)" : "var(--bear)";

  return (
    <div className="relative h-full w-full">
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="h-full w-full">
        <defs>
          <linearGradient id="equityFill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor={stroke} stopOpacity="0.35" />
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
        <line
          x1="0"
          x2="100"
          y1={startY}
          y2={startY}
          stroke="var(--muted-foreground)"
          strokeWidth="0.4"
          strokeDasharray="1 1"
          vectorEffect="non-scaling-stroke"
        />
        <path d={area} fill="url(#equityFill)" />
        <path
          d={path}
          fill="none"
          stroke={stroke}
          strokeWidth="1.2"
          vectorEffect="non-scaling-stroke"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        <circle
          cx="0"
          cy={startY}
          r="0.9"
          fill="var(--muted-foreground)"
          vectorEffect="non-scaling-stroke"
        />
        <circle cx="100" cy={lastY} r="0.9" fill={stroke} vectorEffect="non-scaling-stroke" />
      </svg>
      <div
        className="pointer-events-none absolute left-2 -translate-y-1/2 rounded-sm bg-muted px-1.5 py-0.5 font-mono text-[10px] tabular-nums text-muted-foreground"
        style={{ top: `calc(${startY}% + 8px)` }}
      >
        START {start.toFixed(2)}
      </div>
      <div
        className="pointer-events-none absolute right-2 -translate-y-1/2 rounded-sm px-1.5 py-0.5 font-mono text-[10px] tabular-nums"
        style={{
          top: `calc(${lastY}% + 8px)`,
          backgroundColor: stroke,
          color: "var(--background)",
        }}
      >
        NOW {last.toFixed(2)}
      </div>
      <div className="pointer-events-none absolute bottom-2 left-2 text-[10px] uppercase tracking-wider text-muted-foreground">
        session start
      </div>
      <div className="pointer-events-none absolute bottom-2 right-2 text-[10px] uppercase tracking-wider text-muted-foreground">
        now
      </div>
    </div>
  );
}
