"use client";

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, type SettingsDTO } from "@/lib/api";
import { notifyError, notifySuccess } from "@/lib/notify";
import { cn } from "@/lib/utils";

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved?: () => void;
  /** Active strategy label from the dashboard (informational). */
  activeStrategyLabel?: string | null;
};

function titleCaseKey(key: string): string {
  return key
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

function coerceForPatch(key: string, ui: unknown, baseline: unknown): unknown {
  if (key === "binance_api_key" || key === "binance_api_secret") {
    if (ui === "***" || ui === "") return undefined;
    return ui;
  }
  if (typeof baseline === "boolean") return Boolean(ui);
  if (typeof baseline === "number") {
    if (ui === "" || ui === null || ui === undefined) return baseline;
    const n = typeof ui === "number" ? ui : Number(ui);
    return Number.isFinite(n) ? n : baseline;
  }
  if (Array.isArray(baseline)) {
    if (typeof ui === "string") {
      return ui
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
    }
    return ui;
  }
  return ui;
}

function isStrategyParamKey(key: string): boolean {
  return (
    key === "strategy" ||
    key === "symbols" ||
    key === "base_currency" ||
    key.startsWith("pair_") ||
    key.startsWith("sma_") ||
    key.startsWith("mm_") ||
    key.startsWith("mm2_") ||
    key.startsWith("blend_") ||
    key.startsWith("flow_") ||
    key === "flow_universe_auto"
  );
}

function isSystemKey(key: string): boolean {
  return (
    key === "venue" ||
    key === "trading_mode" ||
    key.startsWith("binance_") ||
    key.startsWith("ibkr_") ||
    key === "api_host" ||
    key === "api_port" ||
    key === "klines_cache_ttl_sec" ||
    key === "cors_origins" ||
    key.startsWith("persist_") ||
    key.startsWith("log_file_") ||
    key === "log_level" ||
    key === "engine_autostart"
  );
}

/** Canonical ``settings.strategy`` values (matches backend strategy ``name`` fields). */
const BOOT_STRATEGY_OPTIONS: { value: string; label: string }[] = [
  { value: "all", label: "All strategies (netted)" },
  { value: "pairs_trading_usdt_usdc", label: "Pairs trading (USDT/USDC)" },
  { value: "sma_crossover", label: "SMA crossover" },
  { value: "blended_signals", label: "Blended signals (EMA/MACD/RSI/BB)" },
  { value: "flow_momentum", label: "Flow momentum (tape follow)" },
  { value: "market_making_v2", label: "Market making (fee-aware quotes)" },
];

const selectTriggerClass =
  "h-9 w-full border-border bg-secondary/60 font-mono text-xs text-foreground shadow-sm hover:bg-secondary/80";

const selectContentClass =
  "scrollbar-themed z-[300] max-h-[min(60vh,24rem)] border-border bg-popover text-popover-foreground";

function isRadixSelectPortalTarget(node: EventTarget | null): boolean {
  return (
    node instanceof Element &&
    Boolean(
      node.closest("[data-radix-select-viewport]") ||
        node.closest('[role="listbox"]') ||
        node.closest("[data-radix-popper-content-wrapper]"),
    )
  );
}

function SettingsSelect(props: {
  id: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  placeholder?: string;
}) {
  const { id, value, onChange, options, placeholder } = props;
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger
        id={id}
        className={cn(selectTriggerClass)}
        onPointerDown={(e) => e.stopPropagation()}
      >
        <SelectValue placeholder={placeholder ?? "Select…"} />
      </SelectTrigger>
      <SelectContent
        position="popper"
        sideOffset={6}
        collisionPadding={12}
        className={cn(selectContentClass)}
        onCloseAutoFocus={(e) => e.preventDefault()}
      >
        {options.map((o) => (
          <SelectItem key={o.value} value={o.value} className="font-mono text-xs">
            {o.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

export function SettingsDialog({ open, onOpenChange, onSaved, activeStrategyLabel }: Props) {
  const [baseline, setBaseline] = useState<SettingsDTO | null>(null);
  const [draft, setDraft] = useState<SettingsDTO>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState("strategy");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.getSettings();
      setBaseline(res.settings);
      setDraft({ ...res.settings });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load settings");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  useEffect(() => {
    if (open) setActiveTab("strategy");
  }, [open]);

  const allKeys = useMemo(() => Object.keys(draft).sort(), [draft]);

  const strategyKeys = useMemo(() => allKeys.filter(isStrategyParamKey), [allKeys]);
  const systemKeys = useMemo(() => allKeys.filter(isSystemKey), [allKeys]);
  const riskKeys = useMemo(
    () =>
      allKeys.filter(
        (k) => !isStrategyParamKey(k) && !isSystemKey(k) && k !== "breaker_enabled",
      ),
    [allKeys],
  );

  const updateField = (key: string, value: unknown) => {
    setDraft((d) => ({ ...d, [key]: value }));
  };

  const renderField = (key: string) => {
    const val = draft[key];
    const label = titleCaseKey(key);

    if (typeof val === "boolean") {
      return (
        <div
          key={key}
          className="flex items-center justify-between gap-4 rounded-sm border border-border/60 bg-card/40 px-3 py-2"
        >
          <Label htmlFor={key} className="cursor-pointer text-[11px] uppercase tracking-wider text-muted-foreground">
            {label}
          </Label>
          <Switch id={key} checked={val} onCheckedChange={(c) => updateField(key, c)} />
        </div>
      );
    }

    if (Array.isArray(val)) {
      return (
        <div key={key} className="space-y-1.5">
          <Label htmlFor={key} className="text-[11px] uppercase tracking-wider text-muted-foreground">
            {label}
          </Label>
          <Input
            id={key}
            value={val.join(", ")}
            onChange={(e) =>
              updateField(
                key,
                e.target.value
                  .split(",")
                  .map((s) => s.trim())
                  .filter(Boolean),
              )
            }
            className="font-mono text-xs"
          />
        </div>
      );
    }

    if (key === "trading_mode") {
      const tm = String(val ?? "").toLowerCase();
      const known = tm === "live" || tm === "paper";
      const tradingOptions: { value: string; label: string }[] = [];
      if (!known && tm) tradingOptions.push({ value: tm, label: `${tm} (current)` });
      tradingOptions.push({ value: "paper", label: "paper" }, { value: "live", label: "live" });
      const tradingValue = tm || "paper";
      return (
        <div key={key} className="space-y-1.5">
          <Label htmlFor={key} className="text-[11px] uppercase tracking-wider text-muted-foreground">
            {label}
          </Label>
          <SettingsSelect
            id={key}
            value={tradingValue}
            onChange={(v) => updateField(key, v)}
            options={tradingOptions}
          />
        </div>
      );
    }

    if (key === "strategy") {
      const v = String(val ?? "");
      const known = BOOT_STRATEGY_OPTIONS.some((o) => o.value === v);
      const strategyOptions = [...BOOT_STRATEGY_OPTIONS];
      if (!known && v) strategyOptions.push({ value: v, label: `${v} (current)` });
      const strategyValue = v || BOOT_STRATEGY_OPTIONS[0].value;
      return (
        <div key={key} className="space-y-1.5">
          <Label htmlFor={key} className="text-[11px] uppercase tracking-wider text-muted-foreground">
            {label}
          </Label>
          <SettingsSelect
            id={key}
            value={strategyValue}
            onChange={(next) => updateField(key, next)}
            options={strategyOptions}
          />
          <p className="text-[10px] text-muted-foreground">
            Boot default stored in settings. Hot-swap the <em>active</em> strategy from the Control panel without restarting.
          </p>
        </div>
      );
    }

    if (key === "log_level") {
      const v = String(val ?? "").toLowerCase();
      const logLevelOptions = [
        { value: "debug", label: "debug (verbose — WS, MD, reconciliation)" },
        { value: "info", label: "info (default)" },
        { value: "warning", label: "warning" },
        { value: "error", label: "error" },
      ];
      const known = logLevelOptions.some((o) => o.value === v);
      const options = known ? logLevelOptions : [{ value: v, label: `${v} (current)` }, ...logLevelOptions];
      return (
        <div key={key} className="space-y-1.5">
          <Label htmlFor={key} className="text-[11px] uppercase tracking-wider text-muted-foreground">
            {label}
          </Label>
          <SettingsSelect
            id={key}
            value={v || "info"}
            onChange={(next) => updateField(key, next)}
            options={options}
          />
          <p className="text-[10px] text-muted-foreground">
            Applies immediately to the running engine. Debug lines appear in the terminal,{" "}
            <code className="font-mono">app.log</code>, and LIVE LOG (tagged DBG).
          </p>
        </div>
      );
    }

    if (key === "mm_symbol_half_spread_bps" || key === "mm_symbol_quote_overrides") {
      const text =
        typeof val === "object" && val !== null
          ? JSON.stringify(val, null, 0)
          : String(val ?? "");
      return (
        <div key={key} className="space-y-1.5">
          <Label htmlFor={key} className="text-[11px] uppercase tracking-wider text-muted-foreground">
            {label}
          </Label>
          <Input
            id={key}
            value={text}
            onChange={(e) => {
              const raw = e.target.value.trim();
              if (!raw) {
                updateField(key, key === "mm_symbol_half_spread_bps" ? {} : {});
                return;
              }
              try {
                updateField(key, JSON.parse(raw) as Record<string, unknown>);
              } catch {
                updateField(key, raw);
              }
            }}
            className="font-mono text-xs"
          />
          <p className="text-[10px] text-muted-foreground">
            {key === "mm_symbol_half_spread_bps"
              ? 'Per-symbol half-spread bps, e.g. {"BTCUSDT":2,"DOGEUSDT":12} or BTCUSDT:2,DOGEUSDT:12'
              : "Per-symbol overrides: half_spread_bps, min_spread_bps, reservation_inventory_bps, …"}
          </p>
        </div>
      );
    }

    const isSecret = key === "binance_api_key" || key === "binance_api_secret";
    const inputType =
      typeof val === "number" ? "number" : isSecret ? "password" : "text";

    return (
      <div key={key} className="space-y-1.5">
        <Label htmlFor={key} className="text-[11px] uppercase tracking-wider text-muted-foreground">
          {label}
        </Label>
        <Input
          id={key}
          type={inputType}
          value={
            val === null || val === undefined ? "" : typeof val === "number" ? String(val) : String(val)
          }
          onChange={(e) => {
            const raw = e.target.value;
            if (typeof baseline?.[key] === "number") {
              const n = parseFloat(raw);
              updateField(key, Number.isFinite(n) ? n : raw);
            } else {
              updateField(key, raw);
            }
          }}
          className="font-mono text-xs"
          autoComplete={isSecret ? "off" : undefined}
        />
      </div>
    );
  };

  const SectionTitle = ({ children }: { children: ReactNode }) => (
    <h3 className="border-b border-border pb-1 pt-2 text-[10px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">
      {children}
    </h3>
  );

  const StrategyTabFields = () => {
    const common = ["strategy", "symbols", "base_currency"].filter((k) => strategyKeys.includes(k));
    const pair = strategyKeys.filter((k) => k.startsWith("pair_"));
    const sma = strategyKeys.filter((k) => k.startsWith("sma_"));
    const mmAll = strategyKeys.filter(
      (k) => k.startsWith("mm_") && !k.startsWith("mm2_"),
    );
    const mmInstPrefixes = [
      "mm_institutional",
      "mm_quote",
      "mm_urgent",
      "mm_tape_pressure",
      "mm_max_inventory",
      "mm_inventory",
      "mm_reservation",
      "mm_symbol",
      "mm_quote_use_venue",
      "mm_quote_venue",
      "mm_jump",
      "mm_max_adverse",
      "mm_markout",
      "mm_scratch",
      "mm_min_exit",
      "mm_max_hold",
      "mm_catastrophe",
      "mm_depletion",
      "mm_large_trade",
      "mm_toxicity",
    ];
    const mmInst = mmAll.filter((k) =>
      mmInstPrefixes.some((p) => k === p || k.startsWith(`${p}_`)),
    );
    const mmLegacy = mmAll.filter((k) => !mmInst.includes(k));
    const mm2 = strategyKeys.filter((k) => k.startsWith("mm2_"));
    const blend = strategyKeys.filter((k) => k.startsWith("blend_"));
    const flow = strategyKeys.filter((k) => k.startsWith("flow_"));

    return (
      <div className="space-y-4">
        {activeStrategyLabel ? (
          <p className="rounded-sm border border-border/80 bg-muted/30 px-3 py-2 text-[11px] text-muted-foreground">
            Currently running: <span className="font-medium text-foreground">{activeStrategyLabel}</span>
          </p>
        ) : null}

        {strategyKeys.length === 0 ? (
          <p className="text-sm text-muted-foreground">No strategy parameters returned.</p>
        ) : null}

        {common.length > 0 && (
          <div className="space-y-3">
            <SectionTitle>Common</SectionTitle>
            {common.map((k) => renderField(k))}
          </div>
        )}

        {pair.length > 0 && (
          <div className="space-y-3">
            <SectionTitle>Pairs trading (basis z-score)</SectionTitle>
            {pair.map((k) => renderField(k))}
          </div>
        )}

        {sma.length > 0 && (
          <div className="space-y-3">
            <SectionTitle>SMA crossover</SectionTitle>
            {sma.map((k) => renderField(k))}
          </div>
        )}

        {mmInst.length > 0 && (
          <div className="space-y-3">
            <SectionTitle>Market making — institutional (quotes, inventory, toxicity)</SectionTitle>
            {mmInst.map((k) => renderField(k))}
          </div>
        )}

        {mmLegacy.length > 0 && (
          <div className="space-y-3">
            <SectionTitle>Market making — skew, tape, sizing</SectionTitle>
            {mmLegacy.map((k) => renderField(k))}
          </div>
        )}

        {mm2.length > 0 && (
          <div className="space-y-3">
            <SectionTitle>Market making 2.0 (fee-aware fade)</SectionTitle>
            {mm2.map((k) => renderField(k))}
          </div>
        )}

        {blend.length > 0 && (
          <div className="space-y-3">
            <SectionTitle>Blended signals (EMA / MACD / RSI / BB)</SectionTitle>
            {blend.map((k) => renderField(k))}
          </div>
        )}

        {flow.length > 0 && (
          <div className="space-y-3">
            <SectionTitle>Flow momentum (tape follow)</SectionTitle>
            {flow.map((k) => renderField(k))}
          </div>
        )}
      </div>
    );
  };

  const handleSave = async () => {
    if (!baseline) return;
    setSaving(true);
    setError(null);
    const patch: Record<string, unknown> = {};
    for (const key of Object.keys(draft)) {
      const next = coerceForPatch(key, draft[key], baseline[key]);
      if (next === undefined && (key === "binance_api_key" || key === "binance_api_secret")) {
        continue;
      }
      const prev = baseline[key];
      const comparableNext = next ?? draft[key];
      if (JSON.stringify(comparableNext) !== JSON.stringify(prev)) {
        patch[key] = comparableNext;
      }
    }
    try {
      const res = await api.patchSettings(patch);
      setBaseline(res.settings);
      setDraft({ ...res.settings });
      notifySuccess("Engine settings saved");
      onSaved?.();
      onOpenChange(false);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Save failed";
      setError(msg);
      notifyError(e, msg);
    } finally {
      setSaving(false);
    }
  };

  const fieldsScroll = (keys: string[]) => (
    <div className="space-y-4 py-2 pr-3">{keys.map((k) => renderField(k))}</div>
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="flex max-h-[90vh] max-w-lg flex-col gap-0 p-0 sm:max-w-2xl"
        onInteractOutside={(event) => {
          if (isRadixSelectPortalTarget(event.target)) event.preventDefault();
        }}
      >
        <DialogHeader className="border-b border-border px-6 py-4">
          <DialogTitle>Engine settings</DialogTitle>
          <DialogDescription className="text-xs leading-relaxed">
            Strategy parameters live here too (tab <strong>Strategy</strong>): pairs, SMA, and market-making knobs map to{" "}
            <code className="text-foreground">PATCH /api/settings</code> and apply immediately via{" "}
            <code className="text-foreground">refresh_settings</code>. Secrets left as{" "}
            <code className="text-foreground">***</code> are unchanged. <code className="text-foreground">api_host</code> /{" "}
            <code className="text-foreground">api_port</code> need a backend restart.
          </DialogDescription>
        </DialogHeader>

        <Tabs value={activeTab} onValueChange={setActiveTab} className="flex min-h-0 flex-1 flex-col px-6">
          <TabsList className="mb-1 h-auto w-full flex-wrap justify-start gap-1 bg-muted/50 p-1">
            <TabsTrigger value="strategy" className="text-xs">
              Strategy
            </TabsTrigger>
            <TabsTrigger value="risk" className="text-xs">
              Risk &amp; execution
            </TabsTrigger>
            <TabsTrigger value="system" className="text-xs">
              System &amp; API
            </TabsTrigger>
            <TabsTrigger value="all" className="text-xs">
              All
            </TabsTrigger>
          </TabsList>

          <div className="scrollbar-themed min-h-[min(50vh,28rem)] max-h-[min(50vh,28rem)] overflow-y-auto overflow-x-hidden pr-1">
            {loading && <p className="py-6 text-sm text-muted-foreground">Loading…</p>}
            {error && (
              <p className="rounded-sm border border-bear/40 bg-bear/10 px-3 py-2 text-xs text-bear">{error}</p>
            )}
            {!loading && (
              <>
                <TabsContent value="strategy" className="mt-0">
                  <StrategyTabFields />
                </TabsContent>
                <TabsContent value="risk" className="mt-0">
                  <p className="mb-3 text-[11px] leading-relaxed text-muted-foreground">
                    Per-circuit-breaker toggles and presets live on the dashboard{" "}
                    <strong>Circuit breakers</strong> panel. Quick limit knobs (daily loss %, cooldowns,
                    WS stale) and ops toggles are under <strong>CONTROL → Quick limits</strong> on the
                    main page.
                  </p>
                  {riskKeys.length === 0 ? (
                    <p className="text-sm text-muted-foreground">No keys in this group.</p>
                  ) : (
                    fieldsScroll(riskKeys)
                  )}
                </TabsContent>
                <TabsContent value="system" className="mt-0">
                  {systemKeys.length === 0 ? (
                    <p className="text-sm text-muted-foreground">No keys in this group.</p>
                  ) : (
                    fieldsScroll(systemKeys)
                  )}
                </TabsContent>
                <TabsContent value="all" className="mt-0">
                  {fieldsScroll(allKeys.filter((k) => k !== "breaker_enabled"))}
                </TabsContent>
              </>
            )}
          </div>
        </Tabs>

        <DialogFooter className="gap-2 border-t border-border px-6 py-4">
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="button" onClick={() => void handleSave()} disabled={saving || loading || !baseline}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
