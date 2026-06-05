import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronDown, Loader2, ShieldAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Separator } from "@/components/ui/separator";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "@/lib/utils";
import { BreakerLiveConfirmDialog } from "@/components/algo/BreakerLiveConfirmDialog";
import { Panel, ToggleRow } from "@/components/algo/dashboard/primitives";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  BREAKER_PRESET_CONNECTIVITY,
  BREAKER_PRESET_DISABLE_MAJORS,
  BREAKER_PRESET_FULL,
  BREAKER_PRESET_NON_STOP_MM,
  LIVE_DISABLE_CONFIRM_TOKEN,
} from "@/lib/breaker-presets";
import type {
  AlgoStatus,
  BreakerDefinition,
  BreakerList,
  BreakerStatus,
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
const ALL_STRATEGIES_OPTION: StrategyInfo = {
  name: "all",
  label: "All strategies (netted)",
  description:
    "Pairs, SMA, blend, flow momentum, and MM2 run together; alpha signals are netted per symbol.",
  active: false,
};

export function StrategyPicker({
  strategies,
  activeName,
  multiMode,
  backendReachable,
  onSelect,
}: {
  strategies: StrategyInfo[];
  activeName: string | null;
  multiMode: boolean;
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
            {backendReachable ? "Loading\u2026" : "Backend offline"}
          </span>
        )}
      </div>
      {multiMode && strategies.length > 0 ? (
        <p className="mb-2 text-[11px] text-muted-foreground">
          All registered strategies are ticking; alpha legs are netted per symbol before execution.
        </p>
      ) : null}
      <div className="grid grid-cols-1 gap-1.5">
        {options.map((s) => {
          const isActive = (activeName ?? "") === s.name;
          const isRunningInAll = multiMode && s.name !== "all";
          const highlighted = isActive || isRunningInAll;
          return (
            <button
              key={s.name}
              type="button"
              onClick={() => onSelect(s.name)}
              className={cn(
                "flex flex-col items-start gap-0.5 rounded-sm border px-2 py-1.5 text-left transition-colors",
                highlighted
                  ? "border-bull/60 bg-bull/10 text-bull"
                  : "border-border bg-background/40 text-foreground/80 hover:border-bull/30 hover:text-foreground",
              )}
            >
              <div className="flex w-full items-center gap-2">
                <span className="text-xs font-semibold tracking-tight">{s.label}</span>
                {isActive && (
                  <span className="ml-auto rounded-sm border border-bull/40 bg-bull/10 px-1.5 py-0.5 text-[9px] uppercase tracking-wider">
                    Active
                  </span>
                )}
                {isRunningInAll && (
                  <span className="ml-auto rounded-sm border border-bull/30 bg-bull/5 px-1.5 py-0.5 text-[9px] uppercase tracking-wider text-bull/90">
                    Running
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

export function RiskPanel({
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

const BREAKER_GROUP_LABEL: Record<BreakerDefinition["group"], string> = {
  market_data: "Market data",
  execution: "Execution",
  portfolio: "Portfolio kills",
  reconciliation: "Reconciliation",
  market_making: "Market making",
  operator: "Operator",
};

const BREAKER_GROUP_ORDER: BreakerDefinition["group"][] = [
  "market_data",
  "execution",
  "portfolio",
  "reconciliation",
  "market_making",
  "operator",
];

const PRESET_TOOLTIPS = {
  full: "Enable every guard (default safest profile).",
  nonStopMm:
    "Relax symbol-level entry guards (stale tick, wide spread, MM flow). Keeps portfolio kills and reconcile.",
  connectivity:
    "Ignore brief market/user WebSocket stale pauses and order reconcile lag so quoting can continue.",
  disableMajors:
    "Turn off all major kill switches (drawdown, daily loss, exec quality, reconcile). Clears latched trips. Requires confirmation in LIVE.",
} as const;

function majorsInPatch(
  enabled: Record<string, boolean>,
  patch: Record<string, boolean>,
  registry: BreakerDefinition[],
): string[] {
  const majorCodes = new Set(registry.filter((d) => d.severity === "major").map((d) => d.code));
  const out: string[] = [];
  for (const [code, next] of Object.entries(patch)) {
    if (enabled[code] !== false && next === false && majorCodes.has(code) && code !== "operator_halt") {
      out.push(code);
    }
  }
  return out;
}

export function BreakersPanel({
  breakers,
  paperMode,
  backendReachable,
  onRearmAll,
  onRearmCode,
  onPatchEnabled,
}: {
  breakers: BreakerList;
  paperMode: boolean;
  backendReachable: boolean;
  onRearmAll: () => void;
  onRearmCode: (code: string) => void;
  onPatchEnabled: (
    patch: Record<string, boolean>,
    opts?: { confirmLiveDisable?: boolean; confirmToken?: string },
  ) => Promise<void>;
}) {
  const [localEnabled, setLocalEnabled] = useState<Record<string, boolean>>(breakers.enabled);
  const [pending, setPending] = useState(false);
  const [liveConfirm, setLiveConfirm] = useState<{
    patch: Record<string, boolean>;
  } | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    setLocalEnabled(breakers.enabled);
  }, [breakers.enabled]);

  const registry =
    breakers.registry.length > 0
      ? breakers.registry
      : Object.entries(localEnabled).map(([code, on]) => ({
          code,
          severity: "minor" as const,
          scope: "engine" as const,
          label: code,
          description: "",
          group: "market_data" as const,
          defaultEnabled: true,
          disableable: code !== "operator_halt",
        }));

  const applyPatch = useCallback(
    async (
      patch: Record<string, boolean>,
      opts?: { confirmLiveDisable?: boolean; confirmToken?: string },
    ) => {
      const next = { ...localEnabled, ...patch };
      setLocalEnabled(next);
      setPending(true);
      try {
        await onPatchEnabled(patch, opts);
      } catch {
        setLocalEnabled(breakers.enabled);
      } finally {
        setPending(false);
      }
    },
    [breakers.enabled, localEnabled, onPatchEnabled],
  );

  const requestPatch = useCallback(
    (patch: Record<string, boolean>) => {
      if (!paperMode) {
        const majors = majorsInPatch(localEnabled, patch, registry);
        if (majors.length > 0) {
          setLiveConfirm({ patch });
          return;
        }
      }
      void applyPatch(patch);
    },
    [applyPatch, localEnabled, paperMode, registry],
  );

  const onToggle = (code: string, checked: boolean) => {
    const patch = { [code]: checked };
    if (debounceRef.current) clearTimeout(debounceRef.current);

    const def = registry.find((d) => d.code === code);
    const majorDisable =
      !checked && def?.severity === "major" && code !== "operator_halt";
    if (!paperMode && majorDisable) {
      requestPatch(patch);
      return;
    }

    setLocalEnabled((prev) => ({ ...prev, ...patch }));
    debounceRef.current = setTimeout(() => requestPatch(patch), 300);
  };

  const onPreset = (preset: Record<string, boolean>) => {
    const patch: Record<string, boolean> = {};
    for (const def of registry) {
      if (!def.disableable) continue;
      const next = preset[def.code] ?? true;
      if (localEnabled[def.code] !== next) {
        patch[def.code] = next;
      }
    }
    if (Object.keys(patch).length === 0) return;
    requestPatch(patch);
  };

  const presetButton = (
    label: string,
    tooltip: string,
    preset: Record<string, boolean>,
  ) => (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-7 text-[10px]"
          disabled={!backendReachable || pending}
          onClick={() => onPreset(preset)}
        >
          {label}
        </Button>
      </TooltipTrigger>
      <TooltipContent side="bottom" className="max-w-[240px] text-xs">
        {tooltip}
      </TooltipContent>
    </Tooltip>
  );

  return (
    <>
      <TooltipProvider delayDuration={300}>
        <Panel
          title="CIRCUIT BREAKERS"
          right={
            <div className="flex items-center gap-2">
              {pending ? (
                <Loader2 className="size-3.5 animate-spin text-muted-foreground" aria-label="Saving" />
              ) : null}
              {breakers.active.length > 0 ? (
                <Button variant="outline" size="sm" className="h-7 text-[11px]" onClick={onRearmAll}>
                  Rearm all
                </Button>
              ) : null}
            </div>
          }
        >
          <div className="space-y-3 p-4">
            <p className="text-[11px] leading-snug text-muted-foreground">
              <span className="font-medium text-foreground">On</span> = guard may pause entries or halt
              trading when tripped. <span className="font-medium text-foreground">Off</span> = ignored so
              trading can continue.
            </p>

            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  type="button"
                  variant="destructive"
                  size="sm"
                  className="h-8 w-full text-[11px]"
                  disabled={!backendReachable || pending}
                  onClick={() => onPreset(BREAKER_PRESET_DISABLE_MAJORS)}
                >
                  Disable major kills (GCP non-stop)
                </Button>
              </TooltipTrigger>
              <TooltipContent side="bottom" className="max-w-[280px] text-xs">
                {PRESET_TOOLTIPS.disableMajors}
              </TooltipContent>
            </Tooltip>

            <div className="flex flex-wrap gap-1.5">
              {presetButton("Full protection", PRESET_TOOLTIPS.full, BREAKER_PRESET_FULL)}
              {presetButton("Non-stop MM", PRESET_TOOLTIPS.nonStopMm, BREAKER_PRESET_NON_STOP_MM)}
              {presetButton("Connectivity only", PRESET_TOOLTIPS.connectivity, BREAKER_PRESET_CONNECTIVITY)}
            </div>

            <ScrollArea className="h-[200px] pr-2">
              {BREAKER_GROUP_ORDER.map((group) => {
                const items = registry.filter((d) => d.group === group && d.code !== "operator_halt");
                const operatorDef = registry.find((d) => d.code === "operator_halt");
                if (!items.length && group !== "operator") return null;
                return (
                  <div key={group} className="mb-3">
                    <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                      {BREAKER_GROUP_LABEL[group]}
                    </div>
                    <div className="space-y-1">
                      {items.map((def) => (
                        <div
                          key={def.code}
                          className="flex items-center justify-between gap-2 rounded-sm border border-border/50 bg-card/30 px-2 py-1.5"
                          title={def.description || undefined}
                        >
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-1.5">
                              <span className="truncate text-[11px] font-medium">{def.label}</span>
                              <Badge variant="outline" className="text-[8px] uppercase">
                                {def.severity}
                              </Badge>
                            </div>
                            {def.description ? (
                              <p className="line-clamp-2 text-[10px] text-muted-foreground">{def.description}</p>
                            ) : null}
                          </div>
                          <Switch
                            checked={localEnabled[def.code] ?? true}
                            disabled={!def.disableable || !backendReachable || pending}
                            onCheckedChange={(c) => onToggle(def.code, c)}
                            aria-label={`${def.label} enabled`}
                          />
                        </div>
                      ))}
                      {group === "operator" && operatorDef ? (
                        <div className="rounded-sm border border-dashed border-border/60 bg-muted/20 px-2 py-1.5 text-[10px] text-muted-foreground">
                          <span className="font-medium text-foreground">{operatorDef.label}</span>
                          {" — always available via "}
                          <span className="font-medium text-foreground">Halt</span> /{" "}
                          <span className="font-medium text-foreground">E-Stop</span>; not disableable.
                        </div>
                      ) : null}
                    </div>
                  </div>
                );
              })}
            </ScrollArea>

          <Separator />

          <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            Active trips
          </div>
          {breakers.active.length === 0 ? (
            <p className="text-center text-xs text-muted-foreground">No active breakers.</p>
          ) : (
            <ScrollArea className="h-[100px]">
              <div className="space-y-2">
                {breakers.active.map((b) => (
                  <BreakerRow
                    key={`${b.code}-${b.target ?? ""}`}
                    breaker={b}
                    onRearm={() => onRearmCode(b.code)}
                  />
                ))}
              </div>
            </ScrollArea>
          )}
          {breakers.history.length > 0 ? (
            <p className="text-[10px] text-muted-foreground">
              {breakers.history.length} recent event(s) in history
            </p>
          ) : null}
        </div>
      </Panel>
      </TooltipProvider>

      <BreakerLiveConfirmDialog
        open={liveConfirm != null}
        codes={liveConfirm ? majorsInPatch(localEnabled, liveConfirm.patch, registry) : []}
        onOpenChange={(open) => {
          if (!open) {
            setLocalEnabled(breakers.enabled);
            setLiveConfirm(null);
          }
        }}
        onConfirm={() => {
          if (!liveConfirm) return;
          void applyPatch(liveConfirm.patch, {
            confirmLiveDisable: true,
            confirmToken: LIVE_DISABLE_CONFIRM_TOKEN,
          }).finally(() => setLiveConfirm(null));
        }}
      />
    </>
  );
}

export function ControlLimitsPanel({
  settings,
  backendReachable,
  onPatchSettings,
}: {
  settings: Record<string, unknown>;
  backendReachable: boolean;
  onPatchSettings: (patch: Record<string, unknown>) => Promise<void | boolean>;
}) {
  const [open, setOpen] = useState(false);
  const num = (key: string, fallback: number) => {
    const v = settings[key];
    return typeof v === "number" && Number.isFinite(v) ? v : fallback;
  };
  const bool = (key: string, fallback: boolean) =>
    typeof settings[key] === "boolean" ? Boolean(settings[key]) : fallback;

  const [dailyLoss, setDailyLoss] = useState(() => (num("daily_loss_kill_pct", 0.05) * 100).toFixed(1));
  const [cooldown, setCooldown] = useState(() => String(num("breaker_minor_cooldown_sec", 60)));
  const [wsStale, setWsStale] = useState(() => String(num("ws_stale_pause_sec", 30)));
  const [maxRejects, setMaxRejects] = useState(() => String(num("max_consecutive_rejects", 3)));

  useEffect(() => {
    setDailyLoss((num("daily_loss_kill_pct", 0.05) * 100).toFixed(1));
    setCooldown(String(num("breaker_minor_cooldown_sec", 60)));
    setWsStale(String(num("ws_stale_pause_sec", 30)));
    setMaxRejects(String(num("max_consecutive_rejects", 3)));
  }, [settings]);

  const commitLimits = async () => {
    const dl = parseFloat(dailyLoss);
    const cd = parseFloat(cooldown);
    const ws = parseFloat(wsStale);
    const mr = parseInt(maxRejects, 10);
    await onPatchSettings({
      daily_loss_kill_pct: Number.isFinite(dl) ? dl / 100 : 0.05,
      breaker_minor_cooldown_sec: Number.isFinite(cd) ? cd : 60,
      ws_stale_pause_sec: Number.isFinite(ws) ? ws : 30,
      max_consecutive_rejects: Number.isFinite(mr) ? mr : 3,
    });
  };

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger asChild>
        <Button variant="ghost" size="sm" className="h-8 w-full justify-between px-0 text-[11px] uppercase tracking-wider text-muted-foreground">
          Quick limits
          <ChevronDown className={cn("size-4 transition-transform", open && "rotate-180")} />
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent className="space-y-3 pt-2">
        <div className="grid grid-cols-2 gap-2">
          <div className="space-y-1">
            <Label className="text-[10px] uppercase text-muted-foreground">Daily loss %</Label>
            <Input value={dailyLoss} onChange={(e) => setDailyLoss(e.target.value)} className="h-8 font-mono text-xs" />
          </div>
          <div className="space-y-1">
            <Label className="text-[10px] uppercase text-muted-foreground">Minor cooldown s</Label>
            <Input value={cooldown} onChange={(e) => setCooldown(e.target.value)} className="h-8 font-mono text-xs" />
          </div>
          <div className="space-y-1">
            <Label className="text-[10px] uppercase text-muted-foreground">WS stale s</Label>
            <Input value={wsStale} onChange={(e) => setWsStale(e.target.value)} className="h-8 font-mono text-xs" />
          </div>
          <div className="space-y-1">
            <Label className="text-[10px] uppercase text-muted-foreground">Max rejects</Label>
            <Input value={maxRejects} onChange={(e) => setMaxRejects(e.target.value)} className="h-8 font-mono text-xs" />
          </div>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-8 w-full text-[11px]"
          disabled={!backendReachable}
          onClick={() => void commitLimits()}
        >
          Apply limits
        </Button>
        <ToggleRow
          label="Flatten on stop"
          hint="Market-out residuals when engine stops"
          checked={bool("flatten_on_stop", true)}
          onChange={(c) => {
            if (backendReachable) void onPatchSettings({ flatten_on_stop: c });
          }}
        />
        <ToggleRow
          label="Auto-rearm losses"
          hint="Clear consecutive-loss latch after auto-flatten"
          checked={bool("auto_rearm_consecutive_losses_after_flatten", true)}
          onChange={(c) => {
            if (backendReachable) void onPatchSettings({ auto_rearm_consecutive_losses_after_flatten: c });
          }}
        />
        <ToggleRow
          label="Cancel orphan orders"
          hint="Reconcile cancels venue orders unknown to OMS"
          checked={bool("reconcile_cancel_orphans", true)}
          onChange={(c) => {
            if (backendReachable) void onPatchSettings({ reconcile_cancel_orphans: c });
          }}
        />
        <ToggleRow
          label="Dynamic spread gate"
          hint="EWMA spread veto vs static max_entry_spread_bps"
          checked={bool("spread_dynamic_enabled", true)}
          onChange={(c) => {
            if (backendReachable) void onPatchSettings({ spread_dynamic_enabled: c });
          }}
        />
      </CollapsibleContent>
    </Collapsible>
  );
}

export function BreakerRow({ breaker, onRearm }: { breaker: BreakerStatus; onRearm: () => void }) {
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

