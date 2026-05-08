import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CircleDot,
  Cpu,
  Gauge,
  Pause,
  Play,
  Power,
  RefreshCcw,
  Settings2,
  Square,
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
import {
  type AlgoStatus,
  type LogEntry,
  type Position,
  type Trade,
  initialLogs,
  initialPositions,
  initialTrades,
  makeEquitySeries,
} from "@/components/algo/mockData";

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
  const [status, setStatus] = useState<AlgoStatus>("running");
  const [equity, setEquity] = useState<number[]>(() => makeEquitySeries());
  const [positions, setPositions] = useState<Position[]>(initialPositions);
  const [trades, setTrades] = useState<Trade[]>(initialTrades);
  const [logs, setLogs] = useState<LogEntry[]>(initialLogs);
  const [risk, setRisk] = useState<number[]>([35]);
  const [autoCompound, setAutoCompound] = useState(true);
  const [paperMode, setPaperMode] = useState(false);
  const [uptimeSec, setUptimeSec] = useState(4 * 3600 + 17 * 60 + 22);
  const tickRef = useRef(0);

  // Live tick simulation
  useEffect(() => {
    const id = setInterval(() => {
      tickRef.current += 1;
      if (status === "running") setUptimeSec((u) => u + 1);

      setEquity((prev) => {
        const last = prev[prev.length - 1];
        const drift = status === "running" ? (Math.random() - 0.45) * 60 : 0;
        const next = Math.max(0, last + drift);
        return [...prev.slice(1), Math.round(next * 100) / 100];
      });

      if (status === "running") {
        setPositions((prev) =>
          prev.map((p) => {
            const wiggle = (Math.random() - 0.5) * (p.mark * 0.0015);
            return { ...p, mark: Math.round((p.mark + wiggle) * 100) / 100 };
          }),
        );
      }

      // Occasionally inject a new trade + log line
      if (status === "running" && tickRef.current % 6 === 0) {
        const symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "ARB/USDT"];
        const sym = symbols[Math.floor(Math.random() * symbols.length)];
        const side: "buy" | "sell" = Math.random() > 0.5 ? "buy" : "sell";
        const price = +(100 + Math.random() * 70000).toFixed(2);
        const qty = +(Math.random() * 0.5).toFixed(3);
        const pnl = Math.random() > 0.55 ? +((Math.random() - 0.4) * 40).toFixed(2) : null;
        const ts = new Date().toLocaleTimeString("en-GB", { hour12: false });
        const id = `T-${10473 + tickRef.current}`;
        setTrades((t) => [{ id, ts, symbol: sym, side, qty, price, pnl }, ...t].slice(0, 40));
        setLogs((l) =>
          [
            { ts, level: "info" as const, msg: `Order filled: ${side.toUpperCase()} ${qty} ${sym} @ ${price.toLocaleString()}` },
            ...l,
          ].slice(0, 60),
        );
      }
    }, 1200);
    return () => clearInterval(id);
  }, [status]);

  const totalEquity = equity[equity.length - 1];
  const startEquity = equity[0];
  const pnlAbs = totalEquity - startEquity;
  const pnlPct = (pnlAbs / startEquity) * 100;

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

  const onStart = () => {
    setStatus("running");
    pushLog("info", "Algorithm resumed by operator");
  };
  const onPause = () => {
    setStatus("paused");
    pushLog("warn", "Algorithm paused — open positions still tracked");
  };
  const onStop = () => {
    setStatus("stopped");
    pushLog("error", "EMERGENCY STOP — all new orders halted");
  };
  const onFlatten = () => {
    pushLog("warn", `Flatten requested · closing ${positions.length} positions`);
    setPositions([]);
  };

  function pushLog(level: LogEntry["level"], msg: string) {
    const ts = new Date().toLocaleTimeString("en-GB", { hour12: false });
    setLogs((l) => [{ ts, level, msg }, ...l].slice(0, 60));
  }

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
                <Slider value={risk} onValueChange={setRisk} min={5} max={100} step={5} />
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

        {/* Positions + trades */}
        <section className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
          <Panel
            className="lg:col-span-2"
            title="OPEN POSITIONS"
            right={
              <span className="text-[11px] text-muted-foreground">{positions.length} active</span>
            }
          >
            <PositionsTable positions={positions} />
          </Panel>

          <Panel title="LIVE LOG" right={<LiveDot active={status === "running"} />}>
            <LogStream logs={logs} />
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

function PositionsTable({ positions }: { positions: Position[] }) {
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
          </tr>
        </thead>
        <tbody className="font-mono">
          {positions.map((p) => {
            const dir = p.side === "long" ? 1 : -1;
            const pnl = (p.mark - p.entry) * p.size * dir;
            const pct = ((p.mark - p.entry) / p.entry) * 100 * dir;
            const positive = pnl >= 0;
            return (
              <tr key={p.symbol} className="border-t border-border/60 hover:bg-accent/30">
                <td className="px-4 py-2.5">{p.symbol}</td>
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
