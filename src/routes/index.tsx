import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Loader2,
  Pause,
  Play,
  RefreshCcw,
  Settings2,
  Square,
  Target,
  TrendingDown,
  Wallet,
  Zap,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Slider } from "@/components/ui/slider";
import { Separator } from "@/components/ui/separator";
import { EquityChart } from "@/components/algo/EquityChart";
import { PositionChartDialog } from "@/components/algo/PositionChartDialog";
import {
  BreakersPanel,
  ControlLimitsPanel,
  ExecutionQualityPanel,
  KpiCard,
  LiveDot,
  LogStream,
  OmsTable,
  Panel,
  PositionsTable,
  RiskPanel,
  StartupProgressBanner,
  StrategyPicker,
  SystemHealthPanel,
  TopBar,
  TradesTable,
  WinRateKpiCard,
} from "@/components/algo/dashboard";
import type {
  AlgoStatus,
  ExecutionAggregate,
  ExecutionParent,
  LogEntry,
  Position,
  StartupProgress,
  StrategyInfo,
  SystemHealth,
  Trade,
  WorkingOrder,
} from "@/components/algo/types";
import { useAlgoStream } from "@/hooks/useAlgoStream";
import { computeMaxDrawdown } from "@/lib/series";
import { api } from "@/lib/api";
import { LIVE_DISABLE_CONFIRM_TOKEN } from "@/lib/breaker-presets";
import { notifyError, notifySuccess } from "@/lib/notify";
import {
  derivePayoffMetrics,
  emptyClosedTradePerf,
} from "@/lib/algo-format";

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
  const settingsSnapshot = live.settingsSnapshot;
  const replaySummary = live.replaySummary;
  const kpi = live.kpi;
  const backendReachable = live.backendReachable;
  const backendError = live.error;
  const streamConnected = live.connected;

  const HEALTH_EXPANDED_STORAGE = "algo-health-expanded";
  const [healthExpanded, setHealthExpanded] = useState(false);
  const healthExpandedReady = useRef(false);
  useEffect(() => {
    if (!healthExpandedReady.current) {
      setHealthExpanded(window.localStorage.getItem(HEALTH_EXPANDED_STORAGE) === "true");
      healthExpandedReady.current = true;
      return;
    }
    window.localStorage.setItem(HEALTH_EXPANDED_STORAGE, healthExpanded ? "true" : "false");
  }, [healthExpanded]);

  const KPI_SCOPE_STORAGE = "algo-kpi-window";
  const [kpiScope, setKpiScope] = useState<"rolling" | "session">("rolling");
  const kpiScopeReady = useRef(false);
  useEffect(() => {
    if (!kpiScopeReady.current) {
      if (window.localStorage.getItem(KPI_SCOPE_STORAGE) === "session") {
        setKpiScope("session");
      }
      kpiScopeReady.current = true;
      return;
    }
    window.localStorage.setItem(KPI_SCOPE_STORAGE, kpiScope);
  }, [kpiScope]);

  const [risk, setRisk] = useState<number[]>([35]);
  const riskHydrated = useRef(false);
  const [chartSymbol, setChartSymbol] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const [controlPending, setControlPending] = useState<"start" | "flatten" | null>(null);

  useEffect(() => {
    if (riskHydrated.current || maxRiskPct <= 0) return;
    setRisk([Math.round(maxRiskPct * 100)]);
    riskHydrated.current = true;
  }, [maxRiskPct]);

  const totalEquity = equity.length ? equity[equity.length - 1] : 0;
  const startEquity = equity.length ? equity[0] : 0;
  const pnlAbs = totalEquity - startEquity;
  const pnlPct = startEquity > 0 ? (pnlAbs / startEquity) * 100 : 0;

  const maxDrawdown = useMemo(() => computeMaxDrawdown(equity), [equity]);

  const openPnl = useMemo(() => {
    if (systemHealth != null) {
      return systemHealth.unrealizedPnl;
    }
    return positions.reduce((acc, p) => acc + p.unrealizedPnl, 0);
  }, [systemHealth, positions]);

  

  /** Rolling = last ≤200 parent-level closes; session = every reducing fill since backend start. */
  const closedTradePerf = useMemo(() => {
    if (kpiScope === "session") {
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

    const closed =
      realizedTrades.length > 0
        ? realizedTrades
        : trades.filter((t) => t.action === "close" && t.pnl != null);
    if (!closed.length) {
      return emptyClosedTradePerf();
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
  const handleControl = (fn: () => Promise<unknown>, opts?: { successMessage?: string }) => {
    fn()
      .then(() => {
        if (opts?.successMessage) notifySuccess(opts.successMessage);
      })
      .catch((err) => {
        console.error("control command failed", err);
        notifyError(err);
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
  const onResume = () => handleControl(api.resume);
  const onStop = () => handleControl(api.stop);
  const onEStop = () => {
    const ok = window.confirm(
      "E-Stop will flatten all open positions and stop the trading engine.\n\n" +
        "The API server keeps running — use Start on this dashboard to trade again.\n\nContinue?",
    );
    if (!ok) return;
    handleControl(
      async () => {
        await api.kill();
        await live.refresh();
      },
      { successMessage: "Engine stopped. Press Start when you are ready to trade again." },
    );
  };
  const onHaltTrading = (opts?: { flatten?: boolean; pause?: boolean }) =>
    handleControl(
      async () => {
        await api.tripBreakers({
          flatten: opts?.flatten ?? true,
          pause: opts?.pause ?? true,
        });
        await live.refresh();
      },
      { successMessage: "Trading halt applied" },
    );

  const onPatchBreakerEnabled = async (
    patch: Record<string, boolean>,
    opts?: { confirmLiveDisable?: boolean; confirmToken?: string },
  ) => {
    try {
      await api.patchBreakerEnabled({
        patch,
        confirm_live_disable: opts?.confirmLiveDisable,
        confirm_token:
          opts?.confirmToken ?? (opts?.confirmLiveDisable ? LIVE_DISABLE_CONFIRM_TOKEN : ""),
      });
      await live.refresh();
      notifySuccess("Protection settings saved");
    } catch (err) {
      notifyError(err, "Failed to update circuit breakers");
      throw err;
    }
  };

  const onPatchSettings = async (patch: Record<string, unknown>) => {
    try {
      await api.patchSettings(patch);
      await live.refresh();
      notifySuccess("Settings applied");
    } catch (err) {
      notifyError(err, "Failed to update settings");
      throw err;
    }
  };
  const onFlatten = () => {
    if (controlPending === "flatten") return;
    setControlPending("flatten");
    handleControl(() =>
      api.flatten().finally(() => {
        setControlPending(null);
      }),
    );
  };

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
            "Cannot reach the trading API. Restart the server process if it is down, then use Start."}
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
        onResume={onResume}
        onPause={onPause}
        onEStop={onEStop}
        onHaltTrading={onHaltTrading}
        onFlatten={onFlatten}
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
              strategy?.name === "all" && strategies.length > 0
                ? strategies.map((s) => s.label).join(" · ")
                : strategy?.description ??
                  (backendReachable ? "Loading..." : "Backend offline — restart API")
            }
            tone="neutral"
          />
        </section>

        <section className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
          <KpiCard
            icon={<TrendingDown className="size-4" />}
            label="MAX DRAWDOWN"
            value={maxDrawdown.abs > 0 ? `-${maxDrawdown.abs.toFixed(2)}` : "0.00"}
            sub="session peak-to-trough"
            tone={maxDrawdown.abs > 0 ? "bear" : "neutral"}
          />
          <KpiCard
            icon={<TrendingDown className="size-4" />}
            label="MAX DRAWDOWN %"
            value={
              maxDrawdown.pct > 0
                ? `-${maxDrawdown.pct.toFixed(2)}%`
                : "0.00%"
            }
            sub="from running equity peak"
            tone={maxDrawdown.pct > 0 ? "bear" : "neutral"}
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
                {status === "paused" ? (
                  <Button
                    onClick={onResume}
                    disabled={!backendReachable || systemBusy}
                    className="col-span-2 bg-bull text-bull-foreground hover:bg-bull/90"
                  >
                    <Play className="size-4" /> RESUME
                  </Button>
                ) : (
                  <>
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
                  </>
                )}
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
                multiMode={strategy?.name === "all"}
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

              <ControlLimitsPanel
                settings={settingsSnapshot}
                backendReachable={backendReachable}
                onPatchSettings={onPatchSettings}
              />

              <Separator />

              <Button
                onClick={onFlatten}
                disabled={!backendReachable || controlPending === "flatten"}
                variant="outline"
                className="w-full border-bear/40 text-bear hover:bg-bear/10 hover:text-bear"
              >
                <AlertTriangle className="size-4" />
                {controlPending === "flatten" ? "Flattening…" : "Flatten all positions"}
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
              paperMode={paperMode}
              backendReachable={backendReachable}
              onRearmAll={() => onRearmBreakers()}
              onRearmCode={(code) => onRearmBreakers(code)}
              onPatchEnabled={onPatchBreakerEnabled}
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
            <TradesTable trades={trades} strategies={strategies} />
          </Panel>
        </section>
      </main>

      <PositionChartDialog
        position={positions.find((p) => p.symbol === chartSymbol) ?? null}
        open={chartSymbol !== null}
        onOpenChange={(o) => !o && setChartSymbol(null)}
      />

    </div>
  );
}