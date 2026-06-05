import { useState } from "react";
import { Link } from "@tanstack/react-router";
import {
  AlertTriangle,
  ChevronLeft,
  Loader2,
  Pause,
  Play,
  Settings2,
  Square,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Slider } from "@/components/ui/slider";
import { Separator } from "@/components/ui/separator";
import { ControlLimitsPanel } from "@/components/algo/dashboard/control-panels";
import { Panel } from "@/components/algo/dashboard/primitives";
import { cn } from "@/lib/utils";
import type { AlgoStatus } from "@/components/algo/types";

const PANEL_WIDTH = "min(300px,calc(100vw-3rem))";

const STATUS_META: Record<
  AlgoStatus,
  { label: string; dot: string }
> = {
  running: { label: "Running", dot: "bg-bull" },
  paused: { label: "Paused", dot: "bg-warning" },
  stopped: { label: "Stopped", dot: "bg-bear" },
  starting: { label: "Starting", dot: "bg-warning" },
};

export function ControlHoverRail({
  status,
  backendReachable,
  systemBusy,
  startDisabled,
  controlPending,
  risk,
  settingsSnapshot,
  onStart,
  onResume,
  onPause,
  onStop,
  onFlatten,
  onRiskChange,
  onRiskCommit,
  onPatchSettings,
}: {
  status: AlgoStatus;
  backendReachable: boolean;
  systemBusy: boolean;
  startDisabled: boolean;
  controlPending: "start" | "flatten" | null;
  risk: number[];
  settingsSnapshot: Record<string, unknown>;
  onStart: () => void;
  onResume: () => void;
  onPause: () => void;
  onStop: () => void;
  onFlatten: () => void;
  onRiskChange: (value: number[]) => void;
  onRiskCommit: (value: number[]) => void;
  onPatchSettings: (patch: Record<string, unknown>) => Promise<void | boolean>;
}) {
  const [hovered, setHovered] = useState(false);
  const [pinned, setPinned] = useState(false);
  const open = hovered || pinned;
  const statusMeta = STATUS_META[status];

  return (
    <>
      {pinned ? (
        <button
          type="button"
          aria-label="Close control panel"
          className="fixed inset-0 z-20 bg-background/40 backdrop-blur-[1px] lg:hidden"
          onClick={() => setPinned(false)}
        />
      ) : null}

      <div
        className="fixed right-0 top-1/2 z-30 flex -translate-y-1/2"
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
      >
        <div
          className={cn(
            "overflow-hidden transition-[width,opacity] duration-200 ease-out",
            open ? "opacity-100" : "pointer-events-none w-0 opacity-0",
          )}
          style={open ? { width: PANEL_WIDTH } : undefined}
          aria-hidden={!open}
        >
          <div
            className="flex flex-col rounded-l-md border border-r-0 border-border bg-background/95 shadow-2xl backdrop-blur"
            style={{ width: PANEL_WIDTH }}
          >
            <Panel
              title="CONTROL"
              right={
                <Badge variant="outline" className="border-border text-[10px] uppercase tracking-wider">
                  <Settings2 className="mr-1 size-3" /> live
                </Badge>
              }
            >
              <div className="space-y-3 p-3">
                <div className="grid grid-cols-3 gap-1.5">
                  {status === "paused" ? (
                    <Button
                      onClick={onResume}
                      disabled={!backendReachable || systemBusy}
                      size="sm"
                      className="col-span-2 bg-bull text-bull-foreground hover:bg-bull/90"
                    >
                      <Play className="size-4" /> RESUME
                    </Button>
                  ) : (
                    <>
                      <Button
                        onClick={onStart}
                        disabled={startDisabled}
                        size="sm"
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
                        size="sm"
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
                    size="sm"
                    variant="destructive"
                  >
                    <Square className="size-4" /> STOP
                  </Button>
                </div>

                <Button
                  onClick={onFlatten}
                  disabled={!backendReachable || controlPending === "flatten"}
                  size="sm"
                  variant="outline"
                  className="w-full border-bear/40 text-bear hover:bg-bear/10 hover:text-bear"
                >
                  <AlertTriangle className="size-4" />
                  {controlPending === "flatten" ? "Flattening…" : "Flatten all positions"}
                </Button>

                <Separator />

                <div>
                  <div className="mb-2 flex items-center justify-between text-xs">
                    <span className="uppercase tracking-wider text-muted-foreground">Risk per trade</span>
                    <span className="tabular-nums text-bull">{risk[0]}%</span>
                  </div>
                  <Slider
                    value={risk}
                    onValueChange={onRiskChange}
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

                <Button variant="outline" className="w-full lg:hidden" asChild>
                  <Link to="/settings">
                    <Settings2 className="size-4" /> Engine settings
                  </Link>
                </Button>
              </div>
            </Panel>
          </div>
        </div>

        <button
          type="button"
          onClick={() => setPinned((value) => !value)}
          className={cn(
            "relative flex w-11 shrink-0 flex-col items-center justify-center gap-2 rounded-l-md border border-r-0 border-border bg-background/90 px-1 py-5 shadow-lg backdrop-blur transition-colors",
            "hover:border-bull/40 hover:bg-bull/5",
            open && "border-bull/50 bg-bull/5",
          )}
          aria-expanded={open}
          aria-label={`Controls — engine ${statusMeta.label}. Hover or tap to open.`}
        >
          {!open ? (
            <span
              className={cn(
                "absolute -left-0.5 top-1/2 size-2 -translate-y-1/2 rounded-full shadow-[0_0_8px_rgba(34,197,94,0.8)] animate-pulse",
                statusMeta.dot,
              )}
              aria-hidden
            />
          ) : null}
          <ChevronLeft
            className={cn(
              "size-4 text-muted-foreground transition-transform duration-200",
              open && "rotate-180 text-bull",
            )}
          />
          <span className="text-[9px] font-semibold uppercase tracking-[0.18em] text-muted-foreground [writing-mode:vertical-rl]">
            Control
          </span>
          <span
            className={cn(
              "text-[10px] font-medium uppercase tracking-wider [writing-mode:vertical-rl]",
              status === "running" ? "text-bull" : status === "paused" ? "text-warning" : "text-bear",
            )}
          >
            {statusMeta.label}
          </span>
        </button>
      </div>
    </>
  );
}
