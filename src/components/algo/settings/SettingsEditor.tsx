"use client";

import { useCallback, useDeferredValue, useEffect, useMemo, useState } from "react";
import { Loader2, RotateCcw, Search } from "lucide-react";

import { SettingField } from "@/components/algo/settings/SettingField";
import {
  SETTINGS_SECTIONS,
  buildSettingsPatch,
  countDirtyKeys,
  keysForSection,
  matchesSearch,
  type SettingsSectionId,
} from "@/components/algo/settings/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, type SettingsDTO } from "@/lib/api";
import { notifyError, notifySuccess } from "@/lib/notify";
import { cn } from "@/lib/utils";

type Props = {
  activeStrategyLabel?: string | null;
  /** When provided (e.g. from live console hydrate), skip the initial GET /api/settings. */
  initialSettings?: SettingsDTO;
  onSaved?: () => void;
  onCancel?: () => void;
};

export function SettingsEditor({
  activeStrategyLabel,
  initialSettings,
  onSaved,
  onCancel,
}: Props) {
  const [baseline, setBaseline] = useState<SettingsDTO | null>(null);
  const [draft, setDraft] = useState<SettingsDTO>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeSection, setActiveSection] = useState<SettingsSectionId>("common");
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search);

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
    if (initialSettings && Object.keys(initialSettings).length > 0) {
      setBaseline(initialSettings);
      setDraft({ ...initialSettings });
      setLoading(false);
      setError(null);
      return;
    }
    void load();
  }, [load, initialSettings]);

  const allKeys = useMemo(() => Object.keys(draft).sort(), [draft]);

  const sectionsWithCounts = useMemo(
    () =>
      SETTINGS_SECTIONS.map((s) => ({
        ...s,
        count: keysForSection(s.id, allKeys).length,
      })).filter((s) => s.count > 0),
    [allKeys],
  );

  const setField = useCallback((key: string, value: unknown) => {
    setDraft((d) => {
      if (Object.is(d[key], value)) return d;
      return { ...d, [key]: value };
    });
  }, []);

  const dirtyCount = useMemo(() => countDirtyKeys(draft, baseline), [draft, baseline]);

  const visibleKeys = useMemo(() => {
    const q = deferredSearch.trim();
    if (q) {
      return allKeys.filter((k) => k !== "breaker_enabled" && matchesSearch(k, q));
    }
    return keysForSection(activeSection, allKeys);
  }, [allKeys, activeSection, deferredSearch]);

  const activeMeta = SETTINGS_SECTIONS.find((s) => s.id === activeSection);
  const searching = deferredSearch.trim().length > 0;

  const handleReset = () => {
    if (baseline) setDraft({ ...baseline });
  };

  const handleSave = async () => {
    if (!baseline) return;
    const patch = buildSettingsPatch(draft, baseline);
    if (Object.keys(patch).length === 0) {
      onCancel?.();
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const res = await api.patchSettings(patch);
      setBaseline(res.settings);
      setDraft({ ...res.settings });
      notifySuccess("Engine settings saved");
      onSaved?.();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Save failed";
      setError(msg);
      notifyError(e, msg);
    } finally {
      setSaving(false);
    }
  };

  const useGrid =
    !searching && (activeSection === "risk" || activeSection === "system") && activeMeta?.grid;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0 space-y-3 border-b border-border bg-background px-4 py-4 lg:px-8">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <p className="max-w-2xl text-sm leading-relaxed text-muted-foreground">
            Edit runtime parameters. Changes apply on save via{" "}
            <span className="font-mono text-foreground">PATCH /api/settings</span>. Secrets left
            as <span className="font-mono">***</span> are unchanged.{" "}
            <span className="font-mono">api_host</span> /{" "}
            <span className="font-mono">api_port</span> need a backend restart.
          </p>
          {dirtyCount > 0 ? (
            <span className="rounded-full border border-warning/40 bg-warning/10 px-2.5 py-0.5 text-xs font-medium text-warning">
              {dirtyCount} unsaved
            </span>
          ) : null}
        </div>

        {activeStrategyLabel ? (
          <p className="rounded-md border border-border/80 bg-muted/40 px-3 py-2 text-sm text-muted-foreground">
            Running:{" "}
            <span className="font-medium text-foreground">{activeStrategyLabel}</span>
          </p>
        ) : null}

        <div className="relative max-w-md">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search settings by name…"
            className="h-10 pl-9 text-sm"
          />
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        {!searching ? (
          <nav
            className="hidden w-52 shrink-0 overflow-y-auto border-r border-border bg-muted/20 lg:block xl:w-60"
            aria-label="Settings sections"
          >
            <ul className="space-y-0.5 p-3">
              {sectionsWithCounts.map((s) => (
                <li key={s.id}>
                  <button
                    type="button"
                    onClick={() => setActiveSection(s.id)}
                    className={cn(
                      "flex w-full items-center justify-between gap-2 rounded-md px-3 py-2.5 text-left text-sm transition-colors",
                      activeSection === s.id
                        ? "bg-background font-medium text-foreground shadow-sm"
                        : "text-muted-foreground hover:bg-background/60 hover:text-foreground",
                    )}
                  >
                    <span className="truncate">{s.label}</span>
                    <span className="shrink-0 tabular-nums text-[10px] text-muted-foreground">
                      {s.count}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </nav>
        ) : null}

        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          {searching ? (
            <p className="shrink-0 border-b border-border/60 px-4 py-2 text-xs text-muted-foreground lg:px-8">
              {visibleKeys.length} match{visibleKeys.length === 1 ? "" : "es"} for &ldquo;
              {deferredSearch.trim()}&rdquo;
            </p>
          ) : (
            <div className="shrink-0 border-b border-border/60 px-4 py-2 lg:hidden">
              <select
                value={activeSection}
                onChange={(e) => setActiveSection(e.target.value as SettingsSectionId)}
                className="h-10 w-full rounded-md border border-border bg-secondary/80 px-3 text-sm"
              >
                {sectionsWithCounts.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.label} ({s.count})
                  </option>
                ))}
              </select>
            </div>
          )}

          {!searching && activeMeta?.description ? (
            <p className="shrink-0 px-4 py-2 text-xs leading-relaxed text-muted-foreground lg:px-8">
              {activeMeta.description}
            </p>
          ) : null}

          <div className="min-h-0 flex-1 overflow-y-auto">
            <div className="px-4 py-4 pb-8 lg:px-8">
              {loading ? (
                <div className="flex items-center gap-2 py-16 text-sm text-muted-foreground">
                  <Loader2 className="size-4 animate-spin" />
                  Loading settings…
                </div>
              ) : null}

              {error ? (
                <p className="mb-4 rounded-md border border-bear/40 bg-bear/10 px-3 py-2 text-sm text-bear">
                  {error}
                </p>
              ) : null}

              {!loading && visibleKeys.length === 0 ? (
                <p className="py-12 text-sm text-muted-foreground">
                  {searching ? "No settings match your search." : "No fields in this section."}
                </p>
              ) : null}

              {!loading && visibleKeys.length > 0 ? (
                <div
                  className={cn(
                    "gap-4",
                    useGrid ? "grid sm:grid-cols-2 xl:grid-cols-3" : "flex max-w-3xl flex-col",
                  )}
                >
                  {visibleKeys.map((key) => (
                    <SettingField
                      key={key}
                      settingKey={key}
                      value={draft[key]}
                      baselineValue={baseline?.[key]}
                      onChange={setField}
                      compact={useGrid}
                    />
                  ))}
                </div>
              ) : null}
            </div>
          </div>
        </div>
      </div>

      <div className="sticky bottom-0 z-10 flex shrink-0 flex-wrap items-center justify-between gap-2 border-t border-border bg-background/95 px-4 py-3 backdrop-blur lg:px-8">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={!baseline || dirtyCount === 0 || saving}
          onClick={handleReset}
        >
          <RotateCcw className="size-4" />
          Reset changes
        </Button>
        <div className="flex gap-2">
          {onCancel ? (
            <Button type="button" variant="outline" onClick={onCancel}>
              Back to console
            </Button>
          ) : null}
          <Button
            type="button"
            onClick={() => void handleSave()}
            disabled={saving || loading || !baseline}
          >
            {saving ? (
              <>
                <Loader2 className="size-4 animate-spin" />
                Saving…
              </>
            ) : dirtyCount > 0 ? (
              `Save ${dirtyCount} change${dirtyCount === 1 ? "" : "s"}`
            ) : (
              "Save"
            )}
          </Button>
        </div>
      </div>
    </div>
  );
}
