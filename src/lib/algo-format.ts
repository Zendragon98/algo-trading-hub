/** Shared formatters for the algo console KPIs and trade tables. */

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

export function formatSignedRealizedPnl(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  const sign = n >= 0 ? "+" : "−";
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
  return { avgWin, avgLoss, payoffRatio, expectancy, breakevenWrPct };
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
    avgWin: null,
    avgLoss: null,
    payoffRatio: null,
    expectancy: null,
    breakevenWrPct: null,
  };
}
