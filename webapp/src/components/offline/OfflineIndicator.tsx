"use client";

import { useState, useEffect } from "react";
import { WifiOff } from "lucide-react";
import { cn } from "@/lib/utils";

export interface OfflineIndicatorProps {
  className?: string;
}

export function OfflineIndicator({ className }: OfflineIndicatorProps) {
  const [isOnline, setIsOnline] = useState(
    typeof navigator !== "undefined" ? navigator.onLine : true
  );

  useEffect(() => {
    const handleOnline = () => setIsOnline(true);
    const handleOffline = () => setIsOnline(false);

    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);

    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
    };
  }, []);

  if (isOnline) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      aria-label="You are offline"
      className={cn(
        "fixed top-0 inset-x-0 z-50 flex items-center justify-center gap-2",
        "bg-destructive/90 text-destructive-foreground px-4 py-2 text-sm font-medium",
        className
      )}
    >
      <WifiOff className="size-4 shrink-0" aria-hidden="true" />
      <span>You are offline</span>
    </div>
  );
}
