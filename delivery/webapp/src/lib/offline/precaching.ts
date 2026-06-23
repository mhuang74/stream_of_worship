import { Workbox } from "workbox-window";

let wb: Workbox | null = null;

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

export async function registerServiceWorker(
  swUrl = "/sw.js"
): Promise<ServiceWorkerRegistrationResult> {
  if (typeof window === "undefined" || !("serviceWorker" in navigator)) {
    return { success: false, error: "Service workers not supported" };
  }

  try {
    wb = new Workbox(swUrl);

    wb.addEventListener("waiting", () => {
      // New SW is waiting; skip waiting to activate immediately.
      wb?.messageSkipWaiting();
    });

    await wb.register();
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
    wb = null;
    return true;
  } catch {
    return false;
  }
}

export function getWorkboxInstance(): Workbox | null {
  return wb;
}
