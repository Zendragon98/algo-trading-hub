/** Buffer reducing fills by parent until the VWAP parent completes. */

import type { Trade } from "@/components/algo/types";
import type { KpiDTO } from "@/lib/api";

export type PendingParentClose = {
  symbol: string;
  side: "buy" | "sell";
  totalPnl: number;
  totalQty: number;
  exitNotional: number;
  entryPrice: number | null;
  ts: string;
  strategyName: string;
  strategyContributions: Record<string, number>;
  excludeFromStreak?: boolean;
};

export function accumulateParentClose(
  pending: Map<string, PendingParentClose>,
  parentId: string,
  trade: Trade,
  pnl: number,
): void {
  let acc = pending.get(parentId);
  if (!acc) {
    acc = {
      symbol: trade.symbol,
      side: trade.side,
      totalPnl: 0,
      totalQty: 0,
      exitNotional: 0,
      entryPrice: trade.entryPrice,
      ts: trade.ts,
      strategyName: trade.strategyName,
      strategyContributions: trade.strategyContributions,
    };
    pending.set(parentId, acc);
  }
  acc.totalPnl += pnl;
  acc.totalQty += trade.qty;
  if (trade.exitPrice != null) {
    acc.exitNotional += trade.exitPrice * trade.qty;
  }
  if (acc.entryPrice == null && trade.entryPrice != null) {
    acc.entryPrice = trade.entryPrice;
  }
  acc.ts = trade.ts;
}

export function finalizeParentCloseTrade(
  pending: Map<string, PendingParentClose>,
  parentId: string,
): Trade | null {
  const acc = pending.get(parentId);
  if (!acc || acc.totalQty <= 0) {
    pending.delete(parentId);
    return null;
  }
  pending.delete(parentId);
  const exitVwap = acc.exitNotional / acc.totalQty;
  return {
    id: parentId,
    ts: acc.ts,
    symbol: acc.symbol,
    side: acc.side,
    qty: acc.totalQty,
    price: exitVwap,
    action: "close",
    entryPrice: acc.entryPrice,
    exitPrice: exitVwap,
    pnl: acc.totalPnl,
    strategyName: acc.strategyName,
    strategyContributions: acc.strategyContributions,
  };
}

export function appendRealizedClose(
  prevRealized: Trade[],
  trade: Trade,
  cap: number,
): Trade[] {
  return [trade, ...prevRealized].slice(0, cap);
}

export function rollingKpiFromRealized(realized: Trade[], kpi: KpiDTO): KpiDTO {
  let winCount = 0;
  let lossCount = 0;
  let breakevenCount = 0;
  let grossWin = 0;
  let grossLoss = 0;
  for (const t of realized) {
    const p = t.pnl ?? 0;
    if (p > 0) {
      grossWin += p;
      winCount += 1;
    } else if (p < 0) {
      grossLoss -= p;
      lossCount += 1;
    } else {
      breakevenCount += 1;
    }
  }
  return {
    ...kpi,
    win_rate: realized.length > 0 ? (winCount / realized.length) * 100 : 0,
    gross_win_pnl: grossWin,
    gross_loss_pnl: grossLoss,
    profit_factor: grossLoss > 0 ? grossWin / grossLoss : null,
    rolling_close_wins: winCount,
    rolling_close_losses: lossCount,
    rolling_close_breakevens: breakevenCount,
  };
}

export function bumpKpiOnRealizedClose(kpi: KpiDTO, pnl: number): KpiDTO {
  const wins = kpi.session_close_wins + (pnl > 0 ? 1 : 0);
  const losses = kpi.session_close_losses + (pnl < 0 ? 1 : 0);
  const breakevens = kpi.session_close_breakevens + (pnl === 0 ? 1 : 0);
  const closed = wins + losses + breakevens;
  const grossWin = kpi.gross_win_pnl_session + (pnl > 0 ? pnl : 0);
  const grossLoss = kpi.gross_loss_pnl_session + (pnl < 0 ? -pnl : 0);
  return {
    ...kpi,
    session_close_wins: wins,
    session_close_losses: losses,
    session_close_breakevens: breakevens,
    win_rate_session: closed > 0 ? (wins / closed) * 100 : 0,
    gross_win_pnl_session: grossWin,
    gross_loss_pnl_session: grossLoss,
    profit_factor_session: grossLoss > 0 ? grossWin / grossLoss : null,
  };
}
