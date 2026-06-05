import { useCallback, useState } from "react";
import { ChevronRight, ShieldAlert, SlidersHorizontal } from "lucide-react";

import { BreakersPanel, StrategyPicker } from "@/components/algo/dashboard/control-panels";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "@/lib/utils";
import type { BreakerList, StrategyInfo } from "@/components/algo/types";

const ALL_STRATEGIES_LABEL = "All strategies (netted)";

type ConfigPage = "strategy" | "breakers";

function resolveActiveLabel(
  strategies: StrategyInfo[],
  activeName: string | null,
): string {
  if (activeName === "all") return ALL_STRATEGIES_LABEL;
  const match = strategies.find((s) => s.name === activeName);
  return match?.label ?? (activeName ? activeName : "No strategy");
}

function railAbbrev(label: string): string {
  if (label.length <= 10) return label;
  return `${label.slice(0, 8)}\u2026`;
}

export function ConfigSidebar({
  strategies,
  activeName,
  multiMode,
  backendReachable,
  onSelectStrategy,
  breakers,
  paperMode,
  onPatchBreakerEnabled,
}: {
  strategies: StrategyInfo[];
  activeName: string | null;
  multiMode: boolean;
  backendReachable: boolean;
  onSelectStrategy: (name: string) => void;
  breakers: BreakerList;
  paperMode: boolean;
  onPatchBreakerEnabled: (
    patch: Record<string, boolean>,
    opts?: { confirmLiveDisable?: boolean; confirmToken?: string },
  ) => Promise<void>;
}) {
  const [page, setPage] = useState<ConfigPage>("strategy");
  const [hovered, setHovered] = useState(false);
  const [pinned, setPinned] = useState(false);
  const open = hovered || pinned;
  const activeLabel = resolveActiveLabel(strategies, activeName);
  const activeTripCount = breakers.active.length;

  const onPickStrategy = useCallback(
    (name: string) => {
      onSelectStrategy(name);
      setPinned(false);
    },
    [onSelectStrategy],
  );

  const selectPage = (next: ConfigPage) => {
    setPage(next);
    setPinned(true);
  };

  return (
    <>
      {pinned ? (
        <button
          type="button"
          aria-label="Close configuration sidebar"
          className="fixed inset-0 z-20 bg-background/40 backdrop-blur-[1px] lg:hidden"
          onClick={() => setPinned(false)}
        />
      ) : null}

      <div
        className="fixed left-0 top-[calc(49px+4.5rem)] z-30 flex max-h-[min(80vh,640px)]"
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
      >
        <nav
          className={cn(
            "flex w-12 shrink-0 flex-col items-stretch rounded-r-md border border-l-0 border-border bg-background/90 shadow-lg backdrop-blur",
            open && "border-bull/40",
          )}
          aria-label="Configuration sidebar"
        >
          <button
            type="button"
            onClick={() => setPinned((value) => !value)}
            className="flex flex-col items-center gap-1 border-b border-border px-1 py-2 text-muted-foreground hover:bg-bull/5 hover:text-foreground"
            aria-expanded={open}
            aria-label="Expand configuration sidebar"
          >
            {!open ? (
              <span
                className="size-2 rounded-full bg-bull shadow-[0_0_8px_rgba(34,197,94,0.8)] animate-pulse"
                aria-hidden
              />
            ) : null}
            <ChevronRight
              className={cn(
                "size-4 transition-transform duration-200",
                open && "rotate-180 text-bull",
              )}
            />
            <span className="text-[8px] uppercase tracking-wider">Open</span>
          </button>

          <button
            type="button"
            onClick={() => selectPage("strategy")}
            className={cn(
              "flex flex-1 flex-col items-center justify-center gap-1.5 border-b border-border px-1 py-3 transition-colors",
              page === "strategy" && open
                ? "bg-bull/10 text-bull"
                : "text-muted-foreground hover:bg-bull/5 hover:text-foreground",
            )}
            aria-current={page === "strategy" ? "page" : undefined}
          >
            <SlidersHorizontal className="size-4 shrink-0" />
            <span className="text-[9px] font-semibold uppercase tracking-[0.14em] [writing-mode:vertical-rl]">
              Strategy
            </span>
            <span
              className="max-h-20 truncate text-[9px] font-medium text-bull [writing-mode:vertical-rl]"
              title={activeLabel}
            >
              {railAbbrev(activeLabel)}
            </span>
          </button>

          <button
            type="button"
            onClick={() => selectPage("breakers")}
            className={cn(
              "relative flex flex-1 flex-col items-center justify-center gap-1.5 px-1 py-3 transition-colors",
              page === "breakers" && open
                ? "bg-bull/10 text-bull"
                : "text-muted-foreground hover:bg-bull/5 hover:text-foreground",
            )}
            aria-current={page === "breakers" ? "page" : undefined}
          >
            <ShieldAlert className="size-4 shrink-0" />
            <span className="text-[9px] font-semibold uppercase tracking-[0.14em] [writing-mode:vertical-rl]">
              Breakers
            </span>
            {activeTripCount > 0 ? (
              <span className="rounded-sm border border-bear/40 bg-bear/10 px-1 py-0.5 text-[8px] font-semibold tabular-nums text-bear [writing-mode:vertical-rl]">
                {activeTripCount} trip{activeTripCount === 1 ? "" : "s"}
              </span>
            ) : (
              <span className="text-[8px] uppercase tracking-wider text-muted-foreground/80 [writing-mode:vertical-rl]">
                Guards
              </span>
            )}
          </button>
        </nav>

        <div
          className={cn(
            "flex w-[min(320px,calc(100vw-4rem))] flex-col overflow-hidden rounded-r-md border border-l-0 border-border bg-background/95 shadow-2xl backdrop-blur transition-[transform,opacity] duration-200 ease-out",
            open
              ? "translate-x-0 opacity-100"
              : "pointer-events-none -translate-x-full opacity-0",
          )}
          aria-hidden={!open}
        >
          <div className="border-b border-border px-3 py-2.5">
            <ToggleGroup
              type="single"
              value={page}
              onValueChange={(value) => {
                if (value === "strategy" || value === "breakers") setPage(value);
              }}
              className="mb-2 grid w-full grid-cols-2 gap-1"
            >
              <ToggleGroupItem value="strategy" className="h-8 text-[10px] uppercase tracking-wider">
                Strategy
              </ToggleGroupItem>
              <ToggleGroupItem value="breakers" className="h-8 text-[10px] uppercase tracking-wider">
                Circuit breakers
              </ToggleGroupItem>
            </ToggleGroup>
            {page === "strategy" ? (
              <>
                <div className="truncate text-sm font-semibold">{activeLabel}</div>
                <p className="mt-1 text-[11px] text-muted-foreground">
                  Pick which strategy profile the engine runs.
                </p>
              </>
            ) : (
              <p className="text-[11px] text-muted-foreground">
                Enable or disable protection guards. Active trips stay in the top dashboard row.
              </p>
            )}
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto">
            {page === "strategy" ? (
              <div className="p-3">
                <StrategyPicker
                  strategies={strategies}
                  activeName={activeName}
                  multiMode={multiMode}
                  backendReachable={backendReachable}
                  onSelect={onPickStrategy}
                  hideHeader
                />
              </div>
            ) : (
              <BreakersPanel
                breakers={breakers}
                paperMode={paperMode}
                backendReachable={backendReachable}
                onPatchEnabled={onPatchBreakerEnabled}
                embedded
              />
            )}
          </div>
        </div>
      </div>
    </>
  );
}
