import { memo, useEffect, useMemo, useState } from "react";
import { Area, AreaChart, Brush, CartesianGrid, ReferenceLine, XAxis, YAxis } from "recharts";

import { ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import {
  downsampleEquityPoints,
  EQUITY_CHART_RENDER_MAX,
  EQUITY_WINDOW_LABELS,
  filterEquityByWindow,
  formatEquityAxisTick,
  sessionSpanSec,
  windowsForSession,
  type EquityCurvePoint,
  type EquityWindow,
} from "@/lib/equityCurve";
import { downsampleSeriesPreserveExtrema } from "@/lib/series";

const chartConfig = {
  equity: { label: "Equity", color: "hsl(var(--bull))" },
};

function normalizeSeries(values: number[]): number[] {
  if (values.length >= 2) return values;
  if (values.length === 1) return [values[0]!, values[0]!];
  return [0, 0];
}

function StaticEquityChart({
  data,
  preDownsampled = false,
}: {
  data: number[];
  preDownsampled?: boolean;
}) {
  const series = useMemo(
    () =>
      normalizeSeries(
        preDownsampled ? data : downsampleSeriesPreserveExtrema(data, 512),
      ),
    [data, preDownsampled],
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
    return { path, area, startY: yFor(start), lastY: yFor(last), start, last };
  }, [series]);

  const up = last >= start;
  const stroke = up ? "var(--bull)" : "var(--bear)";

  return (
    <div className="relative h-full w-full">
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="h-full w-full">
        <defs>
          <linearGradient id="equityFillStatic" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor={stroke} stopOpacity="0.35" />
            <stop offset="100%" stopColor={stroke} stopOpacity="0" />
          </linearGradient>
        </defs>
        <path d={area} fill="url(#equityFillStatic)" />
        <path
          d={path}
          fill="none"
          stroke={stroke}
          strokeWidth="1.2"
          vectorEffect="non-scaling-stroke"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      </svg>
      <div
        className="pointer-events-none absolute right-2 top-2 rounded-sm px-1.5 py-0.5 font-mono text-[10px] tabular-nums"
        style={{ backgroundColor: stroke, color: "var(--background)" }}
      >
        {last.toFixed(2)}
      </div>
    </div>
  );
}

function InteractiveEquityChart({ points }: { points: EquityCurvePoint[] }) {
  const sessionSpan = useMemo(() => sessionSpanSec(points), [points]);
  const availableWindows = useMemo(() => windowsForSession(sessionSpan), [sessionSpan]);
  const [window, setWindow] = useState<EquityWindow>("session");

  useEffect(() => {
    if (!availableWindows.includes(window)) {
      setWindow("session");
    }
  }, [availableWindows, window]);

  const windowed = useMemo(() => filterEquityByWindow(points, window), [points, window]);
  const windowSpan = useMemo(() => sessionSpanSec(windowed), [windowed]);
  const chartData = useMemo(
    () => downsampleEquityPoints(windowed, EQUITY_CHART_RENDER_MAX),
    [windowed],
  );

  const startEquity = windowed[0]?.equity ?? 0;
  const lastEquity = windowed[windowed.length - 1]?.equity ?? 0;
  const up = lastEquity >= startEquity;
  const stroke = up ? "var(--bull)" : "var(--bear)";

  if (chartData.length < 2) {
    return (
      <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
        Waiting for equity samples…
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2 px-1">
        <ToggleGroup
          type="single"
          value={window}
          onValueChange={(v) => {
            if (v) setWindow(v as EquityWindow);
          }}
          className="h-7 justify-start"
        >
          {availableWindows.map((w) => (
            <ToggleGroupItem
              key={w}
              value={w}
              className="h-7 px-2 text-[10px] uppercase tracking-wider data-[state=on]:bg-muted"
            >
              {EQUITY_WINDOW_LABELS[w]}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        <span className="font-mono text-[10px] tabular-nums text-muted-foreground">
          {windowed.length.toLocaleString()} samples · drag brush to scroll
        </span>
      </div>

      <ChartContainer config={chartConfig} className="aspect-auto min-h-0 flex-1 w-full">
        <AreaChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="equityFillLive" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor={stroke} stopOpacity="0.3" />
              <stop offset="100%" stopColor={stroke} stopOpacity="0" />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" vertical={false} className="stroke-border/40" />
          <XAxis
            dataKey="ts"
            type="number"
            domain={["dataMin", "dataMax"]}
            tickFormatter={(ts) => formatEquityAxisTick(ts, windowSpan)}
            minTickGap={28}
            tick={{ fontSize: 10 }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            domain={["auto", "auto"]}
            tickFormatter={(v) => Number(v).toFixed(0)}
            width={52}
            tick={{ fontSize: 10 }}
            axisLine={false}
            tickLine={false}
          />
          <ChartTooltip
            content={
              <ChartTooltipContent
                labelFormatter={(_, payload) => {
                  const ts = payload?.[0]?.payload?.ts as number | undefined;
                  return ts != null ? formatEquityAxisTick(ts, windowSpan) : "";
                }}
                formatter={(value) => (
                  <span className="font-mono tabular-nums">{Number(value).toFixed(2)}</span>
                )}
              />
            }
          />
          <ReferenceLine
            y={startEquity}
            stroke="var(--muted-foreground)"
            strokeDasharray="4 4"
            strokeOpacity={0.6}
          />
          <Area
            type="monotone"
            dataKey="equity"
            stroke={stroke}
            fill="url(#equityFillLive)"
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
          />
          <Brush
            dataKey="ts"
            height={26}
            stroke="var(--border)"
            fill="var(--muted)"
            travellerWidth={10}
            tickFormatter={(ts) => formatEquityAxisTick(ts, windowSpan)}
          />
        </AreaChart>
      </ChartContainer>
    </div>
  );
}

/** Live console: lightweight SVG. Pass `interactive` for Recharts + brush (analytics). */
export const EquityChart = memo(function EquityChart({
  data,
  points,
  interactive = false,
}: {
  data?: number[];
  points?: EquityCurvePoint[];
  interactive?: boolean;
}) {
  // Full session curve stays in state; chart only draws a downsampled path.
  const values = useMemo(() => {
    const raw = points && points.length > 0 ? points.map((p) => p.equity) : (data ?? []);
    return downsampleSeriesPreserveExtrema(raw, 512);
  }, [points, data]);

  if (interactive && points && points.length > 0) {
    return <InteractiveEquityChart points={points} />;
  }
  return <StaticEquityChart data={values} preDownsampled />;
});
