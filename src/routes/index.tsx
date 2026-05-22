import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ChevronDown,
  CircleDot,
  Cpu,
  Download,
  Gauge,
  ListOrdered,
  Loader2,
  Pause,
  Play,
  Power,
  RefreshCcw,
  Settings2,
  ShieldAlert,
  Square,
  Target,
  TrendingDown,
  TrendingUp,
  Wallet,
  Wifi,
  Zap,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "@/lib/utils";
import { EquityChart } from "@/components/algo/EquityChart";
import { PositionChartDialog } from "@/components/algo/PositionChartDialog";
import { SettingsDialog } from "@/components/algo/SettingsDialog";
import type {
  AlgoStatus,
  BreakerList,
  StartupProgress,
  BreakerStatus,
  ExecutionAggregate,
  ExecutionParent,
  LogEntry,
  Position,
  StrategyInfo,
  SystemHealth,
  Trade,
  WorkingOrder,
} from "@/components/algo/types";
import { useAlgoStream } from "@/hooks/useAlgoStream";
import { api } from "@/lib/api";

function formatUsdRough(n: number): string {
  return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** Extra decimals when dollars are tiny so payoff ÷ totals stay consistent with the factor. */
function formatUsdPayoffCell(n: number): string {
  const a = Math.abs(n);
  if (a >= 100)
    return a.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (a >= 1) return n.toFixed(2);
  if (a >= 0.01) return n.toFixed(4);
  if (a > 0) return n.toFixed(6);
  return "0.00";
}

/** Realized PnL on each close row — extra decimals when |$| is tiny (avoids ``toFixed(2)`` → ``+0.00``). */
function formatSignedRealizedPnl(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  const sign = n >= 0 ? "+" : "−";
  return `${sign}$${formatUsdPayoffCell(Math.abs(n))}`;
}

export const Route = createFileRoute("/")({
  component: Index,
  head: () => ({
    meta: [
      { title: "Algo Trading Console" },
      {
        name: "description",
        content: "Live console for monitoring and controlling your crypto trading algorithm.",
      },
    ],
  }),
});

type ClosedTradePerfVm = {
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

function derivePayoffMetrics(
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

function Index() {
  const live = useAlgoStream();
  const status: AlgoStatus = live.status;
  const startupProgress = live.startupProgress;
  const bookResyncProgress = live.bookResyncProgress;
  const paperMode: boolean = live.paperMode;
  const strategy: StrategyInfo | null = live.strategy;
  const strategies: StrategyInfo[] = live.strategies;
  const equity: number[] = live.equity;
  const positions: Position[] = live.positions;
  const trades: Trade[] = live.trades;
  const realizedTrades: Trade[] = live.realizedTrades;
  const logs: LogEntry[] = live.logs;
  const uptimeSec: number = live.uptimeSec;
  const workingOrders: WorkingOrder[] = live.orders;
  const workingParents: ExecutionParent[] = live.workingParents;
  const executionHistory: ExecutionParent[] = live.executionHistory;
  const executionAggregate: ExecutionAggregate = live.executionAggregate;
  const systemHealth = live.systemHealth;
  const maxRiskPct = live.maxRiskPct;
  const maxGrossNotional = live.maxGrossNotional;
  const breakers = live.breakers;
  const replaySummary = live.replaySummary;
  const kpi = live.kpi;
  const backendReachable = live.backendReachable;
  const backendError = live.error;
  const streamConnected = live.connected;

  const HEALTH_EXPANDED_STORAGE = "algo-health-expanded";
  const [healthExpanded, setHealthExpanded] = useState(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem(HEALTH_EXPANDED_STORAGE) === "true";
  });
  useEffect(() => {
    window.localStorage.setItem(HEALTH_EXPANDED_STORAGE, healthExpanded ? "true" : "false");
  }, [healthExpanded]);

  const KPI_SCOPE_STORAGE = "algo-kpi-window";
  const [kpiScope, setKpiScope] = useState<"rolling" | "session">(() => {
    if (typeof window === "undefined") return "rolling";
    return window.localStorage.getItem(KPI_SCOPE_STORAGE) === "session" ? "session" : "rolling";
  });
  useEffect(() => {
    window.localStorage.setItem(KPI_SCOPE_STORAGE, kpiScope);
  }, [kpiScope]);

  const [risk, setRisk] = useState<number[]>([35]);
  const riskHydrated = useRef(false);
  const [autoCompound, setAutoCompound] = useState(true);
  const [chartSymbol, setChartSymbol] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [controlPending, setControlPending] = useState<"start" | null>(null);

  useEffect(() => {
    if (riskHydrated.current || maxRiskPct <= 0) return;
    setRisk([Math.round(maxRiskPct * 100)]);
    riskHydrated.current = true;
  }, [maxRiskPct]);

  const totalEquity = equity.length ? equity[equity.length - 1] : 0;
  const startEquity = equity.length ? equity[0] : 0;
  const pnlAbs = totalEquity - startEquity;
  const pnlPct = startEquity > 0 ? (pnlAbs / startEquity) * 100 : 0;

  const openPnl = useMemo(() => {
    if (systemHealth != null) {
      return systemHealth.unrealizedPnl;
    }
    return positions.reduce((acc, p) => acc + p.unrealizedPnl, 0);
  }, [systemHealth, positions]);

  const emptyPerf = (): ClosedTradePerfVm => ({
    winRatePct: 0,
    profitFactor: null as number | null,
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
  });

  /** Rolling = last ≤200 realized closes; session = all realized closes since backend start. */
  const closedTradePerf = useMemo(() => {
    if (kpiScope === "session") {
      const wins = kpi.session_close_wins;
      const losses = kpi.session_close_losses;
      const be = kpi.session_close_breakevens;
      const closed = wins + losses + be;
      if (!closed) {
        return emptyPerf();
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

    const closed =
      realizedTrades.length > 0
        ? realizedTrades
        : trades.filter((t) => t.action === "close" && t.pnl != null);
    if (!closed.length) {
      return emptyPerf();
    }
    let grossWin = 0;
    let grossLoss = 0;
    let winCount = 0;
    let lossCount = 0;
    let breakevenCount = 0;
    for (const t of closed) {
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
    const profitFactor = grossLoss > 0 ? grossWin / grossLoss : null;
    const netFromCloses = grossWin - grossLoss;
    const winRatePct = (winCount / closed.length) * 100;
    return {
      winRatePct,
      profitFactor,
      grossWin,
      grossLoss,
      netFromCloses,
      closed: closed.length,
      winCount,
      lossCount,
      breakevenCount,
      ...derivePayoffMetrics(
        winCount,
        lossCount,
        grossWin,
        grossLoss,
        closed.length,
        netFromCloses,
      ),
    };
  }, [kpiScope, kpi, realizedTrades, trades]);

  const winRateTapeStats = useMemo(() => {
    let opens = 0;
    let closes = 0;
    let closesWithoutPnl = 0;
    for (const t of trades) {
      if (t.action === "close") {
        closes += 1;
        if (t.pnl == null) closesWithoutPnl += 1;
      } else {
        opens += 1;
      }
    }
    return { fills: trades.length, opens, closes, closesWithoutPnl };
  }, [trades]);

  // Fire-and-forget control commands. The engine drives the next status
  // update over the WebSocket so we don't optimistically mutate React state.
  const handleControl = (fn: () => Promise<unknown>) => {
    fn().catch((err) => {
      console.error("control command failed", err);
    });
  };

  const onStart = () => {
    if (status === "running" || status === "starting" || controlPending === "start") return;
    setControlPending("start");
    handleControl(() =>
      api.start().finally(() => {
        setControlPending(null);
      }),
    );
  };

  useEffect(() => {
    if (controlPending === "start" && (status === "starting" || status === "running")) {
      setControlPending(null);
    }
  }, [controlPending, status]);
  const onPause = () => handleControl(api.pause);
  const onStop = () => handleControl(api.stop);
  const onKill = () => {
    const ok = window.confirm(
      "Kill flattens positions, stops the engine, and exits the backend process.\n\n" +
        "The dashboard cannot start trading again until you restart the API " +
        "(e.g. python backend/main.py).\n\nContinue?",
    );
    if (!ok) return;
    handleControl(async () => {
      try {
        await api.shutdown();
      } catch (err) {
        console.error("shutdown failed", err);
      } finally {
        live.markBackendOffline(
          "Backend stopped (Kill). Restart the API, then press Start.",
        );
      }
    });
  };
  const onHaltTrading = () =>
    handleControl(async () => {
      await api.tripBreakers();
      await live.refresh();
    });
  const onFlatten = () => handleControl(api.flatten);

  // Push the slider's percentage (0-100) to the engine as a fraction.
  const onRiskCommit = (value: number[]) => {
    setRisk(value);
    handleControl(() => api.setRisk(value[0] / 100));
  };

  // Hot-swap the active strategy. The /api/state response drives the
  // ``active`` flag; we re-hydrate immediately so the UI flips without
  // waiting for the next status push.
  const onSelectStrategy = (name: string) => {
    if (strategy?.name === name) return;
    handleControl(async () => {
      await api.setStrategy(name);
      await live.refresh();
    });
  };

  const onRearmBreakers = (code?: string) => {
    handleControl(async () => {
      await api.rearmBreakers(code ? { code } : {});
      await live.refresh();
    });
  };

  const onExportReport = async () => {
    setExportError(null);
    try {
      const report = await api.reportsLatest();
      const blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `daily-report-${Date.now()}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setExportError((err as Error).message);
    }
  };

  const systemBusy =
    status === "starting" || controlPending === "start" || bookResyncProgress != null;
  const startDisabled =
    !backendReachable || status === "running" || systemBusy;

  return (
    <div className="min-h-screen text-foreground">
      {(startupProgress || bookResyncProgress || controlPending === "start") && (
        <StartupProgressBanner
          progress={
            startupProgress ??
            bookResyncProgress ?? {
              phase: "connect",
              label: "Starting engine…",
              done: 0,
              total: 0,
              symbol: null,
            }
          }
          variant={status === "starting" || controlPending === "start" ? "startup" : "resync"}
        />
      )}

      {!backendReachable && backendError && (
        <div
          role="alert"
          className="border-b border-bear/40 bg-bear/10 px-4 py-2 text-center text-xs text-bear lg:px-8"
        >
          <span className="font-medium uppercase tracking-wider">Backend offline</span>
          {" · "}
          {backendError ??
            "Restart the trading API, then use Start to resume. Kill exits the whole process."}
          {!streamConnected && backendReachable && " · Reconnecting live stream…"}
        </div>
      )}

      <TopBar
        status={status}
        uptimeSec={uptimeSec}
        paperMode={paperMode}
        strategy={strategy}
        backendReachable={backendReachable}
        controlsBusy={systemBusy}
        startDisabled={startDisabled}
        onStart={onStart}
        onPause={onPause}
        onKill={onKill}
        onHaltTrading={onHaltTrading}
        onFlatten={onFlatten}
        onOpenSettings={() => setSettingsOpen(true)}
      />

      <main className="mx-auto max-w-[1500px] px-4 pb-10 pt-6 lg:px-8">
        {/* KPI row */}
        <section className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <KpiCard
            icon={<Wallet className="size-4" />}
            label="EQUITY"
            value={`$${totalEquity.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
            sub={`${pnlAbs >= 0 ? "+" : ""}${pnlAbs.toFixed(2)} (${pnlPct.toFixed(2)}%)`}
            tone={pnlAbs >= 0 ? "bull" : "bear"}
          />
          <KpiCard
            icon={<Activity className="size-4" />}
            label="OPEN P&L"
            value={`${openPnl >= 0 ? "+" : ""}$${openPnl.toFixed(2)}`}
            sub={`${positions.length} open positions`}
            tone={openPnl >= 0 ? "bull" : "bear"}
          />
          <WinRateKpiCard
            perf={closedTradePerf}
            scope={kpiScope}
            onScopeChange={setKpiScope}
            tapeStats={winRateTapeStats}
            openPositionCount={positions.length}
          />
          <KpiCard
            icon={<Zap className="size-4" />}
            label="STRATEGY"
            value={strategy?.label ?? "—"}
            sub={
              strategy?.description ??
              (backendReachable ? "Loading..." : "Backend offline — restart API")
            }
            tone="neutral"
          />
        </section>

        {replaySummary ? (
          <section className="mt-4">
            <div className="rounded-md border border-bull/30 bg-bull/5 px-4 py-2 text-xs text-bull">
              {replaySummary}
            </div>
          </section>
        ) : null}

        <section className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
          {/* Equity chart */}
          <Panel
            className="lg:col-span-2"
            title="EQUITY CURVE"
            right={
              <div className="flex items-center gap-3 text-[11px] text-muted-foreground">
                <span className="flex items-center gap-1.5">
                  <span className="size-1.5 rounded-full bg-bull" /> realized
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="size-1.5 rounded-full bg-muted-foreground" /> mark
                </span>
                <span>· last {equity.length} ticks</span>
              </div>
            }
          >
            <div className="h-[280px] px-2 pb-2">
              <EquityChart data={equity} />
            </div>
          </Panel>

          {/* Controls */}
          <Panel
            title="CONTROL"
            right={
              <Badge variant="outline" className="border-border text-[10px] uppercase tracking-wider">
                <Settings2 className="mr-1 size-3" /> live
              </Badge>
            }
          >
            <div className="space-y-5 p-4">
              <div className="grid grid-cols-3 gap-2">
                <Button
                  onClick={onStart}
                  disabled={startDisabled}
                  className="bg-bull text-bull-foreground hover:bg-bull/90 disabled:opacity-40"
                >
                  {systemBusy ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <Play className="size-4" />
                  )}{" "}
                  {systemBusy ? "STARTING…" : "START"}
                </Button>
                <Button
                  onClick={onPause}
                  disabled={status !== "running" || systemBusy}
                  variant="secondary"
                  className="border border-border"
                >
                  <Pause className="size-4" /> PAUSE
                </Button>
                <Button
                  onClick={onStop}
                  disabled={status === "stopped" || systemBusy}
                  variant="destructive"
                >
                  <Square className="size-4" /> STOP
                </Button>
              </div>

              <Separator />

              <StrategyPicker
                strategies={strategies}
                activeName={strategy?.name ?? null}
                backendReachable={backendReachable}
                onSelect={onSelectStrategy}
              />

              <Separator />

              <div>
                <div className="mb-2 flex items-center justify-between text-xs">
                  <span className="uppercase tracking-wider text-muted-foreground">Risk per trade</span>
                  <span className="tabular-nums text-bull">{risk[0]}%</span>
                </div>
                <Slider
                  value={risk}
                  onValueChange={setRisk}
                  onValueCommit={onRiskCommit}
                  min={5}
                  max={100}
                  step={5}
                />
              </div>

              <ToggleRow
                label="Auto-compound"
                hint="Reinvest realized PnL into sizing"
                checked={autoCompound}
                onChange={setAutoCompound}
              />

              <Separator />

              <Button
                onClick={onFlatten}
                variant="outline"
                className="w-full border-bear/40 text-bear hover:bg-bear/10 hover:text-bear"
              >
                <AlertTriangle className="size-4" /> Flatten all positions
              </Button>

              <Button
                variant="outline"
                className="w-full md:hidden"
                onClick={() => setSettingsOpen(true)}
              >
                <Settings2 className="size-4" /> Engine settings
              </Button>
            </div>
          </Panel>
        </section>

        <section className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
          <div className="flex flex-col gap-4">
            <BreakersPanel
              breakers={breakers}
              onRearmAll={() => onRearmBreakers()}
              onRearmCode={(code) => onRearmBreakers(code)}
            />
            <RiskPanel
              systemHealth={systemHealth}
              maxRiskPct={maxRiskPct}
              maxGrossNotional={maxGrossNotional}
              totalEquity={totalEquity}
            />
          </div>
          <Panel
            className="lg:col-span-2"
            title="LIVE LOG"
            right={<LiveDot active={status === "running"} />}
          >
            <LogStream logs={logs} className="h-[400px]" />
          </Panel>
        </section>

        <section className="mt-4">
          <Panel
            title="OPEN POSITIONS"
            right={
              <span className="text-[11px] text-muted-foreground">{positions.length} active</span>
            }
          >
            <PositionsTable positions={positions} onOpen={(p) => setChartSymbol(p.symbol)} />
          </Panel>
        </section>

        {systemHealth ? (
          <section className="mt-4">
            <SystemHealthPanel
              health={systemHealth}
              maxGrossNotional={maxGrossNotional}
              status={status}
              expanded={healthExpanded}
              onExpandedChange={setHealthExpanded}
              exportError={exportError}
              onExportReport={onExportReport}
            />
          </section>
        ) : null}

        {/* OMS: working parent VWAPs + their child orders */}
        <section className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
          <Panel
            className="lg:col-span-2"
            title="ORDER MANAGEMENT"
            right={
              <span className="text-[11px] text-muted-foreground">
                {workingParents.length} parent · {workingOrders.length} child
              </span>
            }
          >
            <OmsTable parents={workingParents} children={workingOrders} />
          </Panel>

          <Panel
            title="EXECUTION QUALITY"
            right={
              <span className="text-[11px] uppercase tracking-wider text-muted-foreground">
                <Target className="mr-1 inline size-3" />
                {executionAggregate.count} parents
              </span>
            }
          >
            <ExecutionQualityPanel aggregate={executionAggregate} history={executionHistory} />
          </Panel>
        </section>

        <section className="mt-4">
          <Panel
            title="RECENT TRADES"
            right={
              <Button variant="ghost" size="sm" className="h-7 gap-1 text-[11px]">
                <RefreshCcw className="size-3" /> refresh
              </Button>
            }
          >
            <TradesTable trades={trades} />
          </Panel>
        </section>
      </main>

      <PositionChartDialog
        position={positions.find((p) => p.symbol === chartSymbol) ?? null}
        open={chartSymbol !== null}
        onOpenChange={(o) => !o && setChartSymbol(null)}
      />

      <SettingsDialog
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        onSaved={() => void live.refresh()}
        activeStrategyLabel={strategy?.label ?? null}
      />
    </div>
  );
}

/* ────────────────── pieces ────────────────── */

function StartupProgressBanner(props: {
  progress: StartupProgress;
  variant: "startup" | "resync";
}) {
  const { progress, variant } = props;
  const pct =
    progress.total > 0
      ? Math.min(100, Math.round((progress.done / progress.total) * 100))
      : null;
  const detail =
    progress.symbol && progress.total > 0
      ? `${progress.symbol} · ${progress.done}/${progress.total}`
      : progress.total > 0
        ? `${progress.done}/${progress.total}`
        : null;

  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "border-b px-4 py-2.5 lg:px-8",
        variant === "startup"
          ? "border-warning/40 bg-warning/10"
          : "border-muted-foreground/30 bg-muted/30",
      )}
    >
      <div className="mx-auto flex max-w-[1500px] flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-center gap-2 text-xs">
          <Loader2 className="size-3.5 shrink-0 animate-spin text-warning" />
          <span className="font-medium uppercase tracking-wider text-warning">
            {variant === "startup" ? "Starting" : "Market data"}
          </span>
          <span className="truncate text-foreground">{progress.label}</span>
          {detail && (
            <span className="shrink-0 tabular-nums text-muted-foreground">{detail}</span>
          )}
        </div>
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-background/60 sm:max-w-xs">
          {pct != null ? (
            <div
              className="h-full rounded-full bg-warning transition-[width] duration-300"
              style={{ width: `${pct}%` }}
            />
          ) : (
            <div className="h-full w-1/3 animate-pulse rounded-full bg-warning/70" />
          )}
        </div>
      </div>
    </div>
  );
}

function TopBar(props: {
  status: AlgoStatus;
  uptimeSec: number;
  paperMode: boolean;
  strategy: StrategyInfo | null;
  backendReachable: boolean;
  controlsBusy: boolean;
  startDisabled: boolean;
  onStart: () => void;
  onPause: () => void;
  onKill: () => void;
  onHaltTrading: () => void;
  onFlatten: () => void;
  onOpenSettings?: () => void;
}) {
  const { status, uptimeSec, paperMode, strategy } = props;
  const statusMeta = {
    running: { label: "RUNNING", color: "text-bull", dot: "bg-bull glow-bull" },
    paused: { label: "PAUSED", color: "text-warning", dot: "bg-warning" },
    stopped: { label: "STOPPED", color: "text-bear", dot: "bg-bear glow-bear" },
    starting: { label: "STARTING", color: "text-warning", dot: "bg-warning pulse-dot" },
  }[status];

  const h = Math.floor(uptimeSec / 3600);
  const m = Math.floor((uptimeSec % 3600) / 60);
  const s = uptimeSec % 60;
  const uptime = `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;

  return (
    <header className="sticky top-0 z-20 border-b border-border bg-background/85 backdrop-blur">
      <div className="mx-auto flex max-w-[1500px] items-center justify-between gap-4 px-4 py-3 lg:px-8">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <div className="grid size-8 place-items-center rounded-sm border border-bull/40 bg-bull/10">
              <Cpu className="size-4 text-bull" />
            </div>
            <div className="leading-tight">
              <div className="text-[11px] uppercase tracking-[0.2em] text-muted-foreground">
                Algo Console
              </div>
              <div className="text-sm font-semibold tracking-wide">
                {strategy?.label ?? (props.backendReachable ? "Loading..." : "—")}
              </div>
            </div>
          </div>

          <Separator orientation="vertical" className="h-8" />

          <div className={cn("flex items-center gap-2 text-xs uppercase tracking-wider", statusMeta.color)}>
            <span className={cn("size-2 rounded-full pulse-dot", statusMeta.dot)} />
            {statusMeta.label}
          </div>

          <div className="hidden items-center gap-3 text-[11px] text-muted-foreground md:flex">
            <span className="flex items-center gap-1">
              <Wifi className="size-3 text-bull" /> binance · ws-feed
            </span>
            <span>uptime <span className="text-foreground tabular-nums">{uptime}</span></span>
            {paperMode && (
              <Badge variant="outline" className="border-warning/50 text-warning">
                PAPER
              </Badge>
            )}
          </div>
        </div>

        <div className="hidden items-center gap-2 md:flex">
          <Button size="sm" variant="outline" className="border-border" asChild>
            <Link to="/backtesting">Backtest</Link>
          </Button>
          {props.onOpenSettings && (
            <Button size="sm" variant="outline" onClick={props.onOpenSettings} className="border-border">
              <Settings2 className="size-4" /> Settings
            </Button>
          )}
          <Button
            size="sm"
            variant="ghost"
            onClick={props.onPause}
            disabled={status !== "running" || props.controlsBusy}
          >
            <Pause className="size-4" />
          </Button>
          <Button
            size="sm"
            onClick={props.onStart}
            disabled={props.startDisabled}
            className="bg-bull text-bull-foreground hover:bg-bull/90"
          >
            {props.controlsBusy ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Play className="size-4" />
            )}{" "}
            {props.controlsBusy ? "Starting…" : "Start"}
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={props.onHaltTrading}
            disabled={!props.backendReachable || status === "stopped" || props.controlsBusy}
            className="border-warning/50 text-warning hover:bg-warning/10"
          >
            <AlertTriangle className="size-4" /> Halt
          </Button>
          <Button
            size="sm"
            variant="destructive"
            onClick={props.onKill}
            disabled={!props.backendReachable}
          >
            <Power className="size-4" /> Kill
          </Button>
        </div>
      </div>
    </header>
  );
}

function WinRateKpiCard({
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
    netFromCloses >= 0 ? `+$${formatUsdPayoffCell(netFromCloses)}` : `−$${formatUsdPayoffCell(Math.abs(netFromCloses))}`;

  const expectancyTone =
    expectancy != null && expectancy > 0
      ? "text-bull"
      : expectancy != null && expectancy < 0
        ? "text-bear"
        : "text-muted-foreground";
  const expectancyFormatted =
    expectancy != null ? formatSignedRealizedPnl(expectancy) : "—";

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
            Win rate counts per-leg exits (venue rp or entry→exit), not open entries or mark-to-market
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
            {scope === "session" ? "Session · " : "Rolling (≤200) · "}
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
                  {avgWin != null ? `+$${formatUsdPayoffCell(avgWin)}` : "—"}
                </p>
              </div>
              <div className="text-right">
                <p className="font-mono text-[9px] uppercase tracking-wide text-muted-foreground">Avg loss</p>
                <p className="mt-0.5 font-mono text-sm tabular-nums text-bear">
                  {avgLoss != null ? `−$${formatUsdPayoffCell(avgLoss)}` : "—"}
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
                  title="Average win ÷ average loss — how much you make per $1 lost"
                >
                  {payoffRatio != null ? (
                    <>
                      {payoffRatio.toFixed(2)}
                      <span className="text-xs text-muted-foreground">×</span>
                    </>
                  ) : (
                    "—"
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
                  {wrVsBreakeven === "at-or-above" ? " (at or above)" : " (below — need higher WR or larger wins)"}
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
              <span className="text-bear">{`−$${formatUsdPayoffCell(grossLoss)}`}</span>
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
                  <span className="text-sm text-muted-foreground">×</span>
                </>
              ) : grossWin > 0 && grossLoss <= 1e-12 ? (
                <>
                  ∞<span className="text-sm text-muted-foreground">×</span>
                </>
              ) : (
                <span className="text-muted-foreground">—</span>
              )}
            </span>
          </div>

          <div className="mt-3 flex items-center justify-between border-t border-border/60 pt-2 font-mono text-xs">
            <span className="text-muted-foreground">Net · realized closes</span>
            <span className={cn("tabular-nums font-semibold", netTone)}>{netFormatted}</span>
          </div>

          <details className="group mt-2 border border-border/50 bg-muted/15 font-mono text-[10px] leading-relaxed text-muted-foreground [&_summary::-webkit-details-marker]:hidden">
            <summary className="cursor-pointer select-none px-2 py-1.5 text-[10px] uppercase tracking-wide hover:bg-muted/30">
              <span className="text-muted-foreground">ⓘ Methodology · </span>
              <span className="normal-case tracking-normal opacity-70">PnL sources & factor definition</span>
            </summary>
            <div className="border-t border-border/40 px-2 py-2 text-[10px]">
              <strong className="text-foreground">Rolling</strong> is the last ≤200 realized-PnL closes;{" "}
              <strong className="text-foreground">Session</strong> is all such closes since the backend process started (a
              restart resets it). Session KPI values refresh with{" "}
              <code className="rounded bg-muted/60 px-0.5">GET /api/state</code> (about every 5s). The rolling view matches live
              WebSocket fills. Binance Futures uses field{" "}
              <code className="rounded bg-muted/60 px-0.5">rp</code> when it is non-zero; otherwise the console uses{' '}
              <span className="whitespace-nowrap">(exit − entry) × closed qty</span>. If{' '}
              <code className="rounded bg-muted/60 px-0.5">rp</code> looks like dust vs that economics (e.g. sub-cent vs several
              dollars), the engine keeps the computed slice PnL. <strong className="text-foreground">Avg win/loss</strong> are
              mean P&L on winning vs losing closes; <strong className="text-foreground">payoff (R)</strong> = avg win ÷ avg
              loss; <strong className="text-foreground">expectancy</strong> = net ÷ closes;{" "}
              <strong className="text-foreground">breakeven WR</strong> = avg loss ÷ (avg win + avg loss). Profit factor =
              Σ&nbsp;positive closes ÷ Σ&nbsp;|negative closes|. Excludes transfers, funding, and fees unless the venue folds
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

function KpiCard({
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

function Panel({
  title,
  right,
  children,
  className,
}: {
  title: string;
  right?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("overflow-hidden rounded-sm border border-border bg-card/60", className)}>
      <div className="flex items-center justify-between border-b border-border px-4 py-2">
        <h2 className="text-[11px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">
          {title}
        </h2>
        {right}
      </div>
      {children}
    </div>
  );
}

function ToggleRow({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string;
  hint: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-4">
      <div>
        <div className="text-sm">{label}</div>
        <div className="text-[11px] text-muted-foreground">{hint}</div>
      </div>
      <Switch checked={checked} onCheckedChange={onChange} />
    </div>
  );
}

const ALL_STRATEGIES_OPTION: StrategyInfo = {
  name: "all",
  label: "All strategies (netted)",
  description: "Run pairs, SMA, and market making with internal position netting.",
  active: false,
};

function StrategyPicker({
  strategies,
  activeName,
  backendReachable,
  onSelect,
}: {
  strategies: StrategyInfo[];
  activeName: string | null;
  backendReachable: boolean;
  onSelect: (name: string) => void;
}) {
  const options = [ALL_STRATEGIES_OPTION, ...strategies];
  return (
    <div>
      <div className="mb-2 flex items-center justify-between text-xs">
        <span className="uppercase tracking-wider text-muted-foreground">Strategy</span>
        {strategies.length === 0 && (
          <span className="text-[11px] text-muted-foreground">
            {backendReachable ? "Loading…" : "Backend offline"}
          </span>
        )}
      </div>
      <div className="grid grid-cols-1 gap-1.5">
        {options.map((s) => {
          const isActive = (activeName ?? "") === s.name;
          return (
            <button
              key={s.name}
              type="button"
              onClick={() => onSelect(s.name)}
              className={cn(
                "flex flex-col items-start gap-0.5 rounded-sm border px-2.5 py-2 text-left transition-colors",
                isActive
                  ? "border-bull/60 bg-bull/10 text-bull"
                  : "border-border bg-background/40 text-foreground/80 hover:border-bull/30 hover:text-foreground",
              )}
            >
              <div className="flex w-full items-center gap-2">
                <span className="text-sm font-semibold tracking-tight">{s.label}</span>
                {isActive && (
                  <span className="ml-auto rounded-sm border border-bull/40 bg-bull/10 px-1.5 py-0.5 text-[9px] uppercase tracking-wider">
                    Active
                  </span>
                )}
              </div>
              {s.description && (
                <div className="text-[11px] text-muted-foreground">{s.description}</div>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function RiskPanel({
  systemHealth,
  maxRiskPct,
  maxGrossNotional,
  totalEquity,
}: {
  systemHealth: SystemHealth | null;
  maxRiskPct: number;
  maxGrossNotional: number;
  totalEquity: number;
}) {
  const equity = systemHealth?.equity ?? totalEquity;
  const gross = systemHealth?.grossNotional ?? 0;
  const net = systemHealth?.netNotional ?? 0;
  const grossPct = maxGrossNotional > 0 ? (gross / maxGrossNotional) * 100 : 0;
  const perTradeCap = equity * maxRiskPct;

  return (
    <Panel title="RISK LIMITS">
      <div className="grid grid-cols-2 gap-3 p-4 text-xs">
        <div>
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Equity</div>
          <div className="mt-1 font-mono text-sm tabular-nums">
            ${equity.toLocaleString(undefined, { maximumFractionDigits: 0 })}
          </div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Per-trade cap</div>
          <div className="mt-1 font-mono text-sm tabular-nums">
            ${perTradeCap.toLocaleString(undefined, { maximumFractionDigits: 0 })}
            <span className="ml-1 text-muted-foreground">({(maxRiskPct * 100).toFixed(0)}%)</span>
          </div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Gross notional</div>
          <div
            className={cn(
              "mt-1 font-mono text-sm tabular-nums",
              gross > maxGrossNotional ? "text-bear" : "text-foreground",
            )}
          >
            ${gross.toLocaleString(undefined, { maximumFractionDigits: 0 })}
            <span className="ml-1 text-muted-foreground">
              / ${maxGrossNotional.toLocaleString(undefined, { maximumFractionDigits: 0 })}
            </span>
          </div>
          <div className="mt-1 text-[10px] text-muted-foreground">{grossPct.toFixed(1)}% of limit</div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Net notional</div>
          <div className="mt-1 font-mono text-sm tabular-nums">
            ${net.toLocaleString(undefined, { maximumFractionDigits: 0 })}
          </div>
        </div>
      </div>
    </Panel>
  );
}

function BreakersPanel({
  breakers,
  onRearmAll,
  onRearmCode,
}: {
  breakers: BreakerList;
  onRearmAll: () => void;
  onRearmCode: (code: string) => void;
}) {
  return (
    <Panel
      title="CIRCUIT BREAKERS"
      right={
        breakers.active.length > 0 ? (
          <Button variant="outline" size="sm" className="h-7 text-[11px]" onClick={onRearmAll}>
            Rearm all
          </Button>
        ) : null
      }
    >
      <div className="p-4">
        {breakers.active.length === 0 ? (
          <p className="text-center text-xs text-muted-foreground">No active breakers.</p>
        ) : (
          <ScrollArea className="h-[120px]">
            <div className="space-y-2">
              {breakers.active.map((b) => (
                <BreakerRow key={`${b.code}-${b.target ?? ""}`} breaker={b} onRearm={() => onRearmCode(b.code)} />
              ))}
            </div>
          </ScrollArea>
        )}
        {breakers.history.length > 0 ? (
          <p className="mt-3 text-[10px] text-muted-foreground">
            {breakers.history.length} recent event(s) in history
          </p>
        ) : null}
      </div>
    </Panel>
  );
}

function BreakerRow({ breaker, onRearm }: { breaker: BreakerStatus; onRearm: () => void }) {
  const latched = breaker.state === "latched" || breaker.state === "tripped";
  return (
    <div className="flex items-start gap-2 rounded-sm border border-border/60 bg-card/40 px-2.5 py-2 text-xs">
      <ShieldAlert className={cn("mt-0.5 size-3.5 shrink-0", latched ? "text-bear" : "text-muted-foreground")} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono font-semibold">{breaker.code}</span>
          <Badge variant="outline" className="text-[9px] uppercase">
            {breaker.severity}
          </Badge>
          <Badge variant="outline" className="text-[9px] uppercase">
            {breaker.state}
          </Badge>
          {breaker.target ? (
            <span className="text-muted-foreground">{breaker.target}</span>
          ) : null}
        </div>
        {breaker.detail ? (
          <p className="mt-1 truncate text-[11px] text-muted-foreground">{breaker.detail}</p>
        ) : null}
      </div>
      {latched ? (
        <Button variant="ghost" size="sm" className="h-7 shrink-0 text-[10px]" onClick={onRearm}>
          Rearm
        </Button>
      ) : null}
    </div>
  );
}

const CLOCK_SKEW_WARN_MS = 250;

function latencyP95Display(
  latency: SystemHealth["latency"],
  key: string,
  warnMs: number,
): { value: string; ok: boolean } {
  const bucket = latency[key];
  if (!bucket || bucket.count < 1) {
    return { value: "—", ok: true };
  }
  const p95 = bucket.p95;
  return {
    value: `${p95.toFixed(0)} ms`,
    ok: p95 < warnMs,
  };
}

function userDataAgeDisplay(health: SystemHealth): { value: string; ok: boolean } {
  if (health.userDataAgeSec < 0) {
    return { value: "—", ok: true };
  }
  const age = `${health.userDataAgeSec.toFixed(1)}s`;
  if (health.userDataStale) {
    return { value: age, ok: false };
  }
  if (health.userDataReconcileStale) {
    return { value: `${age} (REST)`, ok: false };
  }
  if (!health.userDataMonitored) {
    return { value: `${age} (idle)`, ok: true };
  }
  return { value: age, ok: true };
}

function clockSkewDisplay(health: SystemHealth): { value: string; ok: boolean } {
  if (!health.clockSkewSynced) {
    return { value: "—", ok: true };
  }
  const ms = Math.abs(health.clockSkewMs);
  return {
    value: `${ms.toFixed(0)} ms`,
    ok: ms < CLOCK_SKEW_WARN_MS,
  };
}

function SystemHealthPanel({
  health,
  maxGrossNotional,
  status,
  expanded,
  onExpandedChange,
  exportError,
  onExportReport,
}: {
  health: SystemHealth;
  maxGrossNotional: number;
  status: AlgoStatus;
  expanded: boolean;
  onExpandedChange: (open: boolean) => void;
  exportError: string | null;
  onExportReport: () => void | Promise<void>;
}) {
  const issueCount = [
    health.tickAgeSec < 0 || health.tickAgeSec >= 15,
    !userDataAgeDisplay(health).ok,
    health.orderReconcile.ok === false,
    health.activeBreakers.length > 0,
    !latencyP95Display(health.latency, "tick_to_submit_ms", 500).ok,
    !latencyP95Display(health.latency, "submit_to_ack_ms", 500).ok,
    !clockSkewDisplay(health).ok,
    health.grossNotional > maxGrossNotional,
  ].filter(Boolean).length;

  return (
    <Collapsible open={expanded} onOpenChange={onExpandedChange}>
      <div className="overflow-hidden rounded-sm border border-border bg-card/60">
        <CollapsibleTrigger asChild>
          <button
            type="button"
            className="flex w-full items-center justify-between border-b border-border px-4 py-2 text-left transition-colors hover:bg-muted/30"
          >
            <div className="flex items-center gap-2">
              <h2 className="text-[11px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">
                System health
              </h2>
              {issueCount > 0 ? (
                <Badge variant="outline" className="border-bear/40 text-[10px] text-bear">
                  {issueCount} issue{issueCount === 1 ? "" : "s"}
                </Badge>
              ) : (
                <Badge variant="outline" className="border-bull/40 text-[10px] text-bull">
                  OK
                </Badge>
              )}
            </div>
            <div className="flex items-center gap-2">
              <span className="text-[10px] text-muted-foreground">
                {expanded ? "Hide" : "Show"} diagnostics
              </span>
              <ChevronDown
                className={cn(
                  "size-4 text-muted-foreground transition-transform",
                  expanded && "rotate-180",
                )}
              />
            </div>
          </button>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="flex items-center justify-end gap-2 border-b border-border px-4 py-2">
            <Button
              variant="ghost"
              size="sm"
              className="h-7 gap-1 text-[11px]"
              onClick={() => void onExportReport()}
            >
              <Download className="size-3" /> export
            </Button>
            <LiveDot active={status === "running"} />
          </div>
          {exportError ? (
            <p className="mb-2 px-4 text-[11px] text-bear">{exportError}</p>
          ) : null}
          <div className="grid grid-cols-2 gap-3 px-4 pb-2 text-xs md:grid-cols-4 lg:grid-cols-8">
            <HealthChip
              label="Tick age"
              value={
                health.tickAgeSec >= 0 ? `${health.tickAgeSec.toFixed(1)}s` : "—"
              }
              ok={health.tickAgeSec >= 0 && health.tickAgeSec < 15}
            />
            <HealthChip label="User-data age" {...userDataAgeDisplay(health)} />
            <HealthChip
              label="Order reconcile"
              value={
                health.orderReconcile.ok === false
                  ? `mismatch (${String(health.orderReconcile.venue_only ?? 0)}/${String(health.orderReconcile.local_only ?? 0)})`
                  : "OK"
              }
              ok={health.orderReconcile.ok !== false}
            />
            <HealthChip
              label="Breakers"
              value={
                health.activeBreakers.length
                  ? health.activeBreakers.join(", ")
                  : "none"
              }
              ok={health.activeBreakers.length === 0}
            />
            <HealthChip
              label="p95 tick→submit"
              {...latencyP95Display(health.latency, "tick_to_submit_ms", 500)}
            />
            <HealthChip
              label="p95 submit→ack"
              {...latencyP95Display(health.latency, "submit_to_ack_ms", 500)}
            />
            <HealthChip label="Clock skew" {...clockSkewDisplay(health)} />
            <HealthChip
              label="Gross notional"
              value={`$${health.grossNotional.toLocaleString(undefined, { maximumFractionDigits: 0 })}`}
              ok={health.grossNotional <= maxGrossNotional}
            />
          </div>
          {Object.keys(health.mdHealth).length > 0 ? (
            <div className="mt-3 flex flex-wrap gap-2 px-4 pb-4">
              {Object.entries(health.mdHealth).map(([sym, h]) => (
                <span
                  key={sym}
                  className={cn(
                    "rounded border px-2 py-0.5 text-[10px] tabular-nums",
                    h.crossed_count > 0 || h.last_diff_age_ms > 30_000
                      ? "border-bear/40 text-bear"
                      : "border-border text-muted-foreground",
                  )}
                >
                  {sym} gaps={h.sequence_gaps} age=
                  {h.last_diff_age_ms >= 0
                    ? `${(h.last_diff_age_ms / 1000).toFixed(0)}s`
                    : "—"}
                </span>
              ))}
            </div>
          ) : null}
        </CollapsibleContent>
      </div>
    </Collapsible>
  );
}

function HealthChip({
  label,
  value,
  ok,
}: {
  label: string;
  value: string;
  ok: boolean;
}) {
  return (
    <div
      className={cn(
        "rounded border px-2 py-1.5",
        ok ? "border-border" : "border-bear/40 bg-bear/5",
      )}
    >
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className={cn("mt-0.5 tabular-nums", ok ? "text-foreground" : "text-bear")}>{value}</div>
    </div>
  );
}

function LiveDot({ active }: { active: boolean }) {
  return (
    <span className="flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-muted-foreground">
      <span className={cn("size-1.5 rounded-full", active ? "bg-bull pulse-dot" : "bg-muted-foreground")} />
      {active ? "live" : "idle"}
    </span>
  );
}

function PositionsTable({
  positions,
  onOpen,
}: {
  positions: Position[];
  onOpen: (p: Position) => void;
}) {
  if (!positions.length) {
    return (
      <div className="px-4 py-10 text-center text-xs text-muted-foreground">
        No open positions.
      </div>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-[10px] uppercase tracking-wider text-muted-foreground">
            <th className="px-4 py-2 text-left font-normal">Symbol</th>
            <th className="px-2 py-2 text-left font-normal">Side</th>
            <th className="px-2 py-2 text-right font-normal">Size</th>
            <th className="px-2 py-2 text-right font-normal">Entry</th>
            <th className="px-2 py-2 text-right font-normal">Mark</th>
            <th className="px-4 py-2 text-right font-normal">PnL</th>
            <th className="px-2 py-2 text-right font-normal" />
          </tr>
        </thead>
        <tbody className="font-mono">
          {positions.map((p) => {
            const pnl = p.unrealizedPnl;
            const basis = p.entry * p.size;
            const pct = basis > 1e-12 ? (pnl / basis) * 100 : 0;
            const positive = pnl >= 0;
            return (
              <tr
                key={p.symbol}
                onClick={() => onOpen(p)}
                className="cursor-pointer border-t border-border/60 transition-colors hover:bg-accent/30"
              >
                <td className="px-4 py-2.5 font-semibold">{p.symbol}</td>
                <td className="px-2 py-2.5">
                  <span
                    className={cn(
                      "inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-[10px] uppercase",
                      p.side === "long"
                        ? "border-bull/40 bg-bull/10 text-bull"
                        : "border-bear/40 bg-bear/10 text-bear",
                    )}
                  >
                    {p.side === "long" ? <TrendingUp className="size-3" /> : <TrendingDown className="size-3" />}
                    {p.side}
                  </span>
                </td>
                <td className="px-2 py-2.5 text-right tabular-nums">{p.size}</td>
                <td className="px-2 py-2.5 text-right tabular-nums">{p.entry.toLocaleString()}</td>
                <td className="px-2 py-2.5 text-right tabular-nums">{p.mark.toLocaleString()}</td>
                <td className={cn("px-4 py-2.5 text-right tabular-nums", positive ? "text-bull" : "text-bear")}>
                  {positive ? "+" : ""}
                  {pnl.toFixed(2)}{" "}
                  <span className="text-[10px] opacity-70">({pct.toFixed(2)}%)</span>
                </td>
                <td className="px-3 py-2.5 text-right">
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onOpen(p);
                    }}
                    className="rounded-sm border border-border px-2 py-1 text-[10px] uppercase tracking-wider text-muted-foreground hover:border-bull/40 hover:text-bull"
                  >
                    Chart
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function TradesTable({ trades }: { trades: Trade[] }) {
  const fmtPrice = (v: number | null) =>
    v === null ? "—" : v.toLocaleString(undefined, { maximumFractionDigits: 6 });

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-[10px] uppercase tracking-wider text-muted-foreground">
            <th className="px-4 py-2 text-left font-normal">Time</th>
            <th className="px-2 py-2 text-left font-normal">Type</th>
            <th className="px-2 py-2 text-left font-normal">Symbol</th>
            <th className="px-2 py-2 text-left font-normal">Side</th>
            <th className="px-2 py-2 text-right font-normal">Qty</th>
            <th className="px-2 py-2 text-right font-normal">Entry</th>
            <th className="px-2 py-2 text-right font-normal">Exit</th>
            <th className="px-4 py-2 text-right font-normal">PnL</th>
          </tr>
        </thead>
        <tbody className="font-mono">
          {trades.slice(0, 12).map((t) => (
            <tr key={t.id} className="border-t border-border/60 hover:bg-accent/30">
              <td className="px-4 py-2 text-muted-foreground tabular-nums">{t.ts}</td>
              <td className="px-2 py-2">
                <span
                  className={cn(
                    "rounded-sm px-1.5 py-0.5 text-[10px] uppercase",
                    t.action === "open"
                      ? "bg-muted text-muted-foreground"
                      : "bg-warning/15 text-warning",
                  )}
                >
                  {t.action}
                </span>
              </td>
              <td className="px-2 py-2">{t.symbol}</td>
              <td className="px-2 py-2">
                <span
                  className={cn(
                    "rounded-sm px-1.5 py-0.5 text-[10px] uppercase",
                    t.side === "buy" ? "bg-bull/15 text-bull" : "bg-bear/15 text-bear",
                  )}
                >
                  {t.side}
                </span>
              </td>
              <td className="px-2 py-2 text-right tabular-nums">{t.qty}</td>
              <td className="px-2 py-2 text-right tabular-nums">{fmtPrice(t.entryPrice)}</td>
              <td className="px-2 py-2 text-right tabular-nums">{fmtPrice(t.exitPrice)}</td>
              <td
                className={cn(
                  "px-4 py-2 text-right tabular-nums",
                  t.action === "open"
                    ? "text-muted-foreground"
                    : (t.pnl ?? 0) >= 0
                      ? "text-bull"
                      : "text-bear",
                )}
              >
                {t.action === "open" ? "—" : formatSignedRealizedPnl(t.pnl)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function LogStream({ logs, className }: { logs: LogEntry[]; className?: string }) {
  const color: Record<LogEntry["level"], string> = {
    debug: "text-muted-foreground/60",
    info: "text-muted-foreground",
    warn: "text-warning",
    error: "text-bear",
    signal: "text-bull",
  };
  const tag: Record<LogEntry["level"], string> = {
    debug: "DBG ",
    info: "INFO",
    warn: "WARN",
    error: "ERR ",
    signal: "SIG ",
  };
  return (
    <ScrollArea className={cn("h-[320px]", className)}>
      <div className="space-y-1 px-3 py-2 font-mono text-[12px] leading-relaxed">
        {logs.map((l, i) => (
          <div key={i} className="flex gap-2">
            <span className="shrink-0 text-muted-foreground/70 tabular-nums">{l.ts}</span>
            <span className={cn("shrink-0 font-semibold", color[l.level])}>{tag[l.level]}</span>
            {l.logger ? (
              <span className="shrink-0 max-w-[10rem] truncate text-[10px] text-muted-foreground/60">
                {l.logger.split(".").slice(-1)[0]}
              </span>
            ) : null}
            <span className="min-w-0 break-words text-foreground/90">{l.msg}</span>
          </div>
        ))}
      </div>
    </ScrollArea>
  );
}

function OmsTable({
  parents,
  children,
}: {
  parents: ExecutionParent[];
  children: WorkingOrder[];
}) {
  if (!parents.length && !children.length) {
    return (
      <div className="px-4 py-10 text-center text-xs text-muted-foreground">
        No working orders. The OMS lights up the moment a parent VWAP is in flight.
      </div>
    );
  }

  // Group children by parent for the nested rendering. Anything orphaned
  // (e.g. an operator-side cancel from another UI) is shown under "manual".
  const childrenByParent = new Map<string, WorkingOrder[]>();
  for (const child of children) {
    const key = child.parentId ?? "manual";
    const existing = childrenByParent.get(key) ?? [];
    existing.push(child);
    childrenByParent.set(key, existing);
  }

  return (
    <ScrollArea className="h-[320px]">
      <div className="divide-y divide-border/60">
        {parents.map((parent) => (
          <ParentRow
            key={parent.parentId}
            parent={parent}
            children={childrenByParent.get(parent.parentId) ?? []}
          />
        ))}
        {(childrenByParent.get("manual") ?? []).map((child) => (
          <div key={child.id} className="px-4 py-2.5 text-xs">
            <div className="flex items-center gap-2 text-muted-foreground">
              <ListOrdered className="size-3" />
              <span className="uppercase tracking-wider">manual order</span>
            </div>
            <ChildRow child={child} />
          </div>
        ))}
      </div>
    </ScrollArea>
  );
}

function ParentRow({
  parent,
  children,
}: {
  parent: ExecutionParent;
  children: WorkingOrder[];
}) {
  const pct = Math.min(100, Math.max(0, parent.fillRatio * 100));
  const sideClass =
    parent.side === "buy"
      ? "border-bull/40 bg-bull/10 text-bull"
      : "border-bear/40 bg-bear/10 text-bear";
  return (
    <div className="px-4 py-3">
      <div className="flex flex-wrap items-center gap-3 text-xs">
        <span className="font-mono text-foreground/90">{parent.parentId}</span>
        <span
          className={cn(
            "rounded-sm border px-1.5 py-0.5 text-[10px] uppercase",
            sideClass,
          )}
        >
          {parent.side}
        </span>
        <span className="font-semibold">{parent.symbol}</span>
        {parent.algoMode && (
          <Badge variant="outline" className="border-border text-[10px] uppercase tracking-wider">
            {parent.algoMode}
          </Badge>
        )}
        <span className="ml-auto tabular-nums text-muted-foreground">
          {parent.filledQty.toFixed(4)} / {parent.requestedQty.toFixed(4)}
        </span>
      </div>

      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-muted/60">
        <div
          className={cn(
            "h-full transition-all",
            parent.side === "buy" ? "bg-bull" : "bg-bear",
          )}
          style={{ width: `${pct}%` }}
        />
      </div>

      <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
        <span>arrival <span className="tabular-nums text-foreground/80">{parent.arrivalPrice.toFixed(4)}</span></span>
        <span>vwap <span className="tabular-nums text-foreground/80">{parent.vwapPrice.toFixed(4)}</span></span>
        <span className={cn("tabular-nums", parent.slippageBps > 0 ? "text-bear" : "text-bull")}>
          slippage {parent.slippageBps >= 0 ? "+" : ""}{parent.slippageBps.toFixed(1)} bps
        </span>
        <span className={cn("tabular-nums", parent.feeAdjustedSlippageBps > 0 ? "text-bear" : "text-bull")}>
          fee-adj {parent.feeAdjustedSlippageBps >= 0 ? "+" : ""}
          {parent.feeAdjustedSlippageBps.toFixed(1)} bps
        </span>
        <span className="tabular-nums">
          impact {parent.impactBps.toFixed(1)} bps
        </span>
        <span className="tabular-nums">{parent.durationSec.toFixed(1)}s</span>
        {parent.signalScore > 0 ? (
          <span className="tabular-nums">score {parent.signalScore.toFixed(2)}</span>
        ) : null}
      </div>

      {parent.notes ? (
        <p className="mt-1.5 break-words font-mono text-[10px] leading-snug text-muted-foreground">
          {parent.notes}
        </p>
      ) : null}

      {children.length > 0 && (
        <div className="mt-2 space-y-1 border-l border-border/60 pl-3">
          {children.map((c) => (
            <ChildRow key={c.id} child={c} />
          ))}
        </div>
      )}
    </div>
  );
}

function ChildRow({ child }: { child: WorkingOrder }) {
  const sideClass =
    child.side === "buy"
      ? "border-bull/30 bg-bull/5 text-bull"
      : "border-bear/30 bg-bear/5 text-bear";
  return (
    <div className="flex flex-wrap items-center gap-2 text-[11px] font-mono text-muted-foreground">
      <span className="text-foreground/80">{child.id.slice(-10)}</span>
      <span
        className={cn(
          "rounded-sm border px-1 py-0.5 text-[10px] uppercase",
          sideClass,
        )}
      >
        {child.side}
      </span>
      <span className="uppercase tracking-wider">{child.orderType}</span>
      <span className="tabular-nums">
        {child.filledQty.toFixed(4)} / {child.qty.toFixed(4)}
      </span>
      <span className="tabular-nums">
        @ {child.price !== null ? child.price.toFixed(4) : "mkt"}
      </span>
      <span className="ml-auto rounded-sm border border-border px-1 py-0.5 text-[10px] uppercase">
        {child.status}
      </span>
    </div>
  );
}

function ExecutionQualityPanel({
  aggregate,
  history,
}: {
  aggregate: ExecutionAggregate;
  history: ExecutionParent[];
}) {
  return (
    <div className="space-y-4 p-4">
      <div className="grid grid-cols-2 gap-3">
        <Stat
          label="AVG SLIPPAGE"
          value={`${aggregate.avgSlippageBps >= 0 ? "+" : ""}${aggregate.avgSlippageBps.toFixed(2)} bps`}
          tone={aggregate.avgSlippageBps > 0 ? "bear" : "bull"}
        />
        <Stat
          label="AVG IMPACT (sim)"
          value={`${aggregate.avgImpactBps.toFixed(2)} bps`}
          tone="neutral"
        />
        <Stat
          label="FILL RATE"
          value={`${(aggregate.avgFillRatio * 100).toFixed(1)}%`}
          tone={aggregate.avgFillRatio >= 0.95 ? "bull" : "neutral"}
        />
        <Stat
          label="AVG DURATION"
          value={`${aggregate.avgDurationSec.toFixed(1)}s`}
          tone="neutral"
        />
      </div>

      <Separator />

      <div className="space-y-1">
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
          Recent parent orders
        </div>
        {history.length === 0 ? (
          <div className="py-6 text-center text-[11px] text-muted-foreground">
            No completed parents yet.
          </div>
        ) : (
          <ScrollArea className="h-[180px]">
            <div className="space-y-1 font-mono text-[11px]">
              {history.slice(0, 12).map((r) => (
                <div
                  key={r.parentId}
                  className="flex items-center gap-2 border-b border-border/40 py-1"
                >
                  <span className="text-muted-foreground">{r.symbol}</span>
                  <span
                    className={cn(
                      "rounded-sm px-1 text-[10px] uppercase",
                      r.side === "buy" ? "bg-bull/15 text-bull" : "bg-bear/15 text-bear",
                    )}
                  >
                    {r.side}
                  </span>
                  <span className="tabular-nums text-muted-foreground">
                    {r.filledQty.toFixed(4)}
                  </span>
                  <span
                    className={cn(
                      "tabular-nums",
                      r.slippageBps > 0 ? "text-bear" : "text-bull",
                    )}
                  >
                    {r.slippageBps >= 0 ? "+" : ""}
                    {r.slippageBps.toFixed(1)}
                  </span>
                  <span
                    className={cn(
                      "tabular-nums",
                      r.feeAdjustedSlippageBps > 0 ? "text-bear" : "text-bull",
                    )}
                  >
                    fee {r.feeAdjustedSlippageBps >= 0 ? "+" : ""}
                    {r.feeAdjustedSlippageBps.toFixed(1)} bps
                  </span>
                  <span className="tabular-nums text-muted-foreground">
                    {r.durationSec.toFixed(1)}s
                  </span>
                  {r.notes ? (
                    <span
                      className="min-w-0 max-w-[12rem] truncate text-muted-foreground/80"
                      title={r.notes}
                    >
                      {r.notes}
                    </span>
                  ) : null}
                </div>
              ))}
            </div>
          </ScrollArea>
        )}
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "bull" | "bear" | "neutral";
}) {
  const color =
    tone === "bull" ? "text-bull" : tone === "bear" ? "text-bear" : "text-foreground/90";
  return (
    <div className="rounded-sm border border-border/60 bg-card/40 p-2.5">
      <div className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground">{label}</div>
      <div className={cn("mt-1 font-mono text-base font-semibold tabular-nums", color)}>
        {value}
      </div>
    </div>
  );
}
