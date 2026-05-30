import type { SettingsDTO } from "@/lib/api";

export function titleCaseKey(key: string): string {
  return key
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

export function coerceForPatch(key: string, ui: unknown, baseline: unknown): unknown {
  if (key === "binance_api_key" || key === "binance_api_secret") {
    if (ui === "***" || ui === "") return undefined;
    return ui;
  }
  if (typeof baseline === "boolean") return Boolean(ui);
  if (typeof baseline === "number") {
    if (ui === "" || ui === null || ui === undefined) return baseline;
    const n = typeof ui === "number" ? ui : Number(ui);
    return Number.isFinite(n) ? n : baseline;
  }
  if (Array.isArray(baseline)) {
    if (typeof ui === "string") {
      return ui
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
    }
    return ui;
  }
  return ui;
}

export function isStrategyParamKey(key: string): boolean {
  return (
    key === "strategy" ||
    key === "symbols" ||
    key === "base_currency" ||
    key.startsWith("pair_") ||
    key.startsWith("sma_") ||
    key.startsWith("mm_") ||
    key.startsWith("mm2_") ||
    key.startsWith("blend_") ||
    key.startsWith("flow_") ||
    key === "flow_universe_auto"
  );
}

export function isSystemKey(key: string): boolean {
  return (
    key === "venue" ||
    key === "trading_mode" ||
    key.startsWith("binance_") ||
    key.startsWith("ibkr_") ||
    key === "api_host" ||
    key === "api_port" ||
    key === "klines_cache_ttl_sec" ||
    key === "cors_origins" ||
    key.startsWith("persist_") ||
    key.startsWith("log_file_") ||
    key === "log_level" ||
    key === "engine_autostart"
  );
}

export const BOOT_STRATEGY_OPTIONS: { value: string; label: string }[] = [
  { value: "all", label: "All strategies (netted)" },
  { value: "pairs_trading_usdt_usdc", label: "Pairs trading (USDT/USDC)" },
  { value: "sma_crossover", label: "SMA crossover" },
  { value: "blended_signals", label: "Blended signals (EMA/MACD/RSI/BB)" },
  { value: "flow_momentum", label: "Flow momentum (tape follow)" },
  { value: "market_making_v2", label: "Market making (fee-aware quotes)" },
];

const MM_INST_PREFIXES = [
  "mm_institutional",
  "mm_quote",
  "mm_urgent",
  "mm_tape_pressure",
  "mm_max_inventory",
  "mm_inventory",
  "mm_reservation",
  "mm_symbol",
  "mm_quote_use_venue",
  "mm_quote_venue",
  "mm_jump",
  "mm_max_adverse",
  "mm_markout",
  "mm_scratch",
  "mm_min_exit",
  "mm_max_hold",
  "mm_catastrophe",
  "mm_depletion",
  "mm_large_trade",
  "mm_toxicity",
] as const;

export type SettingsSectionId =
  | "common"
  | "pairs"
  | "sma"
  | "mm-inst"
  | "mm-legacy"
  | "mm2"
  | "blend"
  | "flow"
  | "risk"
  | "system";

export type SettingsSection = {
  id: SettingsSectionId;
  label: string;
  description?: string;
  grid?: boolean;
};

export const SETTINGS_SECTIONS: SettingsSection[] = [
  { id: "common", label: "Common", description: "Boot strategy, symbols, base currency" },
  { id: "pairs", label: "Pairs trading" },
  { id: "sma", label: "SMA crossover" },
  { id: "mm-inst", label: "MM institutional" },
  { id: "mm-legacy", label: "MM skew & tape" },
  { id: "mm2", label: "MM 2.0" },
  { id: "blend", label: "Blended signals" },
  { id: "flow", label: "Flow momentum" },
  {
    id: "risk",
    label: "Risk & execution",
    description: "Breaker toggles live on the dashboard Circuit breakers panel.",
    grid: true,
  },
  {
    id: "system",
    label: "System & API",
    description: "api_host / api_port require a backend restart.",
    grid: true,
  },
];

export function keysForSection(sectionId: SettingsSectionId, allKeys: string[]): string[] {
  switch (sectionId) {
    case "common":
      return allKeys.filter((k) => ["strategy", "symbols", "base_currency"].includes(k));
    case "pairs":
      return allKeys.filter((k) => k.startsWith("pair_"));
    case "sma":
      return allKeys.filter((k) => k.startsWith("sma_"));
    case "mm-inst": {
      const mmAll = allKeys.filter((k) => k.startsWith("mm_") && !k.startsWith("mm2_"));
      return mmAll.filter((k) => MM_INST_PREFIXES.some((p) => k === p || k.startsWith(`${p}_`)));
    }
    case "mm-legacy": {
      const mmAll = allKeys.filter((k) => k.startsWith("mm_") && !k.startsWith("mm2_"));
      const inst = keysForSection("mm-inst", allKeys);
      return mmAll.filter((k) => !inst.includes(k));
    }
    case "mm2":
      return allKeys.filter((k) => k.startsWith("mm2_"));
    case "blend":
      return allKeys.filter((k) => k.startsWith("blend_"));
    case "flow":
      return allKeys.filter((k) => k.startsWith("flow_"));
    case "risk":
      return allKeys.filter(
        (k) => !isStrategyParamKey(k) && !isSystemKey(k) && k !== "breaker_enabled",
      );
    case "system":
      return allKeys.filter(isSystemKey);
    default:
      return [];
  }
}

export function countDirtyKeys(draft: SettingsDTO, baseline: SettingsDTO | null): number {
  if (!baseline) return 0;
  let n = 0;
  for (const key of Object.keys(draft)) {
    const next = coerceForPatch(key, draft[key], baseline[key]);
    if (next === undefined && (key === "binance_api_key" || key === "binance_api_secret")) {
      continue;
    }
    const comparableNext = next ?? draft[key];
    if (JSON.stringify(comparableNext) !== JSON.stringify(baseline[key])) n += 1;
  }
  return n;
}

export function buildSettingsPatch(draft: SettingsDTO, baseline: SettingsDTO): Record<string, unknown> {
  const patch: Record<string, unknown> = {};
  for (const key of Object.keys(draft)) {
    const next = coerceForPatch(key, draft[key], baseline[key]);
    if (next === undefined && (key === "binance_api_key" || key === "binance_api_secret")) {
      continue;
    }
    const comparableNext = next ?? draft[key];
    if (JSON.stringify(comparableNext) !== JSON.stringify(baseline[key])) {
      patch[key] = comparableNext;
    }
  }
  return patch;
}

export function matchesSearch(key: string, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return key.toLowerCase().includes(q) || titleCaseKey(key).toLowerCase().includes(q);
}
