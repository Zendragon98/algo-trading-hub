"use client";

import { useEffect, useState } from "react";

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
import { LIVE_DISABLE_CONFIRM_TOKEN } from "@/lib/breaker-presets";

type Props = {
  open: boolean;
  codes: string[];
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
};

export function BreakerLiveConfirmDialog({ open, codes, onOpenChange, onConfirm }: Props) {
  const [token, setToken] = useState("");

  useEffect(() => {
    if (!open) setToken("");
  }, [open]);

  const ok = token.trim() === LIVE_DISABLE_CONFIRM_TOKEN;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md border-border bg-background">
        <DialogHeader>
          <DialogTitle>Disable major breakers in LIVE</DialogTitle>
          <DialogDescription>
            Turning off major kill switches removes automatic flatten on drawdown, daily loss, and
            similar conditions. You are responsible for manual risk control.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3 text-xs">
          <p className="text-muted-foreground">Affected codes:</p>
          <ul className="list-inside list-disc font-mono text-[11px] text-warning">
            {codes.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
          <div className="space-y-1.5">
            <Label htmlFor="live-breaker-token" className="text-[11px] uppercase tracking-wider">
              Type &quot;{LIVE_DISABLE_CONFIRM_TOKEN}&quot; to confirm
            </Label>
            <Input
              id="live-breaker-token"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              className="font-mono text-xs"
              autoComplete="off"
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="destructive" disabled={!ok} onClick={onConfirm}>
            Disable in LIVE
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
