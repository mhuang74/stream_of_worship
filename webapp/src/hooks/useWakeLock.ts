"use client";

import { useState, useEffect, useCallback, useRef } from "react";

interface WakeLockState {
  isSupported: boolean;
  isActive: boolean;
  error: string | null;
}

export function useWakeLock() {
  const [state, setState] = useState<WakeLockState>({
    isSupported: false,
    isActive: false,
    error: null,
  });
  
  const wakeLockRef = useRef<WakeLockSentinel | null>(null);

  // Check if Wake Lock API is supported
  useEffect(() => {
    if (typeof navigator === "undefined") return;

    const isSupported = "wakeLock" in navigator &&
      (navigator as unknown as { wakeLock: unknown }).wakeLock != null;
    setState((prev) => ({ ...prev, isSupported }));
  }, []);

  // Request wake lock
  const request = useCallback(async () => {
    if (!state.isSupported || wakeLockRef.current) return;

    try {
      const wakeLock = await navigator.wakeLock.request("screen");
      wakeLockRef.current = wakeLock;
      
      setState((prev) => ({ ...prev, isActive: true, error: null }));

      // Handle wake lock release
      wakeLock.addEventListener("release", () => {
        wakeLockRef.current = null;
        setState((prev) => ({ ...prev, isActive: false }));
      });
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : "Failed to acquire wake lock";
      setState((prev) => ({ ...prev, error: errorMessage }));
    }
  }, [state.isSupported]);

  // Release wake lock
  const release = useCallback(() => {
    if (wakeLockRef.current) {
      wakeLockRef.current.release();
      wakeLockRef.current = null;
      setState((prev) => ({ ...prev, isActive: false }));
    }
  }, []);

  // Auto-request on mount if supported
  useEffect(() => {
    if (state.isSupported) {
      request();
    }

    return () => {
      release();
    };
  }, [state.isSupported, request, release]);

  // Re-acquire wake lock when visibility changes (if document was hidden)
  useEffect(() => {
    if (!state.isSupported) return;

    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible" && !wakeLockRef.current) {
        request();
      }
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);
    
    return () => {
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [state.isSupported, request]);

  return {
    ...state,
    request,
    release,
  };
}

// Type definition for WakeLockSentinel (not in standard TypeScript types yet)
interface WakeLockSentinel extends EventTarget {
  released: boolean;
  type: "screen";
  release(): Promise<void>;
}
