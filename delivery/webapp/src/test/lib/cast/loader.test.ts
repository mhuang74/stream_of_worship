import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { loadCastSdk, isCastSdkSupported } from "@/lib/cast/loader";

// The loader uses module-level singletons (injected flag, settled state,
// pending/cancelled sets, nextRequestId counter). To get a clean slate
// between tests we reset the DOM, restore the window globals, and re-import
// the module fresh via vi.resetModules + dynamic import.

async function freshLoader() {
  vi.resetModules();
  // Re-import after reset so module-level `injected`/`settled` reset.
  const mod = await import("@/lib/cast/loader");
  return mod;
}

function resetWindow() {
  // Wipe any script tags + SDK globals between tests.
  document.head.innerHTML = "";
  delete (window as unknown as Record<string, unknown>).__onGCastApiAvailable;
  delete (window as unknown as { chrome?: unknown }).chrome;
  delete (window as unknown as { cast?: unknown }).cast;
}

describe("loadCastSdk", () => {
  beforeEach(() => {
    resetWindow();
  });
  afterEach(() => {
    resetWindow();
  });

  it("injects the cast_sender.js script exactly once across multiple callers", async () => {
    const { loadCastSdk } = await freshLoader();
    const p1 = loadCastSdk();
    const p2 = loadCastSdk();
    const p3 = loadCastSdk();
    const scripts = Array.from(document.head.querySelectorAll("script")).map((s) => s.src);
    expect(scripts).toEqual([
      "https://www.gstatic.com/cv/js/sender/v1/cast_sender.js",
    ]);
    // Fire the global callback so the promises settle (no unhandled rejection).
    window.__onGCastApiAvailable?.(true);
    await Promise.all([p1, p2, p3]);
  });

  it("resolves all in-flight callers when __onGCastApiAvailable(true)", async () => {
    const { loadCastSdk } = await freshLoader();
    const p1 = loadCastSdk();
    const p2 = loadCastSdk();
    window.__onGCastApiAvailable?.(true);
    await expect(p1).resolves.toBeUndefined();
    await expect(p2).resolves.toBeUndefined();
  });

  it("rejects in-flight callers when __onGCastApiAvailable(false)", async () => {
    const { loadCastSdk } = await freshLoader();
    const p = loadCastSdk();
    window.__onGCastApiAvailable?.(false);
    await expect(p).rejects.toThrowError(/Cast SDK failed to load/);
  });

  it("is SSR-safe: resolves without touching window when window is undefined", async () => {
    const originalWindow = globalThis.window;
    // Drop window entirely for this test.
    // @ts-expect-error intentional for SSR simulation
    delete globalThis.window;
    try {
      const { loadCastSdk } = await freshLoader();
      // Must resolve without referencing window (no throw, no script injection).
      await expect(loadCastSdk()).resolves.toBeUndefined();
    } finally {
      globalThis.window = originalWindow;
    }
  });

  it("returns false from isCastSdkSupported when chrome.cast / cast.framework are absent", async () => {
    const { isCastSdkSupported } = await freshLoader();
    expect(isCastSdkSupported()).toBe(false);
  });

  it("returns true from isCastSdkSupported when both globals are present", async () => {
    const { isCastSdkSupported } = await freshLoader();
    (window as unknown as { chrome: unknown }).chrome = { cast: {} };
    (window as unknown as { cast: unknown }).cast = { framework: {} };
    expect(isCastSdkSupported()).toBe(true);
  });

  it("does not consult navigator.presentation as a substitute for Cast support", async () => {
    const { isCastSdkSupported } = await freshLoader();
    // navigator.presentation exists in jsdom but Cast globals do not → false.
    expect(isCastSdkSupported()).toBe(false);
  });

  it("resolves silently when abort fires before the global callback (no state update scheduled)", async () => {
    const { loadCastSdk } = await freshLoader();
    const controller = new AbortController();
    const p = loadCastSdk({ signal: controller.signal });
    // Abort before the SDK global callback fires — loader must resolve silently.
    controller.abort();
    await expect(p).resolves.toBeUndefined();
    // Now fire the global callback late. There must be no pending caller to
    // reject — i.e. no unhandled rejection ships for the aborted request.
    window.__onGCastApiAvailable?.(false);
    // Drain microtasks so a stray rejection (if any) would surface.
    await new Promise((r) => setTimeout(r, 0));
  });

  it("resolves silently when signal is already aborted at call time", async () => {
    const { loadCastSdk } = await freshLoader();
    const controller = new AbortController();
    controller.abort();
    await expect(loadCastSdk({ signal: controller.signal })).resolves.toBeUndefined();
    // No script tag is injected and no global callback is bound: an
    // already-aborted caller short-circuits before any side effect.
    expect(document.head.querySelectorAll("script").length).toBe(0);
    expect(window.__onGCastApiAvailable).toBeUndefined();
  });

  it("short-circuits to resolved when SDK already loaded and a new caller arrives later", async () => {
    const { loadCastSdk } = await freshLoader();
    const first = loadCastSdk();
    window.__onGCastApiAvailable?.(true);
    await first;
    // A later caller must resolve immediately without re-injecting.
    const before = document.head.querySelectorAll("script").length;
    await expect(loadCastSdk()).resolves.toBeUndefined();
    const after = document.head.querySelectorAll("script").length;
    expect(after).toBe(before);
  });
});
