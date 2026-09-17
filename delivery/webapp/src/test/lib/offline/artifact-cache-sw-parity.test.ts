// @vitest-environment node
// The service worker and the app write/read the same Cache Storage entries,
// but nothing in either module's own suite can see the other side: the app
// suite pins its literal, the SW suite hardcodes keys. These tests bind the
// two, so a cache-name or key-shape change on one side fails here instead of
// silently breaking offline playback (a miss looks identical to "not offline
// yet" until the network is actually gone).
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, it, expect, vi, afterEach } from "vitest";
import { ARTIFACT_CACHE_NAME, cacheArtifacts } from "@/lib/offline/artifact-cache";
import { SOW_PAGES_CACHE_NAME } from "@/lib/offline/document-cache";
import {
  artifactCacheKeyForUrl,
  artifactHandler,
} from "../../../../public/sw-artifact-serving.js";

const APP_ORIGIN = "https://app.example.com";
const SW_MODULE_PATH = path.resolve(__dirname, "../../../../public/sw-artifact-serving.js");
const SW_SCRIPT_PATH = path.resolve(__dirname, "../../../../public/sw.js");
// Marker comment above the dedicated controller-document route in public/sw.js.
const MATCHER_COMMENT = "// Unexpiring controller-document route (issue #210)";

function memoryCache() {
  const store = new Map<string, Response>();
  const puts: Array<{ key: string; response: Response }> = [];
  return {
    store,
    puts,
    match: vi.fn((key: string) => Promise.resolve(store.get(key))),
    put: vi.fn((key: string, value: Response) => {
      puts.push({ key, response: value });
      store.set(key, value.clone());
      return Promise.resolve();
    }),
    delete: vi.fn(() => Promise.resolve(true)),
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("service worker ↔ app artifact cache contract", () => {
  it("reads the cache name and keys the download path writes", async () => {
    const cache = memoryCache();
    const opened: string[] = [];
    // This file runs in the node environment (the SW module needs undici's
    // Blob semantics), so the browser globals the download path checks for are
    // stubbed explicitly.
    vi.stubGlobal("window", globalThis);
    vi.stubGlobal("caches", {
      open: vi.fn((name: string) => {
        opened.push(name);
        return Promise.resolve(cache);
      }),
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(new Response("artifact-bytes", { status: 200 })))
    );

    const proxyUrl = `${APP_ORIGIN}/api/r2/artifact/job-parity/output.mp4`;
    await cacheArtifacts("job-parity", { mp4Url: proxyUrl });

    expect(opened).toEqual([ARTIFACT_CACHE_NAME]);
    const writtenKey = cache.puts[0].key;
    // The SW must map the proxy URL it is asked for onto that exact key.
    expect(artifactCacheKeyForUrl(new URL(proxyUrl))).toBe(writtenKey);

    // …and serve it from that cache: a ranged request for the proxy URL is
    // answered as a 206 slice of what the download path stored.
    const swOpened: string[] = [];
    const swCache = {
      match: vi.fn((key: string) => Promise.resolve(cache.store.get(key))),
      put: vi.fn(() => Promise.resolve()),
    };
    const response = await artifactHandler({
      request: new Request(proxyUrl, { headers: { Range: "bytes=0-7" } }),
      caches: {
        open: vi.fn((name: string) => {
          swOpened.push(name);
          return Promise.resolve(swCache);
        }),
      },
      fetchFn: vi.fn(),
    });

    expect(swOpened).toEqual([ARTIFACT_CACHE_NAME]);
    expect(swCache.match).toHaveBeenCalledWith(writtenKey);
    expect(response.status).toBe(206);
    expect(await response.text()).toBe("artifact");
  });
});

describe("service worker artifact module version token", () => {
  // sw.js imports the module through a URL whose token is the module's content
  // hash. Without that, editing the module alone would never reach an installed
  // worker: a browser re-runs importScripts only when sw.js's own bytes change.
  it("pins the importScripts token to the module's content hash", () => {
    const digest = createHash("sha256").update(readFileSync(SW_MODULE_PATH)).digest("hex");
    const script = readFileSync(SW_SCRIPT_PATH, "utf8");
    const token = script.match(/importScripts\("\/sw-artifact-serving\.js\?v=([^"]+)"\)/)?.[1];

    expect(token, "sw.js must import the module with a ?v= token").toBeDefined();
    expect(
      token,
      `public/sw-artifact-serving.js changed — set its importScripts() token in public/sw.js to ${digest.slice(0, 12)}`
    ).toBe(digest.slice(0, 12));
  });
});

describe("service worker ↔ app document cache contract (issue #206)", () => {
  // The download path pre-caches the controller document into the SW's
  // navigation cache. If the two sides stop naming the same cache, the
  // offline Start Worship tap dead-ends at the SW's offline fallback page —
  // invisible until the network is actually gone.
  it("pre-caches documents into the cache the SW document route reads", () => {
    const script = readFileSync(SW_SCRIPT_PATH, "utf8");

    expect(
      script.includes(`cacheName: "${SOW_PAGES_CACHE_NAME}"`),
      `sw.js's document route must use the app's cache name (${SOW_PAGES_CACHE_NAME}) — see src/lib/offline/document-cache.ts and the NetworkFirst route in public/sw.js`
    ).toBe(true);
  });
});

describe("service worker ↔ app controller-document route contract (issue #210)", () => {
  // The pre-cached controller document must outlive the generic document
  // route's 7-day/50-entry expiration: the offline tap path depends on an
  // entry that a weeks-later cold start can still hit. Workbox's expiration
  // plugin has no per-entry exemption, so sw.js registers a dedicated route
  // for controller-document navigations BEFORE the generic document route,
  // with the same redirect-drop guard but no expiration plugin.
  it("registers a dedicated controller-document route before the generic document route", () => {
    const script = readFileSync(SW_SCRIPT_PATH, "utf8");

    const dedicatedIndex = script.indexOf(MATCHER_COMMENT);
    expect(
      dedicatedIndex,
      "sw.js must register the dedicated controller-document route (marked by a comment naming issue #210)"
    ).toBeGreaterThan(-1);

    const genericIndex = script.indexOf("request.mode === \"navigate\"");
    expect(genericIndex, "sw.js must keep the generic document route").toBeGreaterThan(-1);
    expect(
      dedicatedIndex,
      "the dedicated controller-document route must be registered BEFORE the generic document route (workbox matches in registration order)"
    ).toBeLessThan(genericIndex);
  });

  it("runs the dedicated route on the controller path with no expiration plugin", () => {
    const script = readFileSync(SW_SCRIPT_PATH, "utf8");

    // Isolate the dedicated route's registration: from its marker comment to
    // the generic document route's comment block.
    const dedicatedBlock = script.slice(
      script.indexOf(MATCHER_COMMENT),
      script.indexOf("// Offline navigation (issue #206)")
    );

    // Same path shape the app derives (controllerDocumentPath).
    expect(dedicatedBlock).toContain('/^\\/songsets\\/[^/]+\\/play\\/controller$/');
    // Navigations only: RSC payload fetches share the controller URL and must
    // stay on the generic route's bounded expiration (unexpiring RSC growth
    // would never be evicted).
    expect(dedicatedBlock).toContain('request.mode === "navigate"');
    // Same cache the pre-cache writes and the generic route reads.
    expect(dedicatedBlock).toContain(`cacheName: "${SOW_PAGES_CACHE_NAME}"`);
    // Redirect-drop guard in lockstep with the pre-cache (issue #210 parity).
    expect(dedicatedBlock).toContain("cacheWillUpdate");
    expect(dedicatedBlock).toContain("response.redirected");
    // The whole point: no expiry on this route.
    expect(
      dedicatedBlock.includes("ExpirationPlugin"),
      "the dedicated controller-document route must NOT expire entries (7-day TTL would evict the pre-cached tap path)"
    ).toBe(false);
    expect(dedicatedBlock).toContain("CacheableResponsePlugin");
  });

  it("keeps the generic document route's expiration intact", () => {
    const script = readFileSync(SW_SCRIPT_PATH, "utf8");
    const genericBlock = script.slice(script.indexOf("// Offline navigation (issue #206)"));

    expect(genericBlock).toContain("request.mode === \"navigate\"");
    expect(genericBlock).toContain("ExpirationPlugin");
    // RSC-payload growth stays bounded on the generic route.
    expect(genericBlock).toContain("maxEntries: 50");
  });
});
