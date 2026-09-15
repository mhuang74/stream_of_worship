const SW_URL = "/sw.js";

export type ServiceWorkerStatus =
  | "unsupported"
  | "registering"
  | "registered"
  | "activated"
  | "error";

export interface ServiceWorkerRegistrationResult {
  success: boolean;
  error?: string;
}

/**
 * Registers /sw.js at app boot (issue #204). Plain navigator.serviceWorker
 * registration — the SW handles its own activation (workbox.core.skipWaiting
 * + clientsClaim in sw.js), so the Workbox window wrapper's
 * waiting→messageSkipWaiting dance has nothing to talk to.
 */
export async function registerServiceWorker(): Promise<ServiceWorkerRegistrationResult> {
  if (typeof window === "undefined" || !("serviceWorker" in navigator)) {
    return { success: false, error: "Service workers not supported" };
  }

  try {
    await navigator.serviceWorker.register(SW_URL);
    return { success: true };
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return { success: false, error: message };
  }
}

export async function unregisterServiceWorker(): Promise<boolean> {
  if (typeof window === "undefined" || !("serviceWorker" in navigator)) {
    return false;
  }

  try {
    const registrations = await navigator.serviceWorker.getRegistrations();
    await Promise.all(registrations.map((r) => r.unregister()));
    return true;
  } catch {
    return false;
  }
}