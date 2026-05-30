import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { ArrowLeft, Settings2 } from "lucide-react";
import { useEffect, useState } from "react";

import { SettingsEditor } from "@/components/algo/settings/SettingsEditor";
import { api } from "@/lib/api";

export const Route = createFileRoute("/settings")({
  component: SettingsPage,
});

function SettingsPage() {
  const navigate = useNavigate();
  const [activeStrategyLabel, setActiveStrategyLabel] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void api
      .state()
      .then((s) => {
        if (cancelled) return;
        const { strategy, strategies } = s;
        if (strategy?.name === "all" && strategies.length > 0) {
          setActiveStrategyLabel(`All (${strategies.map((x) => x.label).join(", ")})`);
        } else {
          setActiveStrategyLabel(strategy?.label ?? null);
        }
      })
      .catch(() => {
        if (!cancelled) setActiveStrategyLabel(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="flex h-screen flex-col bg-background text-foreground">
      <header className="sticky top-0 z-20 shrink-0 border-b border-border bg-background/90 backdrop-blur">
        <div className="mx-auto flex max-w-[1600px] items-center justify-between gap-4 px-4 py-3 lg:px-8">
          <div className="flex items-center gap-3">
            <Link
              to="/"
              className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
            >
              <ArrowLeft className="size-3" /> Live console
            </Link>
            <div className="flex items-center gap-2">
              <Settings2 className="size-4 text-bull" />
              <span className="text-sm font-semibold tracking-wide">Engine settings</span>
            </div>
          </div>
          <p className="hidden text-xs text-muted-foreground md:block">
            Runtime parameters · applies immediately on save
          </p>
        </div>
      </header>

      <main className="mx-auto flex min-h-0 w-full max-w-[1600px] flex-1 flex-col">
        <SettingsEditor
          activeStrategyLabel={activeStrategyLabel}
          onCancel={() => navigate({ to: "/" })}
        />
      </main>
    </div>
  );
}
