/**
 * Browser-side byte cache for song and Songset playback media.
 *
 * `decodeAudioData` detaches the ArrayBuffer it is given, and both playback
 * paths rebuild their media objects often (songset sessions on every Stop and
 * every arrangement edit, the song element on every track change), so bytes
 * are cached here, outside any player: a memory LRU bounded by
 * DEFAULT_AUDIO_BYTES_MAX_BYTES, mirrored into Cache Storage so a page reload
 * still skips the download. Every caller receives its own copy because the
 * first decode would otherwise detach the cached buffer.
 *
 * Keys are recording hash prefixes, not URLs: the same Recording is reachable
 * through the same-origin proxy, a public bucket URL, or a freshly signed URL,
 * and all three must share one entry.
 *
 * Cache Storage is best-effort: absent (jsdom, private mode), quota-blocked, or
 * partitioned browsers degrade to memory-only, never to an error.
 */

export const AUDIO_BYTES_CACHE_NAME = "sow-audio-bytes";
export const DEFAULT_AUDIO_BYTES_MAX_BYTES = 64 * 1024 * 1024;
export const DEFAULT_MAX_PERSISTED_ENTRIES = 32;
export const MAX_SONG_BLOB_URLS = 4;

const MANIFEST_KEY = "/sow-audio-bytes/manifest";
const CACHE_KEY_PREFIX = "/sow-audio-bytes/entry/";
const AUDIO_CONTENT_TYPE = "audio/mpeg";

export type AudioBytesProgress = (
  receivedBytes: number,
  totalBytes: number | null,
) => void;

export interface AudioBytesCache {
  /** Memory hit, Cache Storage hit, or one shared network fetch, in that order. */
  load(
    key: string,
    fetchBytes: (onProgress: AudioBytesProgress) => Promise<ArrayBuffer>,
    onProgress?: AudioBytesProgress,
  ): Promise<ArrayBuffer>;
  /** Drops every cached byte, memory and Cache Storage. */
  clear(): Promise<void>;
  /** Bytes currently resident in memory; tests and diagnostics only. */
  readonly memoryBytes: number;
}

/** Same-origin proxy URL for one Recording's master audio. */
export function recordingAudioUrl(hashPrefix: string): string {
  return `/api/recordings/${hashPrefix}/audio`;
}

/**
 * Streams the response so callers can show byte progress; falls back to
 * `arrayBuffer()` when the environment exposes no readable body (tests, older
 * runtimes).
 */
export async function fetchAudioBytes(
  url: string,
  onProgress: AudioBytesProgress,
): Promise<ArrayBuffer> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const totalBytes = Number(response.headers.get("content-length")) || null;
  const body = response.body;
  if (body === null) {
    onProgress(0, totalBytes);
    const buffer = await response.arrayBuffer();
    onProgress(buffer.byteLength, totalBytes);
    return buffer;
  }

  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let receivedBytes = 0;
  onProgress(0, totalBytes);
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    if (!value) continue;
    chunks.push(value);
    receivedBytes += value.byteLength;
    onProgress(receivedBytes, totalBytes);
  }

  const buffer = new ArrayBuffer(receivedBytes);
  const output = new Uint8Array(buffer);
  let offset = 0;
  for (const chunk of chunks) {
    output.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return buffer;
}

interface ManifestEntry {
  url: string;
  bytes: number;
}

function isManifestEntry(value: unknown): value is ManifestEntry {
  if (typeof value !== "object" || value === null) return false;
  const entry = value as { url?: unknown; bytes?: unknown };
  return typeof entry.url === "string" && typeof entry.bytes === "number";
}

export function createAudioBytesCache(
  options: { maxBytes?: number; maxPersistedEntries?: number } = {},
): AudioBytesCache {
  const maxBytes = options.maxBytes ?? DEFAULT_AUDIO_BYTES_MAX_BYTES;
  const maxPersistedEntries =
    options.maxPersistedEntries ?? DEFAULT_MAX_PERSISTED_ENTRIES;
  // Insertion order is LRU order: the tail is most recently used.
  const memory = new Map<string, ArrayBuffer>();
  const inFlight = new Map<string, Promise<ArrayBuffer>>();
  let memoryBytes = 0;
  let persistChain: Promise<void> = Promise.resolve();

  /** Serializes manifest writes so two loads cannot interleave read-modify-write. */
  function queuePersist(work: () => Promise<void>): void {
    persistChain = persistChain.then(work).catch(() => undefined);
  }

  async function openCache(): Promise<Cache | null> {
    if (typeof window === "undefined" || typeof caches === "undefined") {
      return null;
    }
    try {
      return await caches.open(AUDIO_BYTES_CACHE_NAME);
    } catch {
      return null;
    }
  }

  async function readManifest(cache: Cache): Promise<ManifestEntry[]> {
    try {
      const response = await cache.match(MANIFEST_KEY);
      if (!response) return [];
      const parsed = (await response.json()) as { entries?: unknown };
      if (!Array.isArray(parsed.entries)) return [];
      return parsed.entries.filter(isManifestEntry);
    } catch {
      return [];
    }
  }

  async function writeManifest(
    cache: Cache,
    entries: ManifestEntry[],
  ): Promise<void> {
    await cache.put(
      MANIFEST_KEY,
      new Response(JSON.stringify({ entries }), {
        headers: { "Content-Type": "application/json" },
      }),
    );
  }

  function forgetPersisted(url: string): void {
    queuePersist(async () => {
      const cache = await openCache();
      if (!cache) return;
      const persisted = `${CACHE_KEY_PREFIX}${url}`;
      const entries = (await readManifest(cache)).filter(
        (entry) => entry.url !== persisted,
      );
      await cache.delete(persisted);
      await writeManifest(cache, entries);
    });
  }

  function evictMemory(): void {
    while (memoryBytes > maxBytes && memory.size > 0) {
      const oldest = memory.keys().next();
      if (oldest.done) return;
      const key = oldest.value;
      const dropped = memory.get(key);
      memory.delete(key);
      memoryBytes -= dropped?.byteLength ?? 0;
      forgetPersisted(key);
    }
  }

  function remember(key: string, buffer: ArrayBuffer): void {
    // One oversize Recording must not flush the whole cache.
    if (buffer.byteLength > maxBytes) return;
    const existing = memory.get(key);
    if (existing) {
      memory.delete(key);
      memoryBytes -= existing.byteLength;
    }
    memory.set(key, buffer.slice(0));
    memoryBytes += buffer.byteLength;
    evictMemory();
  }

  function readMemory(key: string): ArrayBuffer | null {
    const hit = memory.get(key);
    if (!hit) return null;
    // Re-insert marks it most-recently-used; the slice protects the stored
    // copy from the detachment `decodeAudioData` performs.
    memory.delete(key);
    memory.set(key, hit);
    return hit.slice(0);
  }

  function persist(key: string, buffer: ArrayBuffer): void {
    if (buffer.byteLength > maxBytes) return;
    // Cache Storage keys resolve as URLs, so a bare hash prefix would be
    // relative to the page; namespace them under the cache's own path.
    const persisted = `${CACHE_KEY_PREFIX}${key}`;
    queuePersist(async () => {
      const cache = await openCache();
      if (!cache) return;
      // Newest-first: `pop()` is always the oldest entry.
      const entries = (await readManifest(cache)).filter(
        (entry) => entry.url !== persisted,
      );
      entries.unshift({ url: persisted, bytes: buffer.byteLength });
      let total = entries.reduce((sum, entry) => sum + entry.bytes, 0);
      while (
        (entries.length > maxPersistedEntries || total > maxBytes) &&
        entries.length > 1
      ) {
        const dropped = entries.pop();
        if (!dropped) break;
        total -= dropped.bytes;
        await cache.delete(dropped.url);
      }
      await cache.put(
        persisted,
        new Response(buffer.slice(0), {
          headers: {
            "Content-Type": AUDIO_CONTENT_TYPE,
            "Content-Length": String(buffer.byteLength),
          },
        }),
      );
      await writeManifest(cache, entries);
    });
  }

  async function load(
    key: string,
    fetchBytes: (onProgress: AudioBytesProgress) => Promise<ArrayBuffer>,
    onProgress?: AudioBytesProgress,
  ): Promise<ArrayBuffer> {
    const memoryHit = readMemory(key);
    if (memoryHit) return memoryHit;

    // A joiner gets bytes but no progress callbacks; each player serializes
    // its own prepares anyway.
    const joined = inFlight.get(key);
    if (joined) return joined.then((buffer) => buffer.slice(0));

    const promise = (async () => {
      const cache = await openCache();
      if (cache) {
        try {
          const stored = await cache.match(`${CACHE_KEY_PREFIX}${key}`);
          if (stored) {
            const bytes = await stored.arrayBuffer();
            remember(key, bytes);
            return bytes.slice(0);
          }
        } catch {
          // Fall through to the network.
        }
      }
      const fetched = await fetchBytes(onProgress ?? (() => {}));
      remember(key, fetched);
      persist(key, fetched);
      return fetched;
    })();

    inFlight.set(key, promise);
    const settle = (): void => {
      if (inFlight.get(key) === promise) inFlight.delete(key);
    };
    promise.then(settle, settle);
    return promise;
  }

  async function clear(): Promise<void> {
    memory.clear();
    memoryBytes = 0;
    queuePersist(async () => {
      const cache = await openCache();
      if (!cache) return;
      for (const request of await cache.keys()) await cache.delete(request);
    });
    return persistChain;
  }

  return {
    load,
    clear,
    get memoryBytes() {
      return memoryBytes;
    },
  };
}

/** Process-wide cache both players use when no cache is injected. */
export const sharedAudioBytesCache = createAudioBytesCache();

const songBlobUrls = new Map<string, string>();

/**
 * Blob object URL for one Recording's audio, memoized per hash prefix so a
 * repeat play is a map hit. The `<audio>` element cannot read a cross-origin
 * bucket URL through `fetch` (R2 is not CORS-configured for this app), so the
 * bytes always come from the same-origin proxy. Throws when the bytes cannot
 * be loaded (caller falls back to the streaming URL) or when the environment
 * has no `URL.createObjectURL`.
 */
export async function resolveSongAudioSrc(
  hashPrefix: string,
  cache: AudioBytesCache = sharedAudioBytesCache,
): Promise<string> {
  const cached = songBlobUrls.get(hashPrefix);
  if (cached) {
    songBlobUrls.delete(hashPrefix);
    songBlobUrls.set(hashPrefix, cached);
    return cached;
  }

  if (typeof URL.createObjectURL !== "function") {
    throw new Error("Blob URLs unavailable");
  }

  const bytes = await cache.load(hashPrefix, (onProgress) =>
    fetchAudioBytes(recordingAudioUrl(hashPrefix), onProgress),
  );
  const blobUrl = URL.createObjectURL(
    new Blob([bytes], { type: AUDIO_CONTENT_TYPE }),
  );
  songBlobUrls.set(hashPrefix, blobUrl);

  // Only one track plays at a time and each play resolves the newest URL, so
  // an evicted URL is never the element's current `src`.
  while (songBlobUrls.size > MAX_SONG_BLOB_URLS) {
    const oldest = songBlobUrls.keys().next();
    if (oldest.done) break;
    const staleUrl = songBlobUrls.get(oldest.value);
    songBlobUrls.delete(oldest.value);
    if (staleUrl) URL.revokeObjectURL(staleUrl);
  }

  return blobUrl;
}

/** Releases every blob URL this module created; tests and logout only. */
export function clearSongAudioSrc(): void {
  for (const url of songBlobUrls.values()) URL.revokeObjectURL(url);
  songBlobUrls.clear();
}
