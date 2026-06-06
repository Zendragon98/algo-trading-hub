import { createFileRoute } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { RefreshCcw, Target } from "lucide-react";

import { Button } from "@/components/ui/button";
import { EquityChart } from "@/components/algo/EquityChart";
import { PositionChartDialog } from "@/components/algo/PositionChartDialog";
import {
  ActiveTripsPanel,
  ConfigSidebar,
  ControlHoverRail,
  ExecutionQualityPanel,
  LiveDot,
  PortfolioSnapshotCard,
  LogStream,
  OmsTable,
  Panel,
  PositionsTable,
  RiskPanel,
  ConsoleHydratingShell,
  StartupProgressBanner,
  SystemHealthPanel,
  TopBar,
  TradesTable,
  useLiveSystemHealth,
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
import { api, markDerivedPositionPnl } from "@/lib/api";
import { LIVE_DISABLE_CONFIRM_TOKEN } from "@/lib/breaker-presets";
import { notifyError, notifySuccess } from "@/lib/notify";
import {
  closedTradePerfFromKpi,
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
  const hydrated = live.hydrated;
  const status: AlgoStatus = live.status;
  const startupProgress = live.startupProgress;
  const bookResyncProgress = live.bookResyncProgress;
  const paperMode: boolean = live.paperMode;
  const strategy: StrategyInfo | null = live.strategy;
  const strategies: StrategyInfo[] = live.strategies;
  const equityCurve = live.equityCurve;
  const positions: Position[] = live.positions;
  const trades: Trade[] = live.trades;
  const logs: LogEntry[] = live.logs;
  const uptimeSec = live.uptimeSec;
  const workingOrders: WorkingOrder[] = live.orders;
  const workingParents: ExecutionParent[] = live.workingParents;
  const executionHistory: ExecutionParent[] = live.executionHistory;
  const executionAggregate: ExecutionAggregate = live.executionAggregate;
  const systemHealth = useLiveSystemHealth(live.systemHealth, live.systemHealthAsOf);
  const maxRiskPct = live.maxRiskPct;
  const maxGrossNotional = live.maxGrossNotional;
  const breakers = live.breakers;
  const settingsSnapshot = live.settingsSnapshot;
  const replaySummary = live.replaySummary;
  const kpi = live.kpi;
  const sessionMaxDrawdownAbs = systemHealth?.sessionMaxDrawdownAbs ?? 0;
  const sessionMaxDrawdownPct = systemHealth?.sessionMaxDrawdownPct ?? 0;
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

  const totalEquity = equityCurve.length ? equityCurve[equityCurve.length - 1]!.equity : 0;
  const startEquity =
    kpi.session_start_equity > 0
      ? kpi.session_start_equity
      : equityCurve.length
        ? equityCurve[0]!.equity
        : 0;
  const pnlAbs = totalEquity - startEquity;
  const pnlPct = startEquity > 0 ? (pnlAbs / startEquity) * 100 : 0;

  const openPnl = useMemo(() => {
    if (positions.length > 0) {
      return positions.reduce((acc, p) => acc + markDerivedPositionPnl(p), 0);
    }
    if (systemHealth != null) return systemHealth.unrealizedPnl;
    return kpi.open_pnl;
  }, [positions, systemHealth, kpi.open_pnl]);

  

  /** Rolling = last ≤200 parent-level closes; session = every reducing fill since backend start. */
  const sessionTradePerf = useMemo(() => closedTradePerfFromKpi("session", kpi), [kpi]);
  const rollingTradePerf = useMemo(() => closedTradePerfFromKpi("rolling", kpi), [kpi]);
  const closedTradePerf = kpiScope === "session" ? sessionTradePerf : rollingTradePerf;

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
  const handleControl = useCallback(
    (fn: () => Promise<unknown>, opts?: { successMessage?: string }) => {
      fn()
        .then(() => {
          if (opts?.successMessage) notifySuccess(opts.successMessage);
        })
        .catch((err) => {
          console.error("control command failed", err);
          notifyError(err);
        });
    },
    [],
  );

  const onStart = useCallback(() => {
    if (status === "running" || status === "starting" || controlPending === "start") return;
    setControlPending("start");
    handleControl(() =>
      api.start().finally(() => {
        setControlPending(null);
      }),
    );
  }, [status, controlPending, handleControl]);

  useEffect(() => {
    if (controlPending === "start" && (status === "starting" || status === "running")) {
      setControlPending(null);
    }
  }, [controlPending, status]);
  const onPause = useCallback(() => handleControl(api.pause), [handleControl]);
  const onResume = useCallback(() => handleControl(api.resume), [handleControl]);
  const onStop = useCallback(() => handleControl(api.stop), [handleControl]);
  const onEStop = useCallback(() => {
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
  }, [handleControl, live]);
  const onHaltTrading = useCallback(
    (opts?: { flatten?: boolean; pause?: boolean }) =>
      handleControl(
        async () => {
          await api.tripBreakers({
            flatten: opts?.flatten ?? true,
            pause: opts?.pause ?? true,
          });
          await live.refresh();
        },
        { successMessage: "Trading halt applied" },
      ),
    [handleControl, live],
  );

  const onPatchBreakerEnabled = useCallback(async (
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
  }, [live]);

  const onPatchSettings = useCallback(async (patch: Record<string, unknown>) => {
    try {
      await api.patchSettings(patch);
      await live.refresh();
      notifySuccess("Settings applied");
    } catch (err) {
      notifyError(err, "Failed to update settings");
      throw err;
    }
  }, [live]);
  const onFlatten = useCallback(() => {
    if (controlPending === "flatten") return;
    setControlPending("flatten");
    handleControl(() =>
      api.flatten().finally(() => {
        setControlPending(null);
      }),
    );
  }, [controlPending, handleControl]);

  // Push the slider's percentage (0-100) to the engine as a fraction.
  const onRiskCommit = useCallback((value: number[]) => {
    setRisk(value);
    handleControl(() => api.setRisk(value[0] / 100));
  }, [handleControl]);

  // Hot-swap the active strategy. The /api/state response drives the
  // ``active`` flag; we re-hydrate immediately so the UI flips without
  // waiting for the next status push.
  const onSelectStrategy = useCallback((name: string) => {
    if (strategy?.name === name) return;
    handleControl(async () => {
      await api.setStrategy(name);
      await live.refresh();
    });
  }, [strategy?.name, handleControl, live]);

  const onRearmBreakers = useCallback((code?: string) => {
    handleControl(async () => {
      await api.rearmBreakers(code ? { code } : {});
      await live.refresh();
    });
  }, [handleControl, live]);

  const onExportReport = useCallback(async () => {
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
  }, []);

  const onOpenPosition = useCallback((p: Position) => setChartSymbol(p.symbol), []);
  const onCloseChart = useCallback((o: boolean) => {
    if (!o) setChartSymbol(null);
  }, []);
  const onRearmAllBreakers = useCallback(() => onRearmBreakers(), [onRearmBreakers]);
  const onRearmBreakerCode = useCallback(
    (code: string) => onRearmBreakers(code),
    [onRearmBreakers],
  );

  const systemBusy =
    status === "starting" || controlPending === "start" || bookResyncProgress != null;
  const startDisabled =
    !backendReachable || status === "running" || systemBusy;

  if (!hydrated) {
    return <ConsoleHydratingShell />;
  }

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

      {!backendReachable && backendError ? (
        <div
          role="alert"
          className="border-b border-bear/40 bg-bear/10 px-4 py-2 text-center text-xs text-bear lg:px-8"
        >
          <span className="font-medium uppercase tracking-wider">Backend offline</span>
          {" · "}
          {backendError ??
            "Cannot reach the trading API. Restart the server process if it is down, then use Start."}
        </div>
      ) : null}

      {backendReachable && !streamConnected ? (
        <div
          role="status"
          className="border-b border-warning/40 bg-warning/10 px-4 py-2 text-center text-xs text-warning lg:px-8"
        >
          <span className="font-medium uppercase tracking-wider">Reconnecting live stream</span>
          {" · "}
          Dashboard is using REST snapshots (~5s) until WebSocket reconnects.
        </div>
      ) : null}

      <TopBar
        status={status}
        uptimeSec={uptimeSec}
        paperMode={paperMode}
        strategy={strategy}
        backendReachable={backendReachable}
        streamConnected={streamConnected}
        controlsBusy={systemBusy}
        onEStop={onEStop}
        onHaltTrading={onHaltTrading}
      />

      <main className="mx-auto max-w-[1500px] px-4 pb-6 pt-3 lg:px-8">
        <div className="flex min-w-0 flex-col gap-3">
            <section className="grid grid-cols-1 items-start gap-2 md:grid-cols-2 xl:grid-cols-3">
              <PortfolioSnapshotCard
                totalEquity={totalEquity}
                pnlAbs={pnlAbs}
                pnlPct={pnlPct}
                openPnl={openPnl}
                openPositionCount={positions.length}
                sessionMaxDrawdownAbs={sessionMaxDrawdownAbs}
                sessionMaxDrawdownPct={sessionMaxDrawdownPct}
                strategy={strategy}
                strategies={strategies}
                backendReachable={backendReachable}
              />
              <WinRateKpiCard
                perf={closedTradePerf}
                scope={kpiScope}
                onScopeChange={setKpiScope}
                tapeStats={winRateTapeStats}
                openPositionCount={positions.length}
                sessionTradePerf={sessionTradePerf}
                rollingTradePerf={rollingTradePerf}
                sessionFeesPaid={kpi.session_fees_paid}
                sessionFundingNet={kpi.session_funding_net}
                sessionStartEquity={kpi.session_start_equity}
                currentEquity={totalEquity}
                openPnl={openPnl}
              />
              <div className="flex flex-col gap-2 md:col-span-2 xl:col-span-1">
                <RiskPanel
                  systemHealth={systemHealth}
                  maxRiskPct={maxRiskPct}
                  maxGrossNotional={maxGrossNotional}
                  totalEquity={totalEquity}
                />
                <ActiveTripsPanel
                  breakers={breakers}
                  onRearmAll={onRearmAllBreakers}
                  onRearmCode={onRearmBreakerCode}
                  compact
                />
              </div>
            </section>

            {replaySummary ? (
              <div className="rounded-md border border-bull/30 bg-bull/5 px-3 py-1.5 text-xs text-bull">
                {replaySummary}
              </div>
            ) : null}

            <section className="grid grid-cols-1 gap-3 lg:grid-cols-2">
              <Panel
                title="EQUITY CURVE"
                right={
                  <div className="flex items-center gap-3 text-[11px] text-muted-foreground">
                    <span className="flex items-center gap-1.5">
                      <span className="size-1.5 rounded-full bg-bull" /> realized
                    </span>
                    <span className="flex items-center gap-1.5">
                      <span className="size-1.5 rounded-full bg-muted-foreground" /> mark
                    </span>
                    <span>· {equityCurve.length.toLocaleString()} samples</span>
                  </div>
                }
              >
                <div className="h-[280px] px-2 pb-1">
                  <EquityChart points={equityCurve} interactive />
                </div>
              </Panel>

              <Panel
                title="LIVE LOG"
                right={
                  <span className="flex items-center gap-3">
                    <span className="font-mono text-[11px] tabular-nums text-muted-foreground">
                      {logs.length.toLocaleString()} lines
                    </span>
                    <LiveDot active={status === "running"} />
                  </span>
                }
              >
                <LogStream logs={logs} className="h-[280px]" />
              </Panel>
            </section>

            <Panel
              title="OPEN POSITIONS"
              right={
                <span className="text-[11px] text-muted-foreground">{positions.length} active</span>
              }
            >
              <PositionsTable positions={positions} onOpen={onOpenPosition} />
            </Panel>

            {systemHealth ? (
              <SystemHealthPanel
                health={systemHealth}
                maxGrossNotional={maxGrossNotional}
                status={status}
                expanded={healthExpanded}
                onExpandedChange={setHealthExpanded}
                exportError={exportError}
                onExportReport={onExportReport}
              />
            ) : null}

            <section className="grid grid-cols-1 gap-3 lg:grid-cols-3">
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

            <Panel
              title="RECENT TRADES"
              right={
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 gap-1 text-[11px]"
                  onClick={() => void live.refresh()}
                >
                  <RefreshCcw className="size-3" /> refresh
                </Button>
              }
            >
              <TradesTable trades={trades} strategies={strategies} />
            </Panel>
        </div>
      </main>

      <ControlHoverRail
        status={status}
        backendReachable={backendReachable}
        systemBusy={systemBusy}
        startDisabled={startDisabled}
        controlPending={controlPending}
        risk={risk}
        settingsSnapshot={settingsSnapshot}
        onStart={onStart}
        onResume={onResume}
        onPause={onPause}
        onStop={onStop}
        onFlatten={onFlatten}
        onRiskChange={setRisk}
        onRiskCommit={onRiskCommit}
        onPatchSettings={onPatchSettings}
      />

      <ConfigSidebar
        strategies={strategies}
        activeName={strategy?.name ?? null}
        multiMode={strategy?.name === "all"}
        backendReachable={backendReachable}
        onSelectStrategy={onSelectStrategy}
        breakers={breakers}
        paperMode={paperMode}
        onPatchBreakerEnabled={onPatchBreakerEnabled}
      />

      <PositionChartDialog
        position={positions.find((p) => p.symbol === chartSymbol) ?? null}
        open={chartSymbol !== null}
        onOpenChange={onCloseChart}
      />

    </div>
  );
}