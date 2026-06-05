/** Shared formatters for the algo console KPIs and trade tables. */

import type { KpiDTO } from "@/lib/api";

/** N/A cell placeholder (em dash). Unicode escape avoids source-file encoding issues. */
export const EM_DASH = "\u2014";

export type ClosedTradePerfVm = {
  winRatePct: number;
  profitFactor: number | null;
  grossWin: number;
  grossLoss: number;
  netFromCloses: number;
  closed: number;
  winCount: number;
  lossCount: number;
  breakevenCount: number;
  /** Wins / (wins + losses) — excludes breakeven closes; used vs breakeven WR target. */
  decisiveWinRatePct: number | null;
  avgWin: number | null;
  avgLoss: number | null;
  payoffRatio: number | null;
  expectancy: number | null;
  breakevenWrPct: number | null;
};

export function formatUsdRough(n: number): string {
  return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** Extra decimals when dollars are tiny so payoff totals stay consistent. */
export function formatUsdPayoffCell(n: number): string {
  const a = Math.abs(n);
  if (a >= 100)
    return a.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (a >= 1) return n.toFixed(2);
  if (a >= 0.01) return n.toFixed(4);
  if (a > 0) return n.toFixed(6);
  return "0.00";
}

export function formatNegativeUsd(n: number): string {
  return `-$${formatUsdPayoffCell(Math.abs(n))}`;
}

export function formatSignedRealizedPnl(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return EM_DASH;
  const sign = n >= 0 ? "+" : "-";
  return `${sign}$${formatUsdPayoffCell(Math.abs(n))}`;
}

export function derivePayoffMetrics(
  winCount: number,
  lossCount: number,
  grossWin: number,
  grossLoss: number,
  closed: number,
  netFromCloses: number,
) {
  const avgWin = winCount > 0 ? grossWin / winCount : null;
  const avgLoss = lossCount > 0 ? grossLoss / lossCount : null;
  const payoffRatio =
    avgWin != null && avgLoss != null && avgLoss > 1e-12 ? avgWin / avgLoss : null;
  const expectancy = closed > 0 ? netFromCloses / closed : null;
  const breakevenWrPct =
    avgWin != null && avgLoss != null && avgWin + avgLoss > 1e-12
      ? (avgLoss / (avgWin + avgLoss)) * 100
      : null;
  const decisive = winCount + lossCount;
  const decisiveWinRatePct = decisive > 0 ? (winCount / decisive) * 100 : null;
  return { avgWin, avgLoss, payoffRatio, expectancy, breakevenWrPct, decisiveWinRatePct };
}

export function emptyClosedTradePerf(): ClosedTradePerfVm {
  return {
    winRatePct: 0,
    profitFactor: null,
    grossWin: 0,
    grossLoss: 0,
    netFromCloses: 0,
    closed: 0,
    winCount: 0,
    lossCount: 0,
    breakevenCount: 0,
    decisiveWinRatePct: null,
    avgWin: null,
    avgLoss: null,
    payoffRatio: null,
    expectancy: null,
    breakevenWrPct: null,
  };
}

/** Authoritative win-rate KPI rollup from backend ``KpiDTO`` (rolling or session). */
export function closedTradePerfFromKpi(
  scope: "rolling" | "session",
  kpi: KpiDTO,
): ClosedTradePerfVm {
  if (scope === "session") {
    const wins = kpi.session_close_wins;
    const losses = kpi.session_close_losses;
    const be = kpi.session_close_breakevens;
    const closed = wins + losses + be;
    if (!closed) {
      return emptyClosedTradePerf();
    }
    const gw = kpi.gross_win_pnl_session;
    const gl = kpi.gross_loss_pnl_session;
    const netFromCloses = gw - gl;
    return {
      winRatePct: kpi.win_rate_session,
      profitFactor: kpi.profit_factor_session,
      grossWin: gw,
      grossLoss: gl,
      netFromCloses,
      closed,
      winCount: wins,
      lossCount: losses,
      breakevenCount: be,
      ...derivePayoffMetrics(wins, losses, gw, gl, closed, netFromCloses),
    };
  }

  const wins = kpi.rolling_close_wins;
  const losses = kpi.rolling_close_losses;
  const be = kpi.rolling_close_breakevens;
  const closed = wins + losses + be;
  if (!closed) {
    return emptyClosedTradePerf();
  }
  const gw = kpi.gross_win_pnl;
  const gl = kpi.gross_loss_pnl;
  const netFromCloses = gw - gl;
  return {
    winRatePct: kpi.win_rate,
    profitFactor: kpi.profit_factor,
    grossWin: gw,
    grossLoss: gl,
    netFromCloses,
    closed,
    winCount: wins,
    lossCount: losses,
    breakevenCount: be,
    ...derivePayoffMetrics(wins, losses, gw, gl, closed, netFromCloses),
  };
}
