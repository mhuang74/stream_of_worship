import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  AUDIO_BYTES_CACHE_NAME,
  MAX_SONG_BLOB_URLS,
  clearSongAudioSrc,
  createAudioBytesCache,
  resolveSongAudioSrc,
} from "@/lib/audio/audio-bytes-cache";

// ---------------------------------------------------------------------------
// Fake Cache Storage
// ---------------------------------------------------------------------------

interface FakeCacheStorage {
  stores: Map<string, Map<string, Response>>;
  instance: CacheStorage;
}

function fakeCacheStorage(): FakeCacheStorage {
  const stores = new Map<string, Map<string, Response>>();
  const openStore = (name: string): Map<string, Response> => {
    const existing = stores.get(name);
    if (existing) return existing;
    const created = new Map<string, Response>();
    stores.set(name, created);
    return created;
  };

  const cacheFor = (store: Map<string, Response>): Cache => ({
    match: async (request: RequestInfo | URL) =>
      store.get(String(request))?.clone(),
    put: async (request: RequestInfo | URL, response: Response) => {
      store.set(String(request), response.clone());
    },
    delete: async (request: RequestInfo | URL) => store.delete(String(request)),
    keys: async () => [...store.keys()].map((key) => new Request(key)),
  }) as unknown as Cache;

  const instance = {
    open: async (name: string) => cacheFor(openStore(name)),
  } as unknown as CacheStorage;

  return { stores, instance };
}

function bytesBuffer(size: number): ArrayBuffer {
  const buffer = new ArrayBuffer(size);
  const view = new Uint8Array(buffer);
  for (let i = 0; i < size; i++) view[i] = i % 251;
  return buffer;
}

function fetchingMock(size: number) {
  return vi.fn(async () => bytesBuffer(size));
}

const flush = async (): Promise<void> => {
  for (let i = 0; i < 8; i++) await Promise.resolve();
};

beforeEach(() => {
  clearSongAudioSrc();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("createAudioBytesCache", () => {
  it("fetches once and hands every load its own copy of the bytes", async () => {
    const fetchBytes = fetchingMock(8);
    const cache = createAudioBytesCache();

    const first = await cache.load("hash-a", fetchBytes);
    const second = await cache.load("hash-a", fetchBytes);

    expect(fetchBytes).toHaveBeenCalledTimes(1);
    expect(first).not.toBe(second);
    expect(second.byteLength).toBe(8);

    // decodeAudioData detaches the buffer it is given; the cached copy must
    // survive that.
    structuredClone(first, { transfer: [first] });
    expect(first.byteLength).toBe(0);
    const third = await cache.load("hash-a", fetchBytes);
    expect(third.byteLength).toBe(8);
    expect(fetchBytes).toHaveBeenCalledTimes(1);
  });

  it("evicts the least recently used key once the byte ceiling is crossed", async () => {
    const fetchBytes = vi.fn(async () => bytesBuffer(8));
    const cache = createAudioBytesCache({ maxBytes: 16 });

    await cache.load("a", fetchBytes);
    await cache.load("b", fetchBytes);
    await cache.load("c", fetchBytes);

    expect(cache.memoryBytes).toBe(16);
    expect(fetchBytes).toHaveBeenCalledTimes(3);

    // The oldest key is gone; the two survivors are not refetched.
    await cache.load("b", fetchBytes);
    await cache.load("c", fetchBytes);
    expect(fetchBytes).toHaveBeenCalledTimes(3);
    await cache.load("a", fetchBytes);
    expect(fetchBytes).toHaveBeenCalledTimes(4);
  });

  it("does not cache a single entry larger than the whole ceiling", async () => {
    const fetchBytes = fetchingMock(16);
    const cache = createAudioBytesCache({ maxBytes: 8 });

    await cache.load("big", fetchBytes);
    expect(cache.memoryBytes).toBe(0);

    await cache.load("big", fetchBytes);
    expect(fetchBytes).toHaveBeenCalledTimes(2);
  });

  it("joins concurrent loads for one key into a single fetch", async () => {
    const fetchBytes = fetchingMock(8);
    const cache = createAudioBytesCache();

    const [first, second] = await Promise.all([
      cache.load("hash-a", fetchBytes),
      cache.load("hash-a", fetchBytes),
    ]);

    expect(fetchBytes).toHaveBeenCalledTimes(1);
    expect(first.byteLength).toBe(8);
    expect(second.byteLength).toBe(8);
    expect(first).not.toBe(second);
  });

  it("serves a cold cache from Cache Storage and records content type", async () => {
    const storage = fakeCacheStorage();
    vi.stubGlobal("caches", storage.instance);
    const fetchBytes = fetchingMock(8);

    const first = createAudioBytesCache();
    await first.load("hash-a", fetchBytes);
    await flush();

    const stored = storage.stores
      .get(AUDIO_BYTES_CACHE_NAME)
      ?.get("/sow-audio-bytes/entry/hash-a");
    expect(stored?.headers.get("Content-Type")).toBe("audio/mpeg");

    // A reload starts from an empty memory cache but the same Cache Storage.
    const reloaded = createAudioBytesCache();
    const bytes = await reloaded.load("hash-a", fetchBytes);
    expect(bytes.byteLength).toBe(8);
    expect(fetchBytes).toHaveBeenCalledTimes(1);
  });

  it("caps the number of persisted entries", async () => {
    const storage = fakeCacheStorage();
    vi.stubGlobal("caches", storage.instance);
    const cache = createAudioBytesCache({ maxPersistedEntries: 1 });

    await cache.load("a", fetchingMock(8));
    await flush();
    await cache.load("b", fetchingMock(8));
    await flush();

    const store = storage.stores.get(AUDIO_BYTES_CACHE_NAME);
    expect(store?.has("/sow-audio-bytes/entry/a")).toBe(false);
    expect(store?.has("/sow-audio-bytes/entry/b")).toBe(true);
  });

  it("degrades to memory only when Cache Storage is unavailable", async () => {
    vi.stubGlobal("caches", undefined);
    const fetchBytes = fetchingMock(8);
    const cache = createAudioBytesCache();

    await expect(cache.load("hash-a", fetchBytes)).resolves.toHaveProperty(
      "byteLength",
      8,
    );
    await cache.load("hash-a", fetchBytes);
    await expect(cache.clear()).resolves.toBeUndefined();
    expect(fetchBytes).toHaveBeenCalledTimes(1);
  });
});

describe("resolveSongAudioSrc", () => {
  beforeEach(() => {
    let nextUrl = 0;
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn(() => `blob:test/${nextUrl++}`),
      revokeObjectURL: vi.fn(),
    });
  });

  it("memoizes one blob URL per Recording and fetches the proxy once", async () => {
    const fetchMock = vi.fn(
      async () =>
        ({
          ok: true,
          status: 200,
          headers: { get: () => "8" },
          body: null,
          arrayBuffer: async () => bytesBuffer(8),
        }) as unknown as Response,
    );
    vi.stubGlobal("fetch", fetchMock);
    const cache = createAudioBytesCache();

    const first = await resolveSongAudioSrc("hash-a", cache);
    const second = await resolveSongAudioSrc("hash-a", cache);

    expect(second).toBe(first);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith("/api/recordings/hash-a/audio");
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
  });

  it("revokes the oldest blob URL past the cap", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          ({
            ok: true,
            status: 200,
            headers: { get: () => "8" },
            body: null,
            arrayBuffer: async () => bytesBuffer(8),
          }) as unknown as Response,
      ),
    );
    const cache = createAudioBytesCache();

    const urls: string[] = [];
    for (let i = 0; i <= MAX_SONG_BLOB_URLS; i++) {
      urls.push(await resolveSongAudioSrc(`hash-${i}`, cache));
    }

    expect(urls).toHaveLength(MAX_SONG_BLOB_URLS + 1);
    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(1);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith(urls[0]);
  });

  it("propagates a fetch failure so the caller can fall back to streaming", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false, status: 404 }) as unknown as Response),
    );

    await expect(
      resolveSongAudioSrc("hash-a", createAudioBytesCache()),
    ).rejects.toThrow("HTTP 404");
  });
});
