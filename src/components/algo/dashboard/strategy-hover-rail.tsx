import { useCallback, useState } from "react";
import { ChevronLeft } from "lucide-react";

import { ScrollArea } from "@/components/ui/scroll-area";
import { StrategyPicker } from "@/components/algo/dashboard/control-panels";
import { cn } from "@/lib/utils";
import type { StrategyInfo } from "@/components/algo/types";

const ALL_STRATEGIES_LABEL = "All strategies (netted)";

function resolveActiveLabel(
  strategies: StrategyInfo[],
  activeName: string | null,
): string {
  if (activeName === "all") return ALL_STRATEGIES_LABEL;
  const match = strategies.find((s) => s.name === activeName);
  return match?.label ?? (activeName ? activeName : "No strategy");
}

function railAbbrev(label: string): string {
  if (label.length <= 14) return label;
  return `${label.slice(0, 12)}\u2026`;
}

export function StrategyHoverRail({
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
  const [hovered, setHovered] = useState(false);
  const [pinned, setPinned] = useState(false);
  const open = hovered || pinned;
  const activeLabel = resolveActiveLabel(strategies, activeName);

  const onPick = useCallback(
    (name: string) => {
      onSelect(name);
      setPinned(false);
    },
    [onSelect],
  );

  return (
    <>
      {pinned ? (
        <button
          type="button"
          aria-label="Close strategy picker"
          className="fixed inset-0 z-20 bg-background/40 backdrop-blur-[1px] lg:hidden"
          onClick={() => setPinned(false)}
        />
      ) : null}

      <div
        className="fixed right-0 top-[calc(49px+4.5rem)] z-30 flex max-h-[min(70vh,520px)]"
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
      >
        <div
          className={cn(
            "flex w-[min(280px,calc(100vw-3rem))] flex-col overflow-hidden rounded-l-md border border-r-0 border-border bg-background/95 shadow-2xl backdrop-blur transition-[transform,opacity] duration-200 ease-out",
            open
              ? "translate-x-0 opacity-100"
              : "pointer-events-none translate-x-full opacity-0",
          )}
          aria-hidden={!open}
        >
          <div className="border-b border-border px-3 py-2.5">
            <div className="text-[11px] uppercase tracking-wider text-muted-foreground">
              Select strategy
            </div>
            <div className="truncate text-sm font-semibold">{activeLabel}</div>
            <p className="mt-1 text-[11px] text-muted-foreground">
              Hover the edge tab on desktop; tap it on mobile.
            </p>
          </div>
          <ScrollArea className="flex-1 p-3">
            <StrategyPicker
              strategies={strategies}
              activeName={activeName}
              multiMode={multiMode}
              backendReachable={backendReachable}
              onSelect={onPick}
              hideHeader
            />
          </ScrollArea>
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
          aria-label={`Strategy: ${activeLabel}. Hover or tap to change.`}
        >
          {!open ? (
            <span
              className="absolute -left-0.5 top-1/2 size-2 -translate-y-1/2 rounded-full bg-bull shadow-[0_0_8px_rgba(34,197,94,0.8)] animate-pulse"
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
            Strategy
          </span>
          <span
            className="max-h-28 truncate text-[10px] font-medium leading-tight text-bull [writing-mode:vertical-rl]"
            title={activeLabel}
          >
            {railAbbrev(activeLabel)}
          </span>
          <span className="text-[8px] uppercase tracking-wider text-muted-foreground/80 [writing-mode:vertical-rl]">
            Hover
          </span>
        </button>
      </div>
    </>
  );
}
