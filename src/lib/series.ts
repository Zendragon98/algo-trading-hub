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
