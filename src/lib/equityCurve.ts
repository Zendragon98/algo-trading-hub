import type { EquityDTO } from "@/lib/api";

export type EquityCurvePoint = { ts: number; equity: number };

export type EquityWindow = "session" | "4h" | "1h" | "15m" | "5m";

export const EQUITY_WINDOW_SECONDS: Record<Exclude<EquityWindow, "session">, number> = {
  "4h": 4 * 3600,
  "1h": 3600,
  "15m": 15 * 60,
  "5m": 5 * 60,
};

export const EQUITY_WINDOW_LABELS: Record<EquityWindow, string> = {
  session: "Session",
  "4h": "4H",
  "1h": "1H",
  "15m": "15M",
  "5m": "5M",
};

/** Max points drawn at once; full session data is kept, only render is downsampled. */
export const EQUITY_CHART_RENDER_MAX = 1200;

export function appendEquityPoint(
  prev: EquityCurvePoint[],
  sample: EquityCurvePoint,
): EquityCurvePoint[] {
  const last = prev[prev.length - 1];
  if (last && last.ts === sample.ts && last.equity === sample.equity) return prev;
  return [...prev, sample];
}

export function equityDtoToPoints(dto: EquityDTO): EquityCurvePoint[] {
  const n = Math.min(dto.equity.length, dto.timestamps.length);
  let points: EquityCurvePoint[];
  if (n > 0) {
    points = Array.from({ length: n }, (_, i) => ({
      ts: dto.timestamps[i]!,
      equity: dto.equity[i]!,
    }));
  } else if (!dto.equity.length) {
    return [];
  } else {
    const endTs = dto.last_ts > 0 ? dto.last_ts : Date.now() / 1000;
    points = dto.equity.map((equity, i) => ({
      ts: endTs - (dto.equity.length - 1 - i),
      equity,
    }));
  }
  return points;
}

export function sessionSpanSec(points: EquityCurvePoint[]): number {
  if (points.length < 2) return 0;
  return Math.max(0, points[points.length - 1]!.ts - points[0]!.ts);
}

export function windowsForSession(spanSec: number): EquityWindow[] {
  const windows: EquityWindow[] = ["session"];
  for (const key of ["4h", "1h", "15m", "5m"] as const) {
    if (spanSec >= EQUITY_WINDOW_SECONDS[key] * 0.5) {
      windows.push(key);
    }
  }
  return windows;
}

export function filterEquityByWindow(
  points: EquityCurvePoint[],
  window: EquityWindow,
): EquityCurvePoint[] {
  if (points.length < 2) return points;
  if (window === "session") return points;
  const lastTs = points[points.length - 1]!.ts;
  const cutoff = lastTs - EQUITY_WINDOW_SECONDS[window];
  const filtered = points.filter((p) => p.ts >= cutoff);
  return filtered.length >= 2 ? filtered : points.slice(-2);
}

function extremaIndices(values: number[], maxPoints: number): number[] {
  const n = values.length;
  if (n <= maxPoints) return Array.from({ length: n }, (_, i) => i);
  const lastIdx = n - 1;
  const numBuckets = Math.max(1, Math.floor((maxPoints - 2) / 2));
  const indices = new Set<number>([0, lastIdx]);
  for (let bucket = 0; bucket < numBuckets; bucket++) {
    const start = 1 + Math.floor((bucket * (lastIdx - 1)) / numBuckets);
    const end = 1 + Math.floor(((bucket + 1) * (lastIdx - 1)) / numBuckets) - 1;
    if (start > end) continue;
    let minI = start;
    let maxI = start;
    for (let i = start + 1; i <= end; i++) {
      if (values[i]! < values[minI]!) minI = i;
      if (values[i]! > values[maxI]!) maxI = i;
    }
    indices.add(minI);
    indices.add(maxI);
  }
  const sorted = [...indices].sort((a, b) => a - b);
  if (sorted.length <= maxPoints) return sorted;
  const step = (sorted.length - 1) / (maxPoints - 1);
  return Array.from({ length: maxPoints }, (_, i) => sorted[Math.round(i * step)]!);
}

export function downsampleEquityPoints(
  points: EquityCurvePoint[],
  maxPoints: number,
): EquityCurvePoint[] {
  if (points.length <= maxPoints) return points;
  const keep = extremaIndices(
    points.map((p) => p.equity),
    maxPoints,
  );
  return keep.map((i) => points[i]!);
}

export function formatEquityAxisTick(ts: number, spanSec: number): string {
  const d = new Date(ts * 1000);
  if (spanSec < 3600) {
    return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }
  if (spanSec < 86_400) {
    return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  }
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
