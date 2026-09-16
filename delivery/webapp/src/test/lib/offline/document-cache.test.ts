import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  SOW_PAGES_CACHE_NAME,
  WARM_TIMEOUT_MS,
  controllerDocumentPath,
  cacheControllerDocument,
} from "@/lib/offline/document-cache";

/**
 * Issue #206: the controller document (the target of the offline Start
 * Worship tap) is pre-cached into the service worker's sow-pages cache at
 * download time, together with the same-origin scripts/styles it references —
 * without those assets the offline navigation would serve an HTML shell whose
 * chunks the network can no longer provide.
 *
 * jsdom never fires load/error for preload links, so the tests below drive
 * every warming path through the module's own safety timeout.
 */

const HTML_WITH_ASSETS = `<!DOCTYPE html><html><head>
  <link rel="stylesheet" href="/_next/static/css/app/layout.css?v=1"/>
  <link rel="preload" as="font" href="/_next/static/media/geist.woff2"/>
  <link rel="preload" as="script" href="/_next/static/chunks/main-app.js"/>
  <script src="/_next/static/chunks/framework.js?v=2"></script>
  <script src="https://cdn.example.com/external.js"></script>
  <script src="data:text/javascript,inline"></script>
  </head><body><div id="__next"></div></body></html>`;

const ORIGIN = "https://app.example.com";

function memoryCache() {
  return {
    put: vi.fn(() => Promise.resolve()),
    match: vi.fn(() => Promise.resolve(undefined)),
    delete: vi.fn(() => Promise.resolve(true)),
    keys: vi.fn(() => Promise.resolve([])),
  };
}

function installCaches(cache = memoryCache()) {
  Object.defineProperty(window, "caches", {
    value: { open: vi.fn(() => Promise.resolve(cache)) },
    configurable: true,
  });
  return cache;
}

function stubLocation(pathname: string) {
  Object.defineProperty(window, "location", {
    value: new URL(`${ORIGIN}${pathname}`),
    configurable: true,
  });
}

/**
 * Runs cacheControllerDocument under fake timers, letting the preload
 * promises settle through their timeout instead of jsdom's missing
 * load/error events.
 */
async function warmDocument(songsetId = "set-1"): Promise<boolean> {
  vi.useFakeTimers();
  const pending = cacheControllerDocument(songsetId);
  await vi.advanceTimersByTimeAsync(WARM_TIMEOUT_MS);
  const result = await pending;
  vi.useRealTimers();
  return result;
}

beforeEach(() => {
  stubLocation("/songsets/set-1/play");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("controllerDocumentPath", () => {
  it("is the controller route the Start Worship tap navigates to", () => {
    expect(controllerDocumentPath("set-1")).toBe("/songsets/set-1/play/controller");
  });
});

describe("cacheControllerDocument", () => {
  it("fetches the controller document and stores it in the sow-pages cache", async () => {
    const cache = installCaches();
    global.fetch = vi
      .fn()
      .mockResolvedValue(new Response(HTML_WITH_ASSETS, { status: 200 }));

    const ok = await warmDocument();

    expect(ok).toBe(true);
    expect(global.fetch).toHaveBeenCalledWith("/songsets/set-1/play/controller");
    expect(window.caches.open).toHaveBeenCalledWith(SOW_PAGES_CACHE_NAME);
    expect(cache.put).toHaveBeenCalledWith("/songsets/set-1/play/controller", expect.any(Response));
  });

  it("preloads the same-origin scripts and styles the document references", async () => {
    installCaches();
    global.fetch = vi
      .fn()
      .mockResolvedValue(new Response(HTML_WITH_ASSETS, { status: 200 }));

    // The links are removed once warming settles, so observe them at
    // creation time.
    const appendSpy = vi.spyOn(document.head, "appendChild");
    await warmDocument();

    const preloaded = appendSpy.mock.calls
      .map(([node]) => node as HTMLLinkElement)
      .filter((el) => el instanceof HTMLLinkElement && el.rel === "preload")
      .map((el) => ({ href: el.getAttribute("href"), as: el.as }));
    expect(preloaded).toEqual(
      expect.arrayContaining([
        { href: `${ORIGIN}/_next/static/chunks/framework.js?v=2`, as: "script" },
        { href: `${ORIGIN}/_next/static/css/app/layout.css?v=1`, as: "style" },
        { href: `${ORIGIN}/_next/static/media/geist.woff2`, as: "font" },
        { href: `${ORIGIN}/_next/static/chunks/main-app.js`, as: "script" },
      ])
    );
    // Cross-origin and non-http resources are not the SW's to warm.
    expect(preloaded.find((p) => p.href?.includes("cdn.example.com"))).toBeUndefined();
    expect(preloaded.find((p) => p.href?.startsWith("data:"))).toBeUndefined();
  });

  it("removes the preload links once warming settles", async () => {
    installCaches();
    global.fetch = vi
      .fn()
      .mockResolvedValue(new Response(HTML_WITH_ASSETS, { status: 200 }));

    await warmDocument();

    expect(document.head.querySelectorAll("link[rel=preload]").length).toBe(0);
  });

  it("resolves true when a preload fails — warming is best-effort", async () => {
    installCaches();
    // The fake-timer flow IS the preload-failure path (jsdom fires no events),
    // so true must survive warmings that never completed cleanly.
    global.fetch = vi
      .fn()
      .mockResolvedValue(new Response(HTML_WITH_ASSETS, { status: 200 }));

    const ok = await warmDocument();

    expect(ok).toBe(true);
  });

  it("stores nothing when the document fetch fails", async () => {
    const cache = installCaches();
    global.fetch = vi.fn().mockResolvedValue(new Response("", { status: 404 }));

    const ok = await cacheControllerDocument("set-1");

    expect(ok).toBe(false);
    expect(cache.put).not.toHaveBeenCalled();
  });

  it("resolves false without throwing when Cache Storage is unavailable", async () => {
    Object.defineProperty(window, "caches", { value: undefined, configurable: true });

    const ok = await cacheControllerDocument("set-1");

    expect(ok).toBe(false);
  });

  it("resolves false without throwing when the cache write fails", async () => {
    installCaches({
      put: vi.fn(() => Promise.reject(new Error("quota"))),
      match: vi.fn(() => Promise.resolve(undefined)),
      delete: vi.fn(() => Promise.resolve(true)),
      keys: vi.fn(() => Promise.resolve([])),
    });
    global.fetch = vi
      .fn()
      .mockResolvedValue(new Response(HTML_WITH_ASSETS, { status: 200 }));

    const ok = await cacheControllerDocument("set-1");

    expect(ok).toBe(false);
  });
});
