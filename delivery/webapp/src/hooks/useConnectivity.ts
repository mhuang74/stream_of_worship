"use client";

import { useSyncExternalStore } from "react";

/**
 * Shared Connectivity state (issue #211).
 *
 * One module-level state machine decides the app's offline behavior for every
 * offline-aware screen (offline banner, controller boot, play navigation,
 * songsets list, songset editor, home page). It replaces the scattered
 * `navigator.onLine` reads: the OS reports interface state, not whether the
 * app's server is reachable — Airplane-style "interface up, no route"
 * conditions look online to `navigator.onLine`, so this module adds an
 * active reachability probe against the app's own unauthenticated health
 * endpoint (`/api/health`, 204).
 *
 * States:
 *   - "online"  — `navigator.onLine` is true AND the probe confirmed 204.
 *   - "offline" — `navigator.onLine` is false (OS is definitive downward).
 *   - "unknown" — onLine true but the probe has not positively confirmed
 *                 (never probed, in-flight, or failed: rejection, timeout,
 *                 non-204). Treated as Offline for all offline affordances
 *                 (fail toward offline): the app behaves offline-capable
 *                 unless positively confirmed online.
 *
 * Boot branches (controller page) additionally gate their cache-first branch
 * on the definitive `"offline"` only: an in-flight probe at boot means we do
 * not yet know, and the online chain's own failure fallback (the controller's
 * branch 2 / the play page's fetch-failure offline entry) covers the
 * genuinely-offline outcome without stealing a fresh online boot.
 *
 * Event-driven probes only — no timers, no polling (issue #211): the probe
 * fires when the module boots on the client, on the `online` event, on
 * `visibilitychange` (returning to the app), and on demand via
 * `probeConnectivity()` after an app-level fetch failure. A real connectivity
 * transition always produces one of these events.
 */

export type Connectivity = "online" | "offline" | "unknown";

/**
 * The reachability seam. Resolves true only when the app's server answered
 * its health endpoint with 204. Default implementation maps everything else —
 * rejection, timeout, non-204 — to false.
 */
export type ConnectivityProbe = () => Promise<boolean>;

const PROBE_TIMEOUT_MS = 5000;

async function defaultProbe(): Promise<boolean> {
  if (typeof fetch === "undefined") return false;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
  try {
    // `cache: "no-store"` and the SW's NetworkOnly carve-out (public/sw.js)
    // mean the answer always reflects a genuine round trip to the server.
    const response = await fetch("/api/health", {
      method: "HEAD",
      cache: "no-store",
      signal: controller.signal,
    });
    return response.status === 204;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

let probe: ConnectivityProbe = defaultProbe;
let lastProbeSucceeded = false;
let probeInFlight = false;
const listeners = new Set<() => void>();

function notify(): void {
  for (const listener of listeners) listener();
}

/**
 * Server snapshot: server components and the hydration pass cannot probe, so
 * they render the online copy; the client snapshot upgrades it the same way
 * the offline banner always has.
 */
export function getConnectivity(): Connectivity {
  if (typeof navigator === "undefined") return "online";
  if (!navigator.onLine) return "offline";
  return lastProbeSucceeded ? "online" : "unknown";
}

export function subscribeConnectivity(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useConnectivity(): Connectivity {
  return useSyncExternalStore(
    subscribeConnectivity,
    getConnectivity,
    () => "online"
  );
}

/**
 * Runs one reachability probe (deduping an in-flight one). Skipped while the
 * OS reports no interface: the probe cannot succeed there and `offline` is
 * already the state. Event handlers and post-fetch-failure recovery call this.
 */
export async function probeConnectivity(): Promise<void> {
  if (typeof navigator === "undefined" || !navigator.onLine || probeInFlight) {
    return;
  }
  probeInFlight = true;
  try {
    const succeeded = await probe();
    if (succeeded !== lastProbeSucceeded) {
      lastProbeSucceeded = succeeded;
      notify();
    }
  } finally {
    probeInFlight = false;
  }
}

/**
 * Test seam (per the issue's testing decision, the only new seam): inject a
 * probe implementation so probe outcomes (success, failure, timeout,
 * inconclusive) are testable at the hook without network. Passing null
 * restores the default and resets probe memory.
 */
export function setConnectivityProbe(replacement: ConnectivityProbe | null): void {
  probe = replacement ?? defaultProbe;
  lastProbeSucceeded = false;
  probeInFlight = false;
}

// Event triggers, registered once per client page load. The OS flips
// navigator.onLine atomically with the online/offline events; notify() makes
// subscribers re-read the derived state after the OS side changes.
if (typeof window !== "undefined") {
  void probeConnectivity(); // initial mount / app boot
  window.addEventListener("online", () => {
    // Any probe result on record predates the outage; it must not certify
    // Online (fail toward offline, issue #211 story 9). Drop it and let the
    // refired probe settle the state.
    lastProbeSucceeded = false;
    notify(); // offline → unknown until the refired probe confirms
    void probeConnectivity();
  });
  window.addEventListener("offline", () => {
    notify(); // online/unknown → offline, no probe (it could not succeed)
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") void probeConnectivity();
  });
}
