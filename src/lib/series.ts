export type MaxDrawdown = {
  abs: number;
  pct: number;
  peak: number;
};

/** Session peak-to-trough drawdown from an equity curve (oldest → newest). */
export function computeMaxDrawdown(values: number[]): MaxDrawdown {
  if (!values.length) return { abs: 0, pct: 0, peak: 0 };
  let peak = values[0]!;
  let maxDd = 0;
  for (const eq of values) {
    peak = Math.max(peak, eq);
    maxDd = Math.max(maxDd, peak - eq);
  }
  const pct = peak > 0 ? (maxDd / peak) * 100 : 0;
  return { abs: maxDd, pct, peak };
}

/** Evenly downsample a series while always keeping the first and last points. */
export function downsampleSeries(values: number[], maxPoints: number): number[] {
  if (values.length <= maxPoints) return values;
  if (maxPoints < 2) return values.slice(-maxPoints);

  const lastIdx = values.length - 1;
  const out: number[] = [];
  for (let i = 0; i < maxPoints; i++) {
    const idx = Math.round((i * lastIdx) / (maxPoints - 1));
    out.push(values[idx]!);
  }
  return out;
}

/** Downsample while preserving per-bucket min/max so drawdown troughs survive. */
export function downsampleSeriesPreserveExtrema(values: number[], maxPoints: number): number[] {
  const n = values.length;
  if (n <= maxPoints) return values;
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
  if (sorted.length > maxPoints) {
    return downsampleSeries(values, maxPoints);
  }
  return sorted.map((i) => values[i]!);
}
