import { Link } from "@tanstack/react-router";
import {
  AlertTriangle,
  Cpu,
  Loader2,
  Pause,
  Play,
  Power,
  Settings2,
  Wifi,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Separator } from "@/components/ui/separator";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { EM_DASH } from "@/lib/algo-format";
import type { AlgoStatus, StartupProgress, StrategyInfo } from "@/components/algo/types";

export function StartupProgressBanner(props: {
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

export function TopBar(props: {
  status: AlgoStatus;
  uptimeSec: number;
  paperMode: boolean;
  strategy: StrategyInfo | null;
  backendReachable: boolean;
  controlsBusy: boolean;
  startDisabled: boolean;
  onStart: () => void;
  onResume?: () => void;
  onPause: () => void;
  onEStop: () => void;
  onHaltTrading: (opts?: { flatten?: boolean; pause?: boolean }) => void;
  onFlatten: () => void;
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
    <TooltipProvider delayDuration={300}>
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
                {strategy?.label ?? (props.backendReachable ? "Loading..." : EM_DASH)}
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
          <Button size="sm" variant="outline" className="border-border" asChild>
            <Link to="/settings">
              <Settings2 className="size-4" /> Settings
            </Link>
          </Button>
          {status === "paused" && props.onResume ? (
            <Button
              size="sm"
              onClick={props.onResume}
              disabled={!props.backendReachable || props.controlsBusy}
              className="bg-bull text-bull-foreground hover:bg-bull/90"
            >
              <Play className="size-4" /> Resume
            </Button>
          ) : (
            <>
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
            </>
          )}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                size="sm"
                variant="outline"
                disabled={!props.backendReachable || status === "stopped" || props.controlsBusy}
                className="border-warning/50 text-warning hover:bg-warning/10"
              >
                <AlertTriangle className="size-4" /> Halt
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="border-border bg-popover">
              <DropdownMenuItem onClick={() => props.onHaltTrading({ flatten: false, pause: true })}>
                Halt + pause engine
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => props.onHaltTrading({ flatten: true, pause: true })}>
                Halt + flatten + pause
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => props.onHaltTrading({ flatten: false, pause: false })}>
                Halt only (latch breaker)
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                size="sm"
                variant="destructive"
                onClick={props.onEStop}
                disabled={!props.backendReachable || props.controlsBusy}
              >
                <Power className="size-4" /> E-Stop
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom" className="max-w-[260px] text-xs">
              Flatten all positions and stop the engine. The API stays online so you can press Start
              again without restarting the server.
            </TooltipContent>
          </Tooltip>
        </div>
      </div>
    </header>
    </TooltipProvider>
  );
}

