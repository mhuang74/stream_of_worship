"use client";

import { useEffect } from "react";
import { registerServiceWorker } from "@/lib/offline/precaching";

/**
 * Registers the service worker at app boot (issue #204). Renders nothing:
 * the SW itself is the feature — it serves offline artifacts with Range
 * support and claims already-open clients on activate (skipWaiting +
 * clientsClaim in sw.js), so the booting document becomes SW-controlled
 * without a reload.
 */
export function ServiceWorkerRegistrar() {
  useEffect(() => {
    void registerServiceWorker();
  }, []);

  return null;
}