import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  ARTIFACT_CACHE_NAME,
  WARN_STORAGE_BYTES,
  HARD_LIMIT_BYTES,
  isOfflineSupportedOnCurrentDevice,
  requestPersistentStorage,
  getStorageBudget,
  getArtifactCacheStatus,
  cacheArtifacts,
  invalidateArtifactCache,
} from "@/lib/offline/artifact-cache";

// --------------------------------------------------------------------------
// Helpers
// --------------------------------------------------------------------------

function makeCacheMock() {
  const store = new Map<string, Response>();

  return {
    match: vi.fn((key: string) => Promise.resolve(store.get(key) ?? undefined)),
    put: vi.fn((key: string, value: Response) => {
      store.set(key, value);
      return Promise.resolve();
    }),
    delete: vi.fn((key: string) => {
      store.delete(key);
      return Promise.resolve(true);
    }),
    _store: store,
  };
}

function setUserAgent(ua: string) {
  Object.defineProperty(navigator, "userAgent", {
    value: ua,
    writable: true,
    configurable: true,
  });
}

function setStorageEstimate(usage: number, quota: number) {
  Object.defineProperty(navigator, "storage", {
    value: {
      estimate: vi.fn().mockResolvedValue({ usage, quota }),
      persist: vi.fn().mockResolvedValue(true),
    },
    writable: true,
    configurable: true,
  });
}

// --------------------------------------------------------------------------
// Constants
// --------------------------------------------------------------------------

describe("constants", () => {
  it("ARTIFACT_CACHE_NAME is sow-artifacts", () => {
    expect(ARTIFACT_CACHE_NAME).toBe("sow-artifacts");
  });

  it("WARN_STORAGE_BYTES is 500 MB", () => {
    expect(WARN_STORAGE_BYTES).toBe(500 * 1024 * 1024);
  });

  it("HARD_LIMIT_BYTES is 1 GB", () => {
    expect(HARD_LIMIT_BYTES).toBe(1024 * 1024 * 1024);
  });
});

// --------------------------------------------------------------------------
// isOfflineSupportedOnCurrentDevice
// --------------------------------------------------------------------------

describe("isOfflineSupportedOnCurrentDevice", () => {
  afterEach(() => vi.restoreAllMocks());

  it("returns true for a desktop browser", () => {
    setUserAgent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36");
    expect(isOfflineSupportedOnCurrentDevice()).toBe(true);
  });

  it("returns true for iOS 18", () => {
    setUserAgent("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)");
    expect(isOfflineSupportedOnCurrentDevice()).toBe(true);
  });

  it("returns true for iOS 17.4", () => {
    setUserAgent("Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X)");
    expect(isOfflineSupportedOnCurrentDevice()).toBe(true);
  });

  it("returns true for iOS 17.5", () => {
    setUserAgent("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X)");
    expect(isOfflineSupportedOnCurrentDevice()).toBe(true);
  });

  it("returns false for iOS 17.3", () => {
    setUserAgent("Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X)");
    expect(isOfflineSupportedOnCurrentDevice()).toBe(false);
  });

  it("returns false for iOS 16", () => {
    setUserAgent("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X)");
    expect(isOfflineSupportedOnCurrentDevice()).toBe(false);
  });

  it("returns false when iOS version cannot be parsed", () => {
    setUserAgent("Mozilla/5.0 (iPhone; CPU iPhone OS like Mac OS X)");
    expect(isOfflineSupportedOnCurrentDevice()).toBe(false);
  });

  it("returns true for iPad running iOS 18", () => {
    setUserAgent("Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X)");
    expect(isOfflineSupportedOnCurrentDevice()).toBe(true);
  });
});

// --------------------------------------------------------------------------
// requestPersistentStorage
// --------------------------------------------------------------------------

describe("requestPersistentStorage", () => {
  afterEach(() => vi.restoreAllMocks());

  it("returns true when persist() succeeds", async () => {
    Object.defineProperty(navigator, "storage", {
      value: { persist: vi.fn().mockResolvedValue(true) },
      writable: true,
      configurable: true,
    });

    expect(await requestPersistentStorage()).toBe(true);
  });

  it("returns false when persist() returns false", async () => {
    Object.defineProperty(navigator, "storage", {
      value: { persist: vi.fn().mockResolvedValue(false) },
      writable: true,
      configurable: true,
    });

    expect(await requestPersistentStorage()).toBe(false);
  });

  it("returns false when storage API is unavailable", async () => {
    Object.defineProperty(navigator, "storage", {
      value: {},
      writable: true,
      configurable: true,
    });

    expect(await requestPersistentStorage()).toBe(false);
  });

  it("returns false when persist() throws", async () => {
    Object.defineProperty(navigator, "storage", {
      value: { persist: vi.fn().mockRejectedValue(new Error("denied")) },
      writable: true,
      configurable: true,
    });

    expect(await requestPersistentStorage()).toBe(false);
  });
});

// --------------------------------------------------------------------------
// getStorageBudget
// --------------------------------------------------------------------------

describe("getStorageBudget", () => {
  afterEach(() => vi.restoreAllMocks());

  it("returns usage and quota from navigator.storage.estimate()", async () => {
    setStorageEstimate(100_000_000, 2_000_000_000);

    const budget = await getStorageBudget();

    expect(budget.usageBytes).toBe(100_000_000);
    expect(budget.quotaBytes).toBe(2_000_000_000);
  });

  it("isWarning is false below 500 MB", async () => {
    setStorageEstimate(WARN_STORAGE_BYTES - 1, 2_000_000_000);
    const { isWarning } = await getStorageBudget();
    expect(isWarning).toBe(false);
  });

  it("isWarning is true at 500 MB", async () => {
    setStorageEstimate(WARN_STORAGE_BYTES, 2_000_000_000);
    const { isWarning } = await getStorageBudget();
    expect(isWarning).toBe(true);
  });

  it("isOverLimit is false below 1 GB", async () => {
    setStorageEstimate(HARD_LIMIT_BYTES - 1, 2_000_000_000);
    const { isOverLimit } = await getStorageBudget();
    expect(isOverLimit).toBe(false);
  });

  it("isOverLimit is true at 1 GB", async () => {
    setStorageEstimate(HARD_LIMIT_BYTES, 2_000_000_000);
    const { isOverLimit } = await getStorageBudget();
    expect(isOverLimit).toBe(true);
  });

  it("returns empty budget when storage API is unavailable", async () => {
    Object.defineProperty(navigator, "storage", {
      value: {},
      writable: true,
      configurable: true,
    });

    const budget = await getStorageBudget();
    expect(budget.usageBytes).toBe(0);
    expect(budget.quotaBytes).toBe(0);
  });

  it("returns empty budget when estimate() throws", async () => {
    Object.defineProperty(navigator, "storage", {
      value: { estimate: vi.fn().mockRejectedValue(new Error("unavailable")) },
      writable: true,
      configurable: true,
    });

    const budget = await getStorageBudget();
    expect(budget.usageBytes).toBe(0);
  });
});

// --------------------------------------------------------------------------
// getArtifactCacheStatus
// --------------------------------------------------------------------------

describe("getArtifactCacheStatus", () => {
  let cacheMock: ReturnType<typeof makeCacheMock>;

  beforeEach(() => {
    cacheMock = makeCacheMock();
    Object.defineProperty(global, "caches", {
      value: { open: vi.fn().mockResolvedValue(cacheMock) },
      writable: true,
      configurable: true,
    });
    setStorageEstimate(0, 2_000_000_000);
  });

  afterEach(() => vi.restoreAllMocks());

  it("returns isCached:false when mp3 is not in cache", async () => {
    const status = await getArtifactCacheStatus("job-1", {
      mp3Url: "https://r2.example.com/audio.mp3",
    });
    expect(status.isCached).toBe(false);
    expect(status.renderJobId).toBe("job-1");
  });

  it("returns isCached:true when mp3 is in cache", async () => {
    // Pre-populate the mock store with the stable key.
    cacheMock._store.set("/sow-artifact-cache/job-1/mp3", new Response("audio"));

    const status = await getArtifactCacheStatus("job-1", {
      mp3Url: "https://r2.example.com/audio.mp3",
    });
    expect(status.isCached).toBe(true);
  });

  it("falls back to mp4 check when mp3Url is absent", async () => {
    cacheMock._store.set("/sow-artifact-cache/job-1/mp4", new Response("video"));

    const status = await getArtifactCacheStatus("job-1", {
      mp4Url: "https://r2.example.com/video.mp4",
    });
    expect(status.isCached).toBe(true);
  });

  it("returns isCached:false when neither mp3 nor mp4 url is provided", async () => {
    const status = await getArtifactCacheStatus("job-1", {});
    expect(status.isCached).toBe(false);
  });

  it("returns isCached:false when caches.open throws", async () => {
    Object.defineProperty(global, "caches", {
      value: { open: vi.fn().mockRejectedValue(new Error("quota exceeded")) },
      writable: true,
      configurable: true,
    });

    const status = await getArtifactCacheStatus("job-1", {
      mp3Url: "https://r2.example.com/audio.mp3",
    });
    expect(status.isCached).toBe(false);
  });
});

// --------------------------------------------------------------------------
// cacheArtifacts
// --------------------------------------------------------------------------

describe("cacheArtifacts", () => {
  let cacheMock: ReturnType<typeof makeCacheMock>;

  beforeEach(() => {
    cacheMock = makeCacheMock();
    Object.defineProperty(global, "caches", {
      value: { open: vi.fn().mockResolvedValue(cacheMock) },
      writable: true,
      configurable: true,
    });
    setStorageEstimate(0, 2_000_000_000);

    global.fetch = vi.fn().mockResolvedValue({ ok: true, body: null });
  });

  afterEach(() => vi.restoreAllMocks());

  it("fetches mp3 and stores it under a stable cache key", async () => {
    await cacheArtifacts("job-1", { mp3Url: "https://r2.example.com/audio.mp3" });

    expect(global.fetch).toHaveBeenCalledWith("https://r2.example.com/audio.mp3");
    expect(cacheMock.put).toHaveBeenCalledWith(
      "/sow-artifact-cache/job-1/mp3",
      expect.anything()
    );
  });

  it("fetches all three artifacts in order", async () => {
    await cacheArtifacts("job-1", {
      mp3Url: "https://r2.example.com/audio.mp3",
      mp4Url: "https://r2.example.com/video.mp4",
      chaptersUrl: "https://r2.example.com/chapters.json",
    });

    expect(global.fetch).toHaveBeenCalledTimes(3);
    expect(cacheMock.put).toHaveBeenCalledWith(
      "/sow-artifact-cache/job-1/mp3",
      expect.anything()
    );
    expect(cacheMock.put).toHaveBeenCalledWith(
      "/sow-artifact-cache/job-1/mp4",
      expect.anything()
    );
    expect(cacheMock.put).toHaveBeenCalledWith(
      "/sow-artifact-cache/job-1/chapters",
      expect.anything()
    );
  });

  it("reports progress via onProgress callback", async () => {
    const onProgress = vi.fn();

    await cacheArtifacts(
      "job-1",
      {
        mp3Url: "https://r2.example.com/audio.mp3",
        mp4Url: "https://r2.example.com/video.mp4",
      },
      onProgress
    );

    expect(onProgress).toHaveBeenCalledWith(50);
    expect(onProgress).toHaveBeenCalledWith(100);
  });

  it("throws when no artifacts are provided", async () => {
    await expect(cacheArtifacts("job-1", {})).rejects.toThrow("No artifacts to cache");
  });

  it("throws when fetch returns non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 403 });

    await expect(
      cacheArtifacts("job-1", { mp3Url: "https://r2.example.com/audio.mp3" })
    ).rejects.toThrow("Failed to fetch artifact");
  });

  it("throws when storage hard limit is exceeded", async () => {
    setStorageEstimate(HARD_LIMIT_BYTES, 2_000_000_000);

    await expect(
      cacheArtifacts("job-1", { mp3Url: "https://r2.example.com/audio.mp3" })
    ).rejects.toThrow("Storage limit exceeded");
  });

  it("throws when Cache Storage API is unavailable", async () => {
    Object.defineProperty(global, "caches", {
      value: undefined,
      writable: true,
      configurable: true,
    });

    await expect(
      cacheArtifacts("job-1", { mp3Url: "https://r2.example.com/audio.mp3" })
    ).rejects.toThrow("Cache Storage API not available");
  });

  it("skips null/undefined artifact URLs", async () => {
    await cacheArtifacts("job-1", {
      mp3Url: "https://r2.example.com/audio.mp3",
      mp4Url: null,
      chaptersUrl: undefined,
    });

    expect(global.fetch).toHaveBeenCalledTimes(1);
    expect(cacheMock.put).toHaveBeenCalledTimes(1);
  });
});

// --------------------------------------------------------------------------
// invalidateArtifactCache
// --------------------------------------------------------------------------

describe("invalidateArtifactCache", () => {
  let cacheMock: ReturnType<typeof makeCacheMock>;

  beforeEach(() => {
    cacheMock = makeCacheMock();
    // Pre-populate with all three artifact types.
    cacheMock._store.set("/sow-artifact-cache/job-1/mp3", new Response("audio"));
    cacheMock._store.set("/sow-artifact-cache/job-1/mp4", new Response("video"));
    cacheMock._store.set("/sow-artifact-cache/job-1/chapters", new Response("{}"));

    Object.defineProperty(global, "caches", {
      value: { open: vi.fn().mockResolvedValue(cacheMock) },
      writable: true,
      configurable: true,
    });
  });

  afterEach(() => vi.restoreAllMocks());

  it("deletes all three artifact cache entries", async () => {
    await invalidateArtifactCache("job-1");

    expect(cacheMock.delete).toHaveBeenCalledWith("/sow-artifact-cache/job-1/mp3");
    expect(cacheMock.delete).toHaveBeenCalledWith("/sow-artifact-cache/job-1/mp4");
    expect(cacheMock.delete).toHaveBeenCalledWith("/sow-artifact-cache/job-1/chapters");
  });

  it("does not throw when entries do not exist", async () => {
    await expect(invalidateArtifactCache("nonexistent-job")).resolves.toBeUndefined();
  });

  it("does not throw when caches.open rejects", async () => {
    Object.defineProperty(global, "caches", {
      value: { open: vi.fn().mockRejectedValue(new Error("storage error")) },
      writable: true,
      configurable: true,
    });

    await expect(invalidateArtifactCache("job-1")).resolves.toBeUndefined();
  });

  it("resolves without error when Cache Storage is unavailable", async () => {
    Object.defineProperty(global, "caches", {
      value: undefined,
      writable: true,
      configurable: true,
    });

    await expect(invalidateArtifactCache("job-1")).resolves.toBeUndefined();
  });
});

// --------------------------------------------------------------------------
// Cache key isolation — different render jobs must not share cache entries
// --------------------------------------------------------------------------

describe("cache key isolation", () => {
  let cacheMock: ReturnType<typeof makeCacheMock>;

  beforeEach(() => {
    cacheMock = makeCacheMock();
    cacheMock._store.set("/sow-artifact-cache/job-A/mp3", new Response("audioA"));

    Object.defineProperty(global, "caches", {
      value: { open: vi.fn().mockResolvedValue(cacheMock) },
      writable: true,
      configurable: true,
    });
    setStorageEstimate(0, 2_000_000_000);
  });

  afterEach(() => vi.restoreAllMocks());

  it("job-A cached does not affect job-B status", async () => {
    const statusB = await getArtifactCacheStatus("job-B", {
      mp3Url: "https://r2.example.com/audio.mp3",
    });
    expect(statusB.isCached).toBe(false);
  });

  it("invalidating job-A leaves job-B entries intact", async () => {
    cacheMock._store.set("/sow-artifact-cache/job-B/mp3", new Response("audioB"));

    await invalidateArtifactCache("job-A");

    // job-B should still be in the store.
    expect(cacheMock._store.has("/sow-artifact-cache/job-B/mp3")).toBe(true);
  });
});
