import { CircleDot, Gauge, TrendingDown, TrendingUp } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "@/lib/utils";
import {
  type ClosedTradePerfVm,
  EM_DASH,
  formatNegativeUsd,
  formatSignedRealizedPnl,
  formatUsdPayoffCell,
} from "@/lib/algo-format";

const TIMES = "\u00D7";
export function WinRateKpiCard({
  perf,
  scope,
  onScopeChange,
  tapeStats,
  openPositionCount,
}: {
  perf: ClosedTradePerfVm;
  scope: "rolling" | "session";
  onScopeChange: (s: "rolling" | "session") => void;
  tapeStats: { fills: number; opens: number; closes: number; closesWithoutPnl: number };
  openPositionCount: number;
}) {
  const {
    closed,
    winRatePct,
    profitFactor,
    grossWin,
    grossLoss,
    netFromCloses,
    winCount,
    lossCount,
    breakevenCount,
    avgWin,
    avgLoss,
    payoffRatio,
    expectancy,
    breakevenWrPct,
  } = perf;

  const winSeg = closed > 0 ? (winCount / closed) * 100 : 0;
  const lossSeg = closed > 0 ? (lossCount / closed) * 100 : 0;
  const flatSeg = closed > 0 ? (breakevenCount / closed) * 100 : 0;

  const dollarDen = grossWin + grossLoss;
  const bullDollarPct = dollarDen > 1e-12 ? Math.min(100, (grossWin / dollarDen) * 100) : 50;

  const netTone =
    netFromCloses > 0 ? "text-bull" : netFromCloses < 0 ? "text-bear" : "text-muted-foreground";
  const netFormatted =
    netFromCloses >= 0
      ? `+$${formatUsdPayoffCell(netFromCloses)}`
      : formatNegativeUsd(netFromCloses);

  const expectancyTone =
    expectancy != null && expectancy > 0
      ? "text-bull"
      : expectancy != null && expectancy < 0
        ? "text-bear"
        : "text-muted-foreground";
  const expectancyFormatted =
    expectancy != null ? formatSignedRealizedPnl(expectancy) : EM_DASH;

  const wrVsBreakeven =
    breakevenWrPct != null
      ? winRatePct >= breakevenWrPct - 0.05
        ? "at-or-above"
        : "below"
      : null;

  return (
    <div className="relative overflow-hidden rounded-sm border border-border bg-card/60 p-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex items-center justify-between gap-2 text-[10px] uppercase tracking-[0.18em] text-muted-foreground sm:justify-start">
          <span className="flex items-center gap-1.5 font-mono">
            <Gauge className="size-4" strokeWidth={2} />
            Win rate · payoff
          </span>
          <CircleDot className="size-3 shrink-0 opacity-40 sm:hidden" />
        </div>
        <div className="flex flex-col items-stretch gap-1 sm:items-end">
          <ToggleGroup
            type="single"
            value={scope}
            onValueChange={(v) => {
              if (v === "rolling" || v === "session") onScopeChange(v);
            }}
            variant="outline"
            size="sm"
            className="self-end"
          >
            <ToggleGroupItem value="rolling" className="px-2 text-[9px] font-mono">
              Last 200
            </ToggleGroupItem>
            <ToggleGroupItem value="session" className="px-2 text-[9px] font-mono">
              Session
            </ToggleGroupItem>
          </ToggleGroup>
        </div>
      </div>

      {!closed ? (
        <div className="mt-10 space-y-2 pb-8 text-center text-xs text-muted-foreground">
          <p>
            {scope === "session"
              ? "No reducing fills with realized P&L this session yet."
              : "No reducing fills with realized P&L in the last 200 closes."}
          </p>
          <p className="mx-auto max-w-sm text-[10px] leading-relaxed opacity-90">
            {tapeStats.fills > 0 ? (
              <>
                {tapeStats.fills} fill{tapeStats.fills === 1 ? "" : "s"} on tape (
                {tapeStats.opens} open{tapeStats.opens === 1 ? "" : "s"}
                {tapeStats.closes > 0
                  ? `, ${tapeStats.closes} close${tapeStats.closes === 1 ? "" : "s"}`
                  : ""}
                {tapeStats.closesWithoutPnl > 0
                  ? ` (${tapeStats.closesWithoutPnl} without P&L)`
                  : ""}
                ).{" "}
              </>
            ) : null}
            Last 200 rolls slice fills into one close per parent; Session counts every reducing fill
            open P&L
            {openPositionCount > 0
              ? ` (${openPositionCount} leg${openPositionCount === 1 ? "" : "s"} still open).`
              : "."}
          </p>
        </div>
      ) : (
        <>
          <div className="mt-3 flex items-end justify-between gap-3">
            <div className="text-4xl font-mono font-semibold tabular-nums leading-none tracking-tight text-foreground">
              {winRatePct.toFixed(1)}
              <span className="align-top text-xl font-semibold text-muted-foreground">%</span>
            </div>
            <div className="flex flex-shrink-0 flex-wrap justify-end gap-1">
              <Badge variant="outline" className="h-6 border-bull/35 bg-bull/10 px-1.5 font-mono text-[10px] text-bull">
                {winCount}W
              </Badge>
              <Badge variant="outline" className="h-6 border-bear/35 bg-bear/10 px-1.5 font-mono text-[10px] text-bear">
                {lossCount}L
              </Badge>
              {breakevenCount ? (
                <Badge variant="outline" className="h-6 border-muted-foreground/35 px-1.5 font-mono text-[10px] text-muted-foreground">
                  {breakevenCount}BE
                </Badge>
              ) : null}
            </div>
          </div>

          <div
            className="mt-2 flex h-2 w-full overflow-hidden rounded-full bg-muted/45"
            title="Share of realized closes: wins vs flat vs losses"
            role="img"
            aria-label={`Winning realized closes ${winSeg.toFixed(0)} percent, losses ${lossSeg.toFixed(0)} percent, breakevens ${flatSeg.toFixed(0)} percent`}
          >
            <div className="h-full bg-bull transition-[width] duration-500" style={{ width: `${winSeg}%` }} />
            <div
              className="h-full bg-muted-foreground/25 transition-[width] duration-500"
              style={{ width: `${flatSeg}%` }}
            />
            <div className="h-full bg-bear transition-[width] duration-500" style={{ width: `${lossSeg}%` }} />
          </div>
          <p className="mt-1 font-mono text-[10px] text-muted-foreground">
            {scope === "session" ? "Session · " : "Rolling (\u2264200) · "}
            {closed} realized closes · {winSeg.toFixed(0)} / {flatSeg.toFixed(0)} / {lossSeg.toFixed(0)}% W / BE / L
          </p>

          <div className="mt-4 rounded-md border border-border/55 bg-muted/10 p-2.5">
            <p className="mb-2 font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
              Payoff profile
            </p>
            <div className="grid grid-cols-2 gap-x-3 gap-y-2.5">
              <div>
                <p className="font-mono text-[9px] uppercase tracking-wide text-muted-foreground">Avg win</p>
                <p className="mt-0.5 font-mono text-sm tabular-nums text-bull">
                  {avgWin != null ? `+$${formatUsdPayoffCell(avgWin)}` : EM_DASH}
                </p>
              </div>
              <div className="text-right">
                <p className="font-mono text-[9px] uppercase tracking-wide text-muted-foreground">Avg loss</p>
                <p className="mt-0.5 font-mono text-sm tabular-nums text-bear">
                  {avgLoss != null ? formatNegativeUsd(avgLoss) : EM_DASH}
                </p>
              </div>
              <div>
                <p className="font-mono text-[9px] uppercase tracking-wide text-muted-foreground">Payoff (R)</p>
                <p
                  className={cn(
                    "mt-0.5 font-mono text-sm tabular-nums",
                    payoffRatio != null && payoffRatio >= 1
                      ? "text-bull"
                      : payoffRatio != null
                        ? "text-bear"
                        : "text-muted-foreground",
                  )}
                  title="Average win / average loss - how much you make per $1 lost"
                >
                  {payoffRatio != null ? (
                    <>
                      {payoffRatio.toFixed(2)}
                      <span className="text-xs text-muted-foreground">{TIMES}</span>
                    </>
                  ) : (
                    EM_DASH
                  )}
                </p>
              </div>
              <div className="text-right">
                <p className="font-mono text-[9px] uppercase tracking-wide text-muted-foreground">Expectancy</p>
                <p
                  className={cn("mt-0.5 font-mono text-sm tabular-nums", expectancyTone)}
                  title="Net P&L per realized close"
                >
                  {expectancyFormatted}
                  {expectancy != null ? (
                    <span className="text-[10px] font-normal text-muted-foreground">/close</span>
                  ) : null}
                </p>
              </div>
            </div>
            {breakevenWrPct != null ? (
              <p
                className={cn(
                  "mt-2 border-t border-border/40 pt-2 font-mono text-[10px] leading-snug",
                  wrVsBreakeven === "at-or-above" ? "text-bull/90" : "text-bear/90",
                )}
              >
                Breakeven WR{" "}
                <span className="tabular-nums text-foreground">{breakevenWrPct.toFixed(1)}%</span>
                <span className="text-muted-foreground"> at this avg win/loss · actual </span>
                <span className="tabular-nums text-foreground">{winRatePct.toFixed(1)}%</span>
                <span className="text-muted-foreground">
                  {wrVsBreakeven === "at-or-above"
                    ? " (at or above)"
                    : " (below breakeven - need higher WR or larger wins)"}
                </span>
              </p>
            ) : null}
          </div>

          <div className="mt-4 space-y-2">
            <div className="flex items-center justify-between gap-2 text-[10px] font-mono uppercase tracking-wide text-muted-foreground">
              <span className="flex items-center gap-1">
                <TrendingUp className="size-3 text-bull" />
                Gross wins
              </span>
              <span className="flex items-center gap-1">
                Gross losses
                <TrendingDown className="size-3 text-bear" />
              </span>
            </div>

            <div
              className="flex h-2.5 w-full overflow-hidden rounded-md bg-muted/45"
              title="Relative dollar magnitude: winning closes vs losing closes"
              role="img"
              aria-label={`Winning closes about ${bullDollarPct.toFixed(0)} percent of payoff dollars`}
            >
              <div
                className="h-full shrink-0 rounded-l-md bg-bull shadow-[inset_0_1px_0_rgba(255,255,255,0.12)] transition-[width] duration-500"
                style={{ width: `${bullDollarPct}%` }}
              />
              <div className="h-full min-w-0 flex-1 rounded-r-md bg-bear shadow-[inset_0_-1px_0_rgba(0,0,0,0.35)]" />
            </div>

            <div className="flex items-baseline justify-between gap-3 font-mono text-sm tabular-nums">
              <span className="text-bull">{`+$${formatUsdPayoffCell(grossWin)}`}</span>
              <span className="text-bear">{formatNegativeUsd(grossLoss)}</span>
            </div>
          </div>

          <div
            className={cn(
              "mt-4 flex items-center justify-between rounded-md border px-3 py-2 font-mono",
              profitFactor != null && profitFactor >= 1
                ? "border-bull/30 bg-bull/10"
                : profitFactor != null
                  ? "border-bear/30 bg-bear/10"
                  : "border-border bg-muted/20",
            )}
          >
            <span className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">Profit factor</span>
            <span className="text-xl tabular-nums tracking-tight">
              {profitFactor != null ? (
                <>
                  {profitFactor.toFixed(2)}
                  <span className="text-sm text-muted-foreground">{TIMES}</span>
                </>
              ) : grossWin > 0 && grossLoss <= 1e-12 ? (
                <>
                  {"\u221E"}
                  <span className="text-sm text-muted-foreground">{TIMES}</span>
                </>
              ) : (
                <span className="text-muted-foreground">{EM_DASH}</span>
              )}
            </span>
          </div>

          <div className="mt-3 flex items-center justify-between border-t border-border/60 pt-2 font-mono text-xs">
            <span className="text-muted-foreground">Net · realized closes</span>
            <span className={cn("tabular-nums font-semibold", netTone)}>{netFormatted}</span>
          </div>

          <details className="group mt-2 border border-border/50 bg-muted/15 font-mono text-[10px] leading-relaxed text-muted-foreground [&_summary::-webkit-details-marker]:hidden">
            <summary className="cursor-pointer select-none px-2 py-1.5 text-[10px] uppercase tracking-wide hover:bg-muted/30">
              <span className="text-muted-foreground">Methodology · </span>
              <span className="normal-case tracking-normal opacity-70">PnL sources & factor definition</span>
            </summary>
            <div className="border-t border-border/40 px-2 py-2 text-[10px]">
              <strong className="text-foreground">Rolling</strong> is the last {"\u2264"}200 realized-PnL closes;{" "}
              <strong className="text-foreground">Session</strong> is all such closes since the backend process started (a
              restart resets it). Session KPI values refresh with{" "}
              <code className="rounded bg-muted/60 px-0.5">GET /api/state</code> (about every 5s). The rolling view matches live
              WebSocket fills. Binance Futures uses field{" "}
              <code className="rounded bg-muted/60 px-0.5">rp</code> when it is non-zero; otherwise the console uses{" "}
              <span className="whitespace-nowrap">(exit - entry) {TIMES} closed qty</span>. If{" "}
              <code className="rounded bg-muted/60 px-0.5">rp</code> looks like dust vs that economics (e.g. sub-cent vs several
              dollars), the engine keeps the computed slice PnL. <strong className="text-foreground">Avg win/loss</strong> are
              mean P&L on winning vs losing closes; <strong className="text-foreground">payoff (R)</strong> = avg win / avg
              loss; <strong className="text-foreground">expectancy</strong> = net / closes;{" "}
              <strong className="text-foreground">breakeven WR</strong> = avg loss / (avg win + avg loss). Profit factor =
              {"\u03A3"} positive closes / {"\u03A3"} |negative closes|. Excludes transfers, funding, and fees unless the venue folds
              them into{' '}
              <code className="rounded bg-muted/60 px-0.5">rp</code>. Dollar labels use extra precision when totals are small
              so they reconcile with the factor; RECENT TRADES uses the same idea so tiny realized amounts are not shown as
              <span className="whitespace-nowrap">+0.00</span>.
            </div>
          </details>
        </>
      )}
    </div>
  );
}

export function KpiCard({
  icon,
  label,
  value,
  sub,
  tone,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  sub: React.ReactNode;
  tone: "bull" | "bear" | "neutral";
}) {
  const subColor =
    tone === "bull" ? "text-bull" : tone === "bear" ? "text-bear" : "text-muted-foreground";
  return (
    <div className="relative overflow-hidden rounded-sm border border-border bg-card/60 p-4">
      <div className="flex items-center justify-between text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
        <span className="flex items-center gap-1.5">{icon}{label}</span>
        <CircleDot className="size-3 opacity-40" />
      </div>
      <div className="mt-2 font-mono text-2xl font-semibold tabular-nums">{value}</div>
      <div className={cn("mt-1 text-xs tabular-nums", subColor)}>{sub}</div>
    </div>
  );
}

