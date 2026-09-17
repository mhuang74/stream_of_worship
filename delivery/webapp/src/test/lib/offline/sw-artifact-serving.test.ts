// @vitest-environment node
// The SW runtime serves Blob slices through the web-standard Response — Node's
// undici implementation matches it; jsdom's stringifies Blob bodies.
import { describe, it, expect, vi } from "vitest";
import {
  artifactHandler,
  artifactCacheKeyForUrl,
  parseRangeHeader,
  rangeResponseFrom,
} from "../../../../public/sw-artifact-serving.js";

/**
 * Issue #204: the exact code the service worker runs (sw.js importScripts
 * public/sw-artifact-serving.js), tested in jsdom/Node where Response/Blob
 * behave like the SW runtime. Covers the contract the acceptance criteria
 * name: mapped-key cache hits serve 206 slices, misses with Range fetch
 * full-then-store (200 only — never 206), misses without Range self-warm,
 * ?download=1 passes through, and the API routes stay untouched.
 */

function makeCacheMock() {
  const store = new Map<string, Response>();
  const puts: Array<{ key: string; response: Response }> = [];
  return {
    match: vi.fn((key: string) => Promise.resolve(store.get(key) ?? undefined)),
    put: vi.fn((key: string, value: Response) => {
      puts.push({ key, response: value });
      store.set(key, value.clone());
      return Promise.resolve();
    }),
    delete: vi.fn((key: string) => {
      store.delete(key);
      return Promise.resolve(true);
    }),
    _store: store,
    _puts: puts,
  };
}

function makeCachesMock(cache = makeCacheMock()) {
  return { open: vi.fn().mockResolvedValue(cache), _cache: cache };
}

function artifactRequest(path: string, headers: Record<string, string> = {}): Request {
  return new Request(`https://app.example.com${path}`, { headers });
}

function fullBodyResponse(body: string, contentType = "video/mp4"): Response {
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": contentType, "Content-Length": String(body.length) },
  });
}

// --------------------------------------------------------------------------
// parseRangeHeader
// --------------------------------------------------------------------------

describe("parseRangeHeader", () => {
  it("parses a bounded range", () => {
    expect(parseRangeHeader("bytes=0-99")).toEqual({ start: 0, end: 99 });
    expect(parseRangeHeader("bytes=100-199")).toEqual({ start: 100, end: 199 });
  });

  it("parses an open-ended range", () => {
    expect(parseRangeHeader("bytes=5-")).toEqual({ start: 5 });
  });

  it("parses a suffix range", () => {
    expect(parseRangeHeader("bytes=-500")).toEqual({ suffix: 500 });
  });

  it("rejects malformed and multi-range headers", () => {
    expect(parseRangeHeader("bytes=0-99,200-299")).toBeNull();
    expect(parseRangeHeader("items=0-99")).toBeNull();
    expect(parseRangeHeader("bytes=-")).toBeNull();
    expect(parseRangeHeader("bytes=a-b")).toBeNull();
  });

  it("rejects an inverted range", () => {
    expect(parseRangeHeader("bytes=200-100")).toBeNull();
  });

  it("rejects a zero-length suffix", () => {
    expect(parseRangeHeader("bytes=-0")).toBeNull();
  });
});

// --------------------------------------------------------------------------
// rangeResponseFrom
// --------------------------------------------------------------------------

describe("rangeResponseFrom", () => {
  it("returns the cached response untouched without a Range header", async () => {
    const cached = fullBodyResponse("0123456789");
    const result = await rangeResponseFrom(cached, null);
    expect(result).toBe(cached);
    expect(result.status).toBe(200);
  });

  it("returns the cached response untouched on a malformed range", async () => {
    const cached = fullBodyResponse("0123456789");
    const result = await rangeResponseFrom(cached, "bytes=not-a-range");
    expect(result).toBe(cached);
    expect(result.status).toBe(200);
  });

  it("slices a bounded range into a 206", async () => {
    const cached = fullBodyResponse("0123456789");
    const result = await rangeResponseFrom(cached, "bytes=2-5");
    expect(result.status).toBe(206);
    expect(result.headers.get("Content-Range")).toBe("bytes 2-5/10");
    expect(result.headers.get("Content-Length")).toBe("4");
    expect(result.headers.get("Content-Type")).toBe("video/mp4");
    expect(result.headers.get("Accept-Ranges")).toBe("bytes");
    expect(await result.text()).toBe("2345");
  });

  it("slices an open-ended range to the end of the body", async () => {
    const cached = fullBodyResponse("0123456789");
    const result = await rangeResponseFrom(cached, "bytes=8-");
    expect(result.status).toBe(206);
    expect(result.headers.get("Content-Range")).toBe("bytes 8-9/10");
    expect(await result.text()).toBe("89");
  });

  it("slices a suffix range counting from the end", async () => {
    const cached = fullBodyResponse("0123456789");
    const result = await rangeResponseFrom(cached, "bytes=-3");
    expect(result.status).toBe(206);
    expect(result.headers.get("Content-Range")).toBe("bytes 7-9/10");
    expect(await result.text()).toBe("789");
  });

  it("clamps an end beyond the body size", async () => {
    const cached = fullBodyResponse("0123456789");
    const result = await rangeResponseFrom(cached, "bytes=5-99");
    expect(result.status).toBe(206);
    expect(result.headers.get("Content-Range")).toBe("bytes 5-9/10");
    expect(await result.text()).toBe("56789");
  });

  it("answers an unsatisfiable range with 416 and the unsatisfied Content-Range (issue #210)", async () => {
    const cached = fullBodyResponse("0123456789");
    const result = await rangeResponseFrom(cached, "bytes=10-19");
    expect(result).not.toBe(cached);
    expect(result.status).toBe(416);
    expect(result.headers.get("Content-Range")).toBe("bytes */10");
    // The returned response must still be readable by the consumer: a body
    // drained by this function makes the browser fail the fetch with a
    // TypeError instead of streaming the error.
    expect(await result.text()).toBe("");
  });
});

// --------------------------------------------------------------------------
// artifactCacheKeyForUrl
// --------------------------------------------------------------------------

describe("artifactCacheKeyForUrl", () => {
  it("maps each artifact file onto its cache key", () => {
    const base = "https://app.example.com/api/r2/artifact/job-1";
    expect(artifactCacheKeyForUrl(new URL(`${base}/output.mp3`))).toBe(
      "/sow-artifact-cache/job-1/mp3"
    );
    expect(artifactCacheKeyForUrl(new URL(`${base}/output.mp4`))).toBe(
      "/sow-artifact-cache/job-1/mp4"
    );
    expect(artifactCacheKeyForUrl(new URL(`${base}/chapters.json`))).toBe(
      "/sow-artifact-cache/job-1/chapters"
    );
  });

  it("returns null for unrecognized or malformed artifact paths", () => {
    const base = "https://app.example.com/api/r2/artifact";
    expect(artifactCacheKeyForUrl(new URL(`${base}/job-1/other.bin`))).toBeNull();
    expect(artifactCacheKeyForUrl(new URL(`${base}/job-1`))).toBeNull();
    expect(artifactCacheKeyForUrl(new URL(`${base}`))).toBeNull();
  });
});

// --------------------------------------------------------------------------
// artifactHandler
// --------------------------------------------------------------------------

describe("artifactHandler", () => {
  it("serves a Range request from the mapped cache key as a 206 slice", async () => {
    const cache = makeCacheMock();
    cache._store.set("/sow-artifact-cache/job-1/mp4", fullBodyResponse("0123456789"));
    const cachesRef = makeCachesMock(cache);

    const response = await artifactHandler({
      request: artifactRequest("/api/r2/artifact/job-1/output.mp4", {
        Range: "bytes=3-6",
      }),
      caches: cachesRef,
    });

    expect(response.status).toBe(206);
    expect(response.headers.get("Content-Range")).toBe("bytes 3-6/10");
    expect(await response.text()).toBe("3456");
    // Cache hit: no network fetch, nothing written.
    expect(cache._puts).toHaveLength(0);
  });

  it("serves a cache hit without Range as the full 200", async () => {
    const cache = makeCacheMock();
    cache._store.set("/sow-artifact-cache/job-1/mp3", fullBodyResponse("audio-bytes", "audio/mpeg"));
    const cachesRef = makeCachesMock(cache);

    const response = await artifactHandler({
      request: artifactRequest("/api/r2/artifact/job-1/output.mp3"),
      caches: cachesRef,
    });

    expect(response.status).toBe(200);
    expect(await response.text()).toBe("audio-bytes");
    expect(cache._puts).toHaveLength(0);
  });

  it("on a cache miss with Range, fetches the full body, stores the 200 under the mapped key, and serves the slice", async () => {
    const cache = makeCacheMock();
    const cachesRef = makeCachesMock(cache);
    const fullBody = fullBodyResponse("0123456789");
    const fetchFn = vi.fn().mockResolvedValue(fullBody);

    const response = await artifactHandler({
      request: artifactRequest("/api/r2/artifact/job-1/output.mp4", {
        Range: "bytes=2-4",
      }),
      caches: cachesRef,
      fetchFn,
    });

    // The Range header was NOT forwarded: the network fetch is a full 200.
    expect(fetchFn).toHaveBeenCalledTimes(1);
    const storedRequest = fetchFn.mock.calls[0][0] as Request;
    expect(storedRequest.url).toBe(
      "https://app.example.com/api/r2/artifact/job-1/output.mp4"
    );
    expect(storedRequest.headers.get("range")).toBeNull();

    // The stored entry is a full 200 under the mapped key — never a 206.
    expect(cache._puts).toHaveLength(1);
    expect(cache._puts[0].key).toBe("/sow-artifact-cache/job-1/mp4");
    expect(cache._puts[0].response.status).toBe(200);

    // And the caller still receives the requested slice.
    expect(response.status).toBe(206);
    expect(response.headers.get("Content-Range")).toBe("bytes 2-4/10");
    expect(await response.text()).toBe("234");
  });

  it("passes an upstream 206 on a ranged miss through untouched", async () => {
    const cache = makeCacheMock();
    const cachesRef = makeCachesMock(cache);
    const fetchFn = vi
      .fn()
      .mockResolvedValue(new Response("partial", { status: 206 }));

    const response = await artifactHandler({
      request: artifactRequest("/api/r2/artifact/job-1/output.mp4", {
        Range: "bytes=0-4",
      }),
      caches: cachesRef,
      fetchFn,
    });

    expect(cache._puts).toHaveLength(0);
    expect(cache._store.size).toBe(0);
    // Passed through untouched: no fabricated Content-Range over the error body.
    expect(response.status).toBe(206);
    expect(response.headers.get("Content-Range")).toBeNull();
  });

  it("passes a ranged 404 miss through unsliced and stores nothing", async () => {
    const cache = makeCacheMock();
    const cachesRef = makeCachesMock(cache);
    const fetchFn = vi
      .fn()
      .mockResolvedValue(new Response("nope", { status: 404 }));

    const response = await artifactHandler({
      request: artifactRequest("/api/r2/artifact/job-1/output.mp4", {
        Range: "bytes=0-4",
      }),
      caches: cachesRef,
      fetchFn,
    });

    expect(response.status).toBe(404);
    expect(await response.text()).toBe("nope");
    expect(cache._puts).toHaveLength(0);
  });

  it("on a cache miss without Range, fetches and stores the full body (self-warm)", async () => {
    const cache = makeCacheMock();
    const cachesRef = makeCachesMock(cache);
    const fetchFn = vi.fn().mockResolvedValue(fullBodyResponse("0123456789"));

    const response = await artifactHandler({
      request: artifactRequest("/api/r2/artifact/job-1/chapters.json"),
      caches: cachesRef,
      fetchFn,
    });

    expect(fetchFn).toHaveBeenCalledTimes(1);
    expect(cache._puts).toHaveLength(1);
    expect(cache._puts[0].key).toBe("/sow-artifact-cache/job-1/chapters");
    expect(response.status).toBe(200);
    expect(await response.text()).toBe("0123456789");
  });

  it("passes ?download=1 through to the network without touching the cache", async () => {
    const cache = makeCacheMock();
    const cachesRef = makeCachesMock(cache);
    const networkResponse = new Response("download-body", {
      status: 200,
      headers: { "Content-Disposition": 'attachment; filename="song.mp4"' },
    });
    const fetchFn = vi.fn().mockResolvedValue(networkResponse);

    const response = await artifactHandler({
      request: artifactRequest("/api/r2/artifact/job-1/output.mp4?download=1"),
      caches: cachesRef,
      fetchFn,
    });

    expect(fetchFn).toHaveBeenCalledTimes(1);
    expect((fetchFn.mock.calls[0][0] as Request).url).toBe(
      "https://app.example.com/api/r2/artifact/job-1/output.mp4?download=1"
    );
    expect(response).toBe(networkResponse);
    expect(response.headers.get("Content-Disposition")).toBe(
      'attachment; filename="song.mp4"'
    );
    expect(cache._puts).toHaveLength(0);
  });

  it("passes unrecognized artifact paths through to the network", async () => {
    const cache = makeCacheMock();
    const cachesRef = makeCachesMock(cache);
    const networkResponse = fullBodyResponse("passthrough");
    const fetchFn = vi.fn().mockResolvedValue(networkResponse);

    const response = await artifactHandler({
      request: artifactRequest("/api/r2/artifact/job-1/unknown.bin"),
      caches: cachesRef,
      fetchFn,
    });

    expect(fetchFn).toHaveBeenCalledTimes(1);
    expect(response).toBe(networkResponse);
    expect(cache._puts).toHaveLength(0);
  });

  // Cache Storage failures must never turn a request into a media error while
  // the network is available: before this route existed every artifact request
  // was a plain network passthrough, and a blocked/over-quota/evicting cache
  // must not regress that.
  it("falls back to the network when Cache Storage cannot be opened", async () => {
    const networkResponse = fullBodyResponse("from-network");
    const fetchFn = vi.fn().mockResolvedValue(networkResponse);
    const cachesRef = {
      open: vi.fn().mockRejectedValue(new Error("Cache Storage disabled")),
    };

    const response = await artifactHandler({
      request: artifactRequest("/api/r2/artifact/job-1/output.mp4", { Range: "bytes=0-3" }),
      caches: cachesRef,
      fetchFn,
    });

    expect(response).toBe(networkResponse);
    expect(fetchFn).toHaveBeenCalledTimes(1);
    // The original request is passed through untouched, Range header included.
    expect(fetchFn.mock.calls[0][0]).toBeInstanceOf(Request);
  });

  it("serves the fetched body when storing it fails (quota)", async () => {
    const cache = makeCacheMock();
    cache.put = vi.fn().mockRejectedValue(new Error("QuotaExceededError"));
    const cachesRef = makeCachesMock(cache);
    const fetchFn = vi.fn().mockResolvedValue(fullBodyResponse("0123456789"));

    const response = await artifactHandler({
      request: artifactRequest("/api/r2/artifact/job-1/output.mp4", { Range: "bytes=3-6" }),
      caches: cachesRef,
      fetchFn,
    });

    expect(response.status).toBe(206);
    expect(response.headers.get("Content-Range")).toBe("bytes 3-6/10");
    expect(await response.text()).toBe("3456");
  });

  it("treats a failing cache lookup as a miss and serves from the network", async () => {
    const cache = makeCacheMock();
    cache.match = vi.fn().mockRejectedValue(new Error("cache read failed"));
    const cachesRef = makeCachesMock(cache);
    const fetchFn = vi.fn().mockResolvedValue(fullBodyResponse("0123456789"));

    const response = await artifactHandler({
      request: artifactRequest("/api/r2/artifact/job-1/output.mp4", { Range: "bytes=0-3" }),
      caches: cachesRef,
      fetchFn,
    });

    expect(response.status).toBe(206);
    expect(await response.text()).toBe("0123");
    expect(cache._puts).toHaveLength(1);
  });

  it("re-fetches when the cached entry becomes unreadable after the lookup", async () => {
    const cache = makeCacheMock();
    // A response whose body is already consumed stands in for an entry evicted
    // between match() and the body read: reading it throws.
    const consumed = fullBodyResponse("0123456789");
    await consumed.text();
    cache._store.set("/sow-artifact-cache/job-1/mp4", consumed);
    const cachesRef = makeCachesMock(cache);
    const fetchFn = vi.fn().mockResolvedValue(fullBodyResponse("fedcba9876"));

    const response = await artifactHandler({
      request: artifactRequest("/api/r2/artifact/job-1/output.mp4", { Range: "bytes=0-3" }),
      caches: cachesRef,
      fetchFn,
    });

    expect(fetchFn).toHaveBeenCalledTimes(1);
    expect(response.status).toBe(206);
    expect(await response.text()).toBe("fedc");
  });

  // Regression: the browser failed this exact request with "TypeError: Failed
  // to fetch" because rangeResponseFrom drained the cached body via blob()
  // before returning the response unchanged. Issue #210 changed the shape:
  // an unsatisfiable range is now a 416 (the media element recovers instead
  // of rejecting the fetch), while malformed ranges keep the full-200
  // degradation (RFC 9110: an invalid Range header MUST be ignored).
  it("answers an unsatisfiable range on a cache hit with 416 and bytes */size", async () => {
    const cache = makeCacheMock();
    cache._store.set("/sow-artifact-cache/job-1/mp4", fullBodyResponse("0123456789"));
    const cachesRef = makeCachesMock(cache);
    const fetchFn = vi.fn();

    const response = await artifactHandler({
      request: artifactRequest("/api/r2/artifact/job-1/output.mp4", { Range: "bytes=999-1200" }),
      caches: cachesRef,
      fetchFn,
    });

    expect(fetchFn).not.toHaveBeenCalled();
    expect(response.status).toBe(416);
    expect(response.headers.get("Content-Range")).toBe("bytes */10");
  });

  it("answers an unsatisfiable range on a cache miss with 416 after storing the full body", async () => {
    const cache = makeCacheMock();
    const cachesRef = makeCachesMock(cache);
    const fetchFn = vi.fn().mockResolvedValue(fullBodyResponse("0123456789"));

    const response = await artifactHandler({
      request: artifactRequest("/api/r2/artifact/job-1/output.mp4", { Range: "bytes=10-" }),
      caches: cachesRef,
      fetchFn,
    });

    expect(response.status).toBe(416);
    expect(response.headers.get("Content-Range")).toBe("bytes */10");
    // The self-warm still happened: the full 200 was cached before slicing.
    expect(cache._puts).toHaveLength(1);
    expect(cache._puts[0].response.status).toBe(200);
  });

  it("keeps the graceful full-200 degradation for a malformed Range on a cache hit", async () => {
    const cache = makeCacheMock();
    cache._store.set("/sow-artifact-cache/job-1/mp4", fullBodyResponse("0123456789"));
    const cachesRef = makeCachesMock(cache);

    const response = await artifactHandler({
      request: artifactRequest("/api/r2/artifact/job-1/output.mp4", { Range: "bytes=not-a-range" }),
      caches: cachesRef,
      fetchFn: vi.fn(),
    });

    expect(response.status).toBe(200);
    expect(await response.text()).toBe("0123456789");
  });

  // Regression: workbox-routing 7 calls handlers as
  // `handle({url, request, event, params})` — no `caches`, no `fetchFn`. When
  // the handler destructured `caches` without a default, every real SW
  // request threw on `cachesRef.open` and the router's catch handler turned
  // it into Response.error(), so nothing was ever served from cache.
  it("serves through the ambient caches global when invoked with workbox router params only", async () => {
    const cache = makeCacheMock();
    cache._store.set("/sow-artifact-cache/job-1/mp4", fullBodyResponse("0123456789"));
    vi.stubGlobal("caches", makeCachesMock(cache));
    const fetchFn = vi.fn();
    vi.stubGlobal("fetch", fetchFn);

    try {
      const url = new URL("https://app.example.com/api/r2/artifact/job-1/output.mp4");
      const request = new Request(url, { headers: { Range: "bytes=3-6" } });
      const response = await artifactHandler({ url, request, event: undefined, params: [] });

      expect(response.status).toBe(206);
      expect(response.headers.get("Content-Range")).toBe("bytes 3-6/10");
      expect(await response.text()).toBe("3456");
      expect(fetchFn).not.toHaveBeenCalled();
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

