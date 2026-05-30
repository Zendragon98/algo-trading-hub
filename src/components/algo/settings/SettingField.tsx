"use client";

import { memo, useCallback, useEffect, useId, useState, type ReactNode } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";

import { BOOT_STRATEGY_OPTIONS, titleCaseKey } from "./utils";

const selectClass =
  "flex h-9 w-full rounded-md border border-border bg-secondary/80 px-3 font-mono text-sm text-foreground shadow-sm outline-none ring-offset-background focus-visible:ring-2 focus-visible:ring-ring";

const FIELD_HINTS: Record<string, string> = {
  strategy:
    "Boot default in settings. Hot-swap the active strategy from the Control panel without restarting.",
  log_level: "Applies immediately. Debug lines go to terminal, app.log, and LIVE LOG (DBG).",
  mm_symbol_half_spread_bps:
    'Per-symbol half-spread bps, e.g. {"BTCUSDT":2} or BTCUSDT:2,DOGEUSDT:12',
  mm_symbol_quote_overrides:
    "Per-symbol overrides: half_spread_bps, min_spread_bps, reservation_inventory_bps, …",
  trading_mode: "paper = simulated fills; live = real venue orders.",
};

function displayValue(val: unknown): string {
  if (val === null || val === undefined) return "";
  if (typeof val === "number") return String(val);
  if (Array.isArray(val)) return val.join(", ");
  if (typeof val === "object") return JSON.stringify(val);
  return String(val);
}

function NativeSelect(props: {
  id: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (v: string) => void;
}) {
  const { id, value, options, onChange } = props;
  return (
    <select
      id={id}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={selectClass}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

type SettingFieldProps = {
  settingKey: string;
  value: unknown;
  baselineValue: unknown;
  compact?: boolean;
  onChange: (key: string, value: unknown) => void;
};

export const SettingField = memo(function SettingField({
  settingKey,
  value,
  baselineValue,
  compact,
  onChange,
}: SettingFieldProps) {
  const id = useId();
  const inputId = `${id}-${settingKey}`;
  const label = titleCaseKey(settingKey);
  const hint = FIELD_HINTS[settingKey];

  const commit = useCallback(
    (next: unknown) => {
      onChange(settingKey, next);
    },
    [onChange, settingKey],
  );

  if (typeof value === "boolean") {
    return (
      <div
        className={cn(
          "flex items-center justify-between gap-3 rounded-md border border-border/70 bg-card/50 px-3 py-2.5",
          compact && "py-2",
        )}
      >
        <Label htmlFor={inputId} className="cursor-pointer text-sm font-medium leading-snug">
          {label}
        </Label>
        <Switch id={inputId} checked={value} onCheckedChange={(c) => commit(c)} />
      </div>
    );
  }

  if (settingKey === "trading_mode") {
    const tm = String(value ?? "").toLowerCase();
    const known = tm === "live" || tm === "paper";
    const options: { value: string; label: string }[] = [];
    if (!known && tm) options.push({ value: tm, label: `${tm} (current)` });
    options.push({ value: "paper", label: "Paper" }, { value: "live", label: "Live" });
    return (
      <FieldShell inputId={inputId} label={label} hint={hint}>
        <NativeSelect
          id={inputId}
          value={tm || "paper"}
          onChange={(v) => commit(v)}
          options={options}
        />
      </FieldShell>
    );
  }

  if (settingKey === "strategy") {
    const v = String(value ?? "");
    const options = [...BOOT_STRATEGY_OPTIONS];
    if (!options.some((o) => o.value === v) && v) {
      options.push({ value: v, label: `${v} (current)` });
    }
    return (
      <FieldShell inputId={inputId} label={label} hint={hint}>
        <NativeSelect
          id={inputId}
          value={v || BOOT_STRATEGY_OPTIONS[0].value}
          onChange={(next) => commit(next)}
          options={options}
        />
      </FieldShell>
    );
  }

  if (settingKey === "log_level") {
    const v = String(value ?? "").toLowerCase();
    const logOptions = [
      { value: "debug", label: "Debug (verbose)" },
      { value: "info", label: "Info (default)" },
      { value: "warning", label: "Warning" },
      { value: "error", label: "Error" },
    ];
    const options = logOptions.some((o) => o.value === v)
      ? logOptions
      : [{ value: v, label: `${v} (current)` }, ...logOptions];
    return (
      <FieldShell inputId={inputId} label={label} hint={hint}>
        <NativeSelect id={inputId} value={v || "info"} onChange={(next) => commit(next)} options={options} />
      </FieldShell>
    );
  }

  if (Array.isArray(value)) {
    return (
      <TextSettingField
        inputId={inputId}
        label={label}
        hint={hint}
        value={displayValue(value)}
        inputType="text"
        onCommit={(raw) =>
          commit(
            raw
              .split(",")
              .map((s) => s.trim())
              .filter(Boolean),
          )
        }
      />
    );
  }

  if (settingKey === "mm_symbol_half_spread_bps" || settingKey === "mm_symbol_quote_overrides") {
    return (
      <JsonSettingField
        inputId={inputId}
        label={label}
        hint={hint}
        value={value}
        onCommit={commit}
      />
    );
  }

  const isSecret = settingKey === "binance_api_key" || settingKey === "binance_api_secret";
  const inputType = typeof value === "number" ? "number" : isSecret ? "password" : "text";

  return (
    <TextSettingField
      inputId={inputId}
      label={label}
      hint={hint}
      value={displayValue(value)}
      inputType={inputType}
      autoComplete={isSecret ? "off" : undefined}
      onCommit={(raw) => {
        if (typeof baselineValue === "number") {
          const n = parseFloat(raw);
          commit(Number.isFinite(n) ? n : raw);
        } else {
          commit(raw);
        }
      }}
    />
  );
});

function FieldShell(props: {
  inputId: string;
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={props.inputId} className="text-sm font-medium">
        {props.label}
      </Label>
      {props.children}
      {props.hint ? <p className="text-xs leading-relaxed text-muted-foreground">{props.hint}</p> : null}
    </div>
  );
}

const TextSettingField = memo(function TextSettingField(props: {
  inputId: string;
  label: string;
  hint?: string;
  value: string;
  inputType?: string;
  autoComplete?: string;
  onCommit: (raw: string) => void;
}) {
  const { inputId, label, hint, value, inputType = "text", autoComplete, onCommit } = props;
  const [local, setLocal] = useState(value);

  useEffect(() => {
    setLocal(value);
  }, [value]);

  return (
    <div className="space-y-1.5">
      <Label htmlFor={inputId} className="text-sm font-medium">
        {label}
      </Label>
      <Input
        id={inputId}
        type={inputType}
        value={local}
        onChange={(e) => setLocal(e.target.value)}
        onBlur={() => {
          if (local !== value) onCommit(local);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        }}
        className="h-9 font-mono text-sm"
        autoComplete={autoComplete}
      />
      {hint ? <p className="text-xs leading-relaxed text-muted-foreground">{hint}</p> : null}
    </div>
  );
});

const JsonSettingField = memo(function JsonSettingField(props: {
  inputId: string;
  label: string;
  hint?: string;
  value: unknown;
  onCommit: (v: unknown) => void;
}) {
  const text =
    typeof props.value === "object" && props.value !== null
      ? JSON.stringify(props.value, null, 0)
      : String(props.value ?? "");
  const [local, setLocal] = useState(text);

  useEffect(() => {
    setLocal(text);
  }, [text]);

  return (
    <div className="space-y-1.5 sm:col-span-2">
      <Label htmlFor={props.inputId} className="text-sm font-medium">
        {props.label}
      </Label>
      <Input
        id={props.inputId}
        value={local}
        onChange={(e) => setLocal(e.target.value)}
        onBlur={() => {
          const raw = local.trim();
          if (!raw) {
            props.onCommit({});
            return;
          }
          try {
            props.onCommit(JSON.parse(raw) as Record<string, unknown>);
          } catch {
            props.onCommit(raw);
          }
        }}
        className="h-9 font-mono text-sm"
      />
      {props.hint ? (
        <p className="text-xs leading-relaxed text-muted-foreground">{props.hint}</p>
      ) : null}
    </div>
  );
});
