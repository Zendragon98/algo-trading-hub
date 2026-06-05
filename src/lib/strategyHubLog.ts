/** Strategy-hub JSONL rows (REST tail + WS ``strategy_hub`` events) for the analytics log panel. */

export const STRATEGY_HUB_LOG_CAP = 50;

export type StrategyHubLogLine = Record<string, unknown>;

export function strategyHubPayloadToLogLine(
  ts: number,
  data: Record<string, unknown>,
): StrategyHubLogLine {
  return {
    ts: Number(data.ts ?? ts),
    mode: data.mode,
    strategies: data.strategies,
    analytics: data.analytics,
    portfolio: data.portfolio,
  };
}

/** Unwrap recorder rows ``{ ts, type, data }`` or pass through flat hub payloads. */
export function normalizeStrategyHubLogRecord(raw: StrategyHubLogLine): StrategyHubLogLine {
  const nested = raw.data;
  if (nested && typeof nested === "object" && !Array.isArray(nested)) {
    return strategyHubPayloadToLogLine(
      Number(raw.ts ?? 0),
      nested as Record<string, unknown>,
    );
  }
  return raw;
}

export function appendStrategyHubLogLine(
  prev: StrategyHubLogLine[],
  line: StrategyHubLogLine,
): StrategyHubLogLine[] {
  const ts = line.ts;
  if (prev.length > 0 && prev[0]?.ts === ts) return prev;
  return [line, ...prev].slice(0, STRATEGY_HUB_LOG_CAP);
}

export function hydrateStrategyHubLogLines(rows: StrategyHubLogLine[]): StrategyHubLogLine[] {
  const normalized = rows.map(normalizeStrategyHubLogRecord);
  const seen = new Set<number>();
  const out: StrategyHubLogLine[] = [];
  for (const row of [...normalized].reverse()) {
    const ts = Number(row.ts);
    if (!Number.isFinite(ts) || seen.has(ts)) continue;
    seen.add(ts);
    out.push(row);
    if (out.length >= STRATEGY_HUB_LOG_CAP) break;
  }
  return out;
}

/** Prefer in-memory WS rows; fill gaps from REST JSONL tail (newest first). */
export function mergeStrategyHubLogLines(
  wsLines: StrategyHubLogLine[],
  restRows: StrategyHubLogLine[],
): StrategyHubLogLine[] {
  const hydrated = hydrateStrategyHubLogLines(restRows);
  const seen = new Set<number>();
  const out: StrategyHubLogLine[] = [];
  for (const row of [...wsLines, ...hydrated]) {
    const ts = Number(row.ts);
    if (!Number.isFinite(ts) || seen.has(ts)) continue;
    seen.add(ts);
    out.push(row);
    if (out.length >= STRATEGY_HUB_LOG_CAP) break;
  }
  return out;
}
