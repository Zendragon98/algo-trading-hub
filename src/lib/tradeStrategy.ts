import type { StrategyInfo } from "@/components/algo/types";
import { EM_DASH } from "@/lib/algo-format";

const NETTED = "__netted__";
const FLATTEN = "__flatten__";

function strategyShortTag(name: string): string | null {
  if (name.includes("pairs_")) return "PAIRS";
  if (name.includes("flow_momentum")) return "FLOW";
  if (name.includes("sma_cross")) return "SMA";
  if (name.includes("blend_")) return "BLEND";
  if (name.includes("market_making")) return "MM2";
  return null;
}

function resolveStrategyLabel(name: string, strategies: StrategyInfo[]): string {
  const hit = strategies.find((s) => s.name === name);
  if (hit) return hit.label;
  const tag = strategyShortTag(name);
  return tag ?? name.replace(/_/g, " ");
}

/** Human-readable strategy attribution for a recent-trade row. */
export function formatTradeStrategyLabel(
  strategyName: string,
  strategyContributions: Record<string, number>,
  strategies: StrategyInfo[],
): string {
  if (!strategyName) return EM_DASH;
  if (strategyName === FLATTEN) return "Flatten";
  if (strategyName === NETTED) {
    const names = Object.keys(strategyContributions);
    if (names.length) {
      return names.map((n) => resolveStrategyLabel(n, strategies)).join(" + ");
    }
    return "Netted";
  }
  return resolveStrategyLabel(strategyName, strategies);
}
