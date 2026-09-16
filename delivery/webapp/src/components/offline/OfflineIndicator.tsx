"use client";

import { useSyncExternalStore } from "react";
import { WifiOff } from "lucide-react";
import { cn } from "@/lib/utils";
import { useLocale } from "@/hooks/useLocale";

export interface OfflineIndicatorProps {
  className?: string;
}

// Connectivity via useSyncExternalStore: the server snapshot renders the
// online state (null), so an offline cold start hydrates without a mismatch
// and the client snapshot upgrades to the banner in the same commit.
function subscribeConnectivity(onChange: () => void): () => void {
  window.addEventListener("online", onChange);
  window.addEventListener("offline", onChange);
  return () => {
    window.removeEventListener("online", onChange);
    window.removeEventListener("offline", onChange);
  };
}

export function OfflineIndicator({ className }: OfflineIndicatorProps) {
  const { t } = useLocale();
  const isOffline = useSyncExternalStore(
    subscribeConnectivity,
    () => typeof navigator !== "undefined" && navigator.onLine === false,
    () => false
  );

  if (!isOffline) return null;

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
