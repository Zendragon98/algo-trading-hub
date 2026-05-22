import { createFileRoute, Link } from "@tanstack/react-router";
import { Activity, ArrowLeft, Download, Play } from "lucide-react";
import { useMemo, useState } from "react";

import { EquityChart } from "@/components/algo/EquityChart";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useBacktest } from "@/hooks/useBacktest";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/backtesting")({
  component: BacktestingPage,
});

const STRATEGIES = [
  { id: "sma", label: "SMA Crossover" },
  { id: "blend", label: "Blended Signals" },
  { id: "pairs", label: "Pairs Trading" },
] as const;

function BacktestingPage() {
  const bt = useBacktest();
  const [strategy, setStrategy] = useState<string>("sma");
  const [dataset, setDataset] = useState("library");
  const [symbols, setSymbols] = useState("BTCUSDT");
  const [smaFast, setSmaFast] = useState("5");
  const [smaSlow, setSmaSlow] = useState("20");

  const datasetOptions = useMemo(() => {
    const opts = [{ value: "library", label: "Merged library (live + downloads)" }];
    for (const s of bt.sessions) {
      opts.push({ value: `run:${s.runId}`, label: `Live session ${s.label}` });
    }
    return opts;
  }, [bt.sessions]);

  const overrides = useMemo(() => {
    if (strategy === "sma") {
      return {
        sma_fast_window: Number(smaFast) || 5,
        sma_slow_window: Number(smaSlow) || 20,
        sma_symbols: symbols.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean),
      };
    }
    return {};
  }, [strategy, smaFast, smaSlow, symbols]);

  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="sticky top-0 z-20 border-b border-border bg-background/85 backdrop-blur">
        <div className="mx-auto flex max-w-[1500px] items-center justify-between gap-4 px-4 py-3 lg:px-8">
          <div className="flex items-center gap-3">
            <Link
              to="/"
              className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
            >
              <ArrowLeft className="size-3" /> Live console
            </Link>
            <div className="flex items-center gap-2">
              <Activity className="size-4 text-bull" />
              <span className="text-sm font-semibold tracking-wide">Backtesting</span>
            </div>
          </div>
          <p className="hidden text-[11px] text-muted-foreground md:block">
            1m bars from live capture or Binance download · simulated fills
          </p>
        </div>
      </header>

      <main className="mx-auto max-w-[1500px] space-y-4 px-4 py-6 lg:px-8">
        {bt.error && (
          <div className="rounded-sm border border-bear/40 bg-bear/10 px-4 py-2 text-sm text-bear">
            {bt.error}
          </div>
        )}

        <div className="grid gap-4 lg:grid-cols-2">
          <Panel title="Datasets">
            <div className="space-y-3 p-4">
              <div className="flex flex-wrap gap-2">
                <Input
                  value={symbols}
                  onChange={(e) => setSymbols(e.target.value)}
                  placeholder="BTCUSDT,ETHUSDT"
                  className="max-w-xs"
                />
                <Button
                  size="sm"
                  variant="outline"
                  disabled={bt.loading}
                  onClick={() => void bt.download(symbols.split(",").map((s) => s.trim()).filter(Boolean), 7)}
                >
                  <Download className="size-4" /> Download 7d
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={bt.loading}
                  onClick={() => void bt.download(symbols.split(",").map((s) => s.trim()).filter(Boolean), 30)}
                >
                  <Download className="size-4" /> Download 30d
                </Button>
              </div>
              <div className="max-h-48 overflow-auto text-xs">
                {bt.datasets.length === 0 ? (
                  <p className="text-muted-foreground">
                    No data yet. Run the engine to capture live 1m bars, or download history above.
                  </p>
                ) : (
                  <table className="w-full">
                    <thead>
                      <tr className="text-left text-muted-foreground">
                        <th className="py-1">Symbol</th>
                        <th>Rows</th>
                        <th>Source</th>
                        <th>Range</th>
                      </tr>
                    </thead>
                    <tbody>
                      {bt.datasets.map((d) => (
                        <tr key={`${d.symbol}-${d.interval}`} className="border-t border-border/50">
                          <td className="py-1 font-mono">{d.symbol}</td>
                          <td>{d.rows}</td>
                          <td>
                            <SourceBadge source={d.source} />
                          </td>
                          <td className="text-muted-foreground">
                            {d.start ? `${d.start.slice(0, 16)} …` : "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          </Panel>

          <Panel title="Run backtest">
            <div className="space-y-4 p-4">
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <Label className="text-xs">Strategy</Label>
                  <Select value={strategy} onValueChange={setStrategy}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {STRATEGIES.map((s) => (
                        <SelectItem key={s.id} value={s.id}>
                          {s.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div>
                  <Label className="text-xs">Dataset</Label>
                  <Select value={dataset} onValueChange={setDataset}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {datasetOptions.map((o) => (
                        <SelectItem key={o.value} value={o.value}>
                          {o.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>
              {strategy === "sma" && (
                <div className="grid gap-3 sm:grid-cols-3">
                  <div>
                    <Label className="text-xs">Fast SMA</Label>
                    <Input value={smaFast} onChange={(e) => setSmaFast(e.target.value)} />
                  </div>
                  <div>
                    <Label className="text-xs">Slow SMA</Label>
                    <Input value={smaSlow} onChange={(e) => setSmaSlow(e.target.value)} />
                  </div>
                  <div>
                    <Label className="text-xs">Symbols</Label>
                    <Input value={symbols} onChange={(e) => setSymbols(e.target.value)} />
                  </div>
                </div>
              )}
              <Button disabled={bt.loading} onClick={() => void bt.run({ strategy, dataset, settingsOverrides: overrides })}>
                <Play className="size-4" /> {bt.loading ? "Running…" : "Run backtest"}
              </Button>
            </div>
          </Panel>
        </div>

        {bt.result && (
          <>
            <div className="grid gap-3 sm:grid-cols-4">
              <Kpi label="Return" value={`${bt.result.metrics.totalReturnPct.toFixed(2)}%`} />
              <Kpi label="Max DD" value={`${bt.result.metrics.maxDrawdownPct.toFixed(2)}%`} />
              <Kpi label="Trades" value={String(bt.result.metrics.tradeCount)} />
              <Kpi label="Win rate" value={`${(bt.result.metrics.winRate * 100).toFixed(1)}%`} />
            </div>
            <Panel title="Equity curve" className="h-64">
              <div className="h-full p-2">
                <EquityChart data={bt.result.equityCurve} />
              </div>
            </Panel>
            {bt.result.notes.length > 0 && (
              <p className="text-xs text-muted-foreground">{bt.result.notes.join(" · ")}</p>
            )}
            <Panel title="Simulated trades">
              <div className="max-h-72 overflow-auto p-4 text-xs">
                <table className="w-full">
                  <thead>
                    <tr className="text-left text-muted-foreground">
                      <th>Symbol</th>
                      <th>Side</th>
                      <th>Qty</th>
                      <th>Price</th>
                      <th>PnL</th>
                      <th>Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {bt.result.fills.map((f, i) => (
                      <tr key={i} className="border-t border-border/50 font-mono">
                        <td className="py-1">{f.symbol}</td>
                        <td>{f.side}</td>
                        <td>{f.qty.toFixed(6)}</td>
                        <td>{f.price.toFixed(4)}</td>
                        <td className={cn(f.pnl >= 0 ? "text-bull" : "text-bear")}>
                          {f.pnl.toFixed(2)}
                        </td>
                        <td className="max-w-[200px] truncate text-muted-foreground">{f.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>
          </>
        )}
      </main>
    </div>
  );
}

function SourceBadge({ source }: { source: string }) {
  const variant =
    source === "live" ? "border-bull/50 text-bull" : source === "download" ? "border-warning/50 text-warning" : "";
  return (
    <Badge variant="outline" className={variant}>
      {source}
    </Badge>
  );
}

function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-sm border border-border bg-card/60 px-4 py-3">
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className="text-lg font-semibold tabular-nums">{value}</div>
    </div>
  );
}

function Panel({
  title,
  children,
  className,
}: {
  title: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("overflow-hidden rounded-sm border border-border bg-card/60", className)}>
      <div className="border-b border-border px-4 py-2">
        <h2 className="text-[11px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">{title}</h2>
      </div>
      {children}
    </div>
  );
}
