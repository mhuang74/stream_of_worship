"use client";

import { WifiOff } from "lucide-react";
import { cn } from "@/lib/utils";
import { useLocale } from "@/hooks/useLocale";
import { useConnectivity } from "@/hooks/useConnectivity";

export interface OfflineIndicatorProps {
  className?: string;
}

// Connectivity comes from the shared state machine (src/hooks/
// useConnectivity.ts): navigator.onLine plus an active reachability probe
// against /api/health (issue #211). Fail toward offline: the banner shows
// unless the state is positively "online" — unknown (never probed, in-flight,
// or failed) counts as Offline. The server snapshot renders the online state
// ("online"), so an offline cold start hydrates without a mismatch and the
// client snapshot upgrades to the banner in the same commit.
export function OfflineIndicator({ className }: OfflineIndicatorProps) {
  const { t } = useLocale();
  const connectivity = useConnectivity();

  if (connectivity === "online") return null;

  return (
    <div
      role="status"
      aria-live="polite"
      aria-label={t("audio.offline.message")}
      className={cn(
        "fixed top-0 inset-x-0 z-50 flex items-center justify-center gap-2",
        "bg-destructive/90 text-destructive-foreground px-4 py-2 text-sm font-medium",
        className
      )}
    >
      <WifiOff className="size-4 shrink-0" aria-hidden="true" />
      <span>{t("audio.offline.message")}</span>
    </div>
  );
}
