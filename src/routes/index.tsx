import { createFileRoute } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CircleDot,
  Cpu,
  Gauge,
  ListOrdered,
  Pause,
  Play,
  Power,
  RefreshCcw,
  Settings2,
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
import { cn } from "@/lib/utils";
import { EquityChart } from "@/components/algo/EquityChart";
import { PositionChartDialog } from "@/components/algo/PositionChartDialog";
import type {
  AlgoStatus,
  ExecutionAggregate,
  ExecutionParent,
  LogEntry,
  Position,
  Trade,
  WorkingOrder,
} from "@/components/algo/mockData";
import { useAlgoStream } from "@/hooks/useAlgoStream";
import { api } from "@/lib/api";

export const Route = createFileRoute("/")({
  component: Index,
  head: () => ({
    meta: [
      { title: "ALPHA-7 · Algo Trading Console" },
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
  const equity: number[] = live.equity;
  const positions: Position[] = live.positions;
  const trades: Trade[] = live.trades;
  const logs: LogEntry[] = live.logs;
  const uptimeSec: number = live.uptimeSec;
  const workingOrders: WorkingOrder[] = live.orders;
  const workingParents: ExecutionParent[] = live.workingParents;
  const executionHistory: ExecutionParent[] = live.executionHistory;
  const executionAggregate: ExecutionAggregate = live.executionAggregate;

  const [risk, setRisk] = useState<number[]>([35]);
  const [autoCompound, setAutoCompound] = useState(true);
  const [paperMode, setPaperMode] = useState(false);
  const [chartSymbol, setChartSymbol] = useState<string | null>(null);

  const totalEquity = equity.length ? equity[equity.length - 1] : 0;
  const startEquity = equity.length ? equity[0] : 0;
  const pnlAbs = totalEquity - startEquity;
  const pnlPct = startEquity > 0 ? (pnlAbs / startEquity) * 100 : 0;

  const openPnl = useMemo(
    () =>
      positions.reduce((acc, p) => {
        const dir = p.side === "long" ? 1 : -1;
        return acc + (p.mark - p.entry) * p.size * dir;
      }, 0),
    [positions],
  );

  const winRate = useMemo(() => {
    const closed = trades.filter((t) => t.pnl !== null);
    if (!closed.length) return 0;
    const wins = closed.filter((t) => (t.pnl ?? 0) > 0).length;
    return (wins / closed.length) * 100;
  }, [trades]);

  // Fire-and-forget control commands. The engine drives the next status
  // update over the WebSocket so we don't optimistically mutate React state.
  const handleControl = (fn: () => Promise<unknown>) => {
    fn().catch((err) => {
      console.error("control command failed", err);
    });
  };

  const onStart = () => handleControl(api.start);
  const onPause = () => handleControl(api.pause);
  const onStop = () => handleControl(api.stop);
  const onFlatten = () => handleControl(api.flatten);

  // Push the slider's percentage (0-100) to the engine as a fraction.
  const onRiskCommit = (value: number[]) => {
    setRisk(value);
    handleControl(() => api.setRisk(value[0] / 100));
  };

  return (
    <div className="min-h-screen text-foreground">
      <TopBar
        status={status}
        uptimeSec={uptimeSec}
        paperMode={paperMode}
        onStart={onStart}
        onPause={onPause}
        onStop={onStop}
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
          <KpiCard
            icon={<Gauge className="size-4" />}
            label="WIN RATE"
            value={`${winRate.toFixed(1)}%`}
            sub={`${trades.filter((t) => t.pnl !== null).length} closed trades`}
            tone="neutral"
          />
          <KpiCard
            icon={<Zap className="size-4" />}
            label="STRATEGY"
            value="ALPHA-7"
            sub="momentum + mean-revert"
            tone="neutral"
          />
        </section>

        {/* Main grid */}
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
                  disabled={status === "running"}
                  className="bg-bull text-bull-foreground hover:bg-bull/90 disabled:opacity-40"
                >
                  <Play className="size-4" /> START
                </Button>
                <Button
                  onClick={onPause}
                  disabled={status !== "running"}
                  variant="secondary"
                  className="border border-border"
                >
                  <Pause className="size-4" /> PAUSE
                </Button>
                <Button
                  onClick={onStop}
                  disabled={status === "stopped"}
                  variant="destructive"
                >
                  <Square className="size-4" /> STOP
                </Button>
              </div>

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
              <ToggleRow
                label="Paper trading"
                hint="Simulate orders, no exchange calls"
                checked={paperMode}
                onChange={setPaperMode}
              />

              <Separator />

              <Button
                onClick={onFlatten}
                variant="outline"
                className="w-full border-bear/40 text-bear hover:bg-bear/10 hover:text-bear"
              >
                <AlertTriangle className="size-4" /> Flatten all positions
              </Button>
            </div>
          </Panel>
        </section>

        {/* Positions + log */}
        <section className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
          <Panel
            className="lg:col-span-2"
            title="OPEN POSITIONS"
            right={
              <span className="text-[11px] text-muted-foreground">{positions.length} active</span>
            }
          >
            <PositionsTable positions={positions} onOpen={(p) => setChartSymbol(p.symbol)} />
          </Panel>

          <Panel title="LIVE LOG" right={<LiveDot active={status === "running"} />}>
            <LogStream logs={logs} />
          </Panel>
        </section>

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
    </div>
  );
}

/* ────────────────── pieces ────────────────── */

function TopBar(props: {
  status: AlgoStatus;
  uptimeSec: number;
  paperMode: boolean;
  onStart: () => void;
  onPause: () => void;
  onStop: () => void;
  onFlatten: () => void;
}) {
  const { status, uptimeSec, paperMode } = props;
  const statusMeta = {
    running: { label: "RUNNING", color: "text-bull", dot: "bg-bull glow-bull" },
    paused: { label: "PAUSED", color: "text-warning", dot: "bg-warning" },
    stopped: { label: "STOPPED", color: "text-bear", dot: "bg-bear glow-bear" },
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
              <div className="text-sm font-semibold tracking-wide">ALPHA-7 · v1.4.2</div>
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
          <Button size="sm" variant="ghost" onClick={props.onPause} disabled={status !== "running"}>
            <Pause className="size-4" />
          </Button>
          <Button
            size="sm"
            onClick={props.onStart}
            disabled={status === "running"}
            className="bg-bull text-bull-foreground hover:bg-bull/90"
          >
            <Play className="size-4" /> Start
          </Button>
          <Button size="sm" variant="destructive" onClick={props.onStop} disabled={status === "stopped"}>
            <Power className="size-4" /> Kill
          </Button>
        </div>
      </div>
    </header>
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
  sub: string;
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
            const dir = p.side === "long" ? 1 : -1;
            const pnl = (p.mark - p.entry) * p.size * dir;
            const pct = ((p.mark - p.entry) / p.entry) * 100 * dir;
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
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-[10px] uppercase tracking-wider text-muted-foreground">
            <th className="px-4 py-2 text-left font-normal">Time</th>
            <th className="px-2 py-2 text-left font-normal">Order</th>
            <th className="px-2 py-2 text-left font-normal">Symbol</th>
            <th className="px-2 py-2 text-left font-normal">Side</th>
            <th className="px-2 py-2 text-right font-normal">Qty</th>
            <th className="px-2 py-2 text-right font-normal">Price</th>
            <th className="px-4 py-2 text-right font-normal">PnL</th>
          </tr>
        </thead>
        <tbody className="font-mono">
          {trades.slice(0, 12).map((t) => (
            <tr key={t.id} className="border-t border-border/60 hover:bg-accent/30">
              <td className="px-4 py-2 text-muted-foreground tabular-nums">{t.ts}</td>
              <td className="px-2 py-2 text-xs text-muted-foreground">{t.id}</td>
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
              <td className="px-2 py-2 text-right tabular-nums">{t.price.toLocaleString()}</td>
              <td
                className={cn(
                  "px-4 py-2 text-right tabular-nums",
                  t.pnl === null
                    ? "text-muted-foreground"
                    : t.pnl >= 0
                      ? "text-bull"
                      : "text-bear",
                )}
              >
                {t.pnl === null ? "—" : `${t.pnl >= 0 ? "+" : ""}${t.pnl.toFixed(2)}`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function LogStream({ logs }: { logs: LogEntry[] }) {
  const color: Record<LogEntry["level"], string> = {
    info: "text-muted-foreground",
    warn: "text-warning",
    error: "text-bear",
    signal: "text-bull",
  };
  const tag: Record<LogEntry["level"], string> = {
    info: "INFO",
    warn: "WARN",
    error: "ERR ",
    signal: "SIG ",
  };
  return (
    <ScrollArea className="h-[320px]">
      <div className="space-y-1 px-3 py-2 font-mono text-[12px] leading-relaxed">
        {logs.map((l, i) => (
          <div key={i} className="flex gap-2">
            <span className="shrink-0 text-muted-foreground/70 tabular-nums">{l.ts}</span>
            <span className={cn("shrink-0 font-semibold", color[l.level])}>{tag[l.level]}</span>
            <span className="text-foreground/90">{l.msg}</span>
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
        <span className="tabular-nums">
          impact {parent.impactBps.toFixed(1)} bps
        </span>
        <span className="tabular-nums">{parent.durationSec.toFixed(1)}s</span>
      </div>

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
                      "ml-auto tabular-nums",
                      r.slippageBps > 0 ? "text-bear" : "text-bull",
                    )}
                  >
                    {r.slippageBps >= 0 ? "+" : ""}
                    {r.slippageBps.toFixed(1)} bps
                  </span>
                  <span className="tabular-nums text-muted-foreground">
                    {r.durationSec.toFixed(1)}s
                  </span>
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
