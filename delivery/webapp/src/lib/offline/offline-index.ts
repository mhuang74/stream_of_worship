/**
 * Offline index (issue #203): one IndexedDB store recording which songsets
 * have cached artifacts for offline playback. The durable bookkeeping that
 * later offline features (list badge, offline boot, remove-from-offline)
 * read; artifact bytes themselves live in Cache Storage (artifact-cache).
 *
 * Every operation degrades to a no-throw no-op when IndexedDB is
 * unavailable — mirroring artifact-cache's silent-failure convention.
 * putOfflineRecord reads any prior record and invalidates the prior
 * renderJobId's artifact cache entries first (supersede eviction), so a
 * re-rendered songset never leaves unreachable, undeletable cache entries.
 */

import { invalidateArtifactCache } from "./artifact-cache";

export const OFFLINE_INDEX_DB_NAME = "sow-offline-index";
export const OFFLINE_INDEX_STORE_NAME = "songsets";
const OFFLINE_INDEX_DB_VERSION = 1;

/**
 * Chapters and songset items are position-aligned: entry i of
 * chapterContentHashes is the recording contentHash of songset item i, the
 * recording Lyrics Feedback keys on.
 */
export interface OfflineSongsetRecord {
  songsetId: string;
  renderJobId: string;
  songsetName: string;
  cachedMp3: boolean;
  cachedMp4: boolean;
  cachedChapters: boolean;
  cachedAt: string;
  /** Entry i is songset item i's recording contentHash; null when the item has no recording. */
  chapterContentHashes: (string | null)[];
}

function isOfflineIndexAvailable(): boolean {
  return typeof window !== "undefined" && "indexedDB" in window && window.indexedDB !== null;
}

/** Opens (creating on first use) the sow-offline-index database. */
function openIndexDb(): Promise<IDBDatabase> {
  const { promise, resolve, reject } = Promise.withResolvers<IDBDatabase>();
  const request = window.indexedDB.open(OFFLINE_INDEX_DB_NAME, OFFLINE_INDEX_DB_VERSION);

  request.onupgradeneeded = (event) => {
    const db = (event.target as IDBOpenDBRequest).result as IDBDatabase;
    if (!db.objectStoreNames.contains(OFFLINE_INDEX_STORE_NAME)) {
      db.createObjectStore(OFFLINE_INDEX_STORE_NAME, { keyPath: "songsetId" });
    }
  };
  request.onsuccess = () => resolve(request.result);
  request.onerror = () => reject(request.error ?? new Error("IndexedDB open failed"));
  return promise;
}

/**
 * Runs fn against the index DB; resolves null on unavailability or any
 * IndexedDB failure (silent-failure convention). The db is closed after fn
 * settles.
 */
async function withIndexDb<T>(fn: (db: IDBDatabase) => Promise<T>): Promise<T | null> {
  if (!isOfflineIndexAvailable()) return null;
  try {
    const db = await openIndexDb();
    try {
      return await fn(db);
    } finally {
      db.close();
    }
  } catch {
    return null;
  }
}

/** Resolves when the IDB request succeeds; rejects on error. */
function requestDone(request: IDBRequest): Promise<void> {
  const { promise, resolve, reject } = Promise.withResolvers<void>();
  request.onsuccess = () => resolve();
  request.onerror = () => reject(request.error ?? new Error("IndexedDB request failed"));
  return promise;
}

/** Returns the offline record for a songset, or null when absent/unavailable. */
export async function getOfflineRecord(songsetId: string): Promise<OfflineSongsetRecord | null> {
  return withIndexDb(async (db) => {
    const request = db
      .transaction(OFFLINE_INDEX_STORE_NAME, "readonly")
      .objectStore(OFFLINE_INDEX_STORE_NAME)
      .get(songsetId);
    const { promise, resolve, reject } = Promise.withResolvers<OfflineSongsetRecord | undefined>();
    request.onsuccess = () => resolve(request.result as OfflineSongsetRecord | undefined);
    request.onerror = () => reject(request.error ?? new Error("IndexedDB get failed"));
    const result = await promise;
    return result ?? null;
  });
}

/** Returns every offline record (empty when none or unavailable). */
export async function listOfflineRecords(): Promise<OfflineSongsetRecord[]> {
  const records = await withIndexDb(async (db) => {
    const request = db
      .transaction(OFFLINE_INDEX_STORE_NAME, "readonly")
      .objectStore(OFFLINE_INDEX_STORE_NAME)
      .getAll();
    const { promise, resolve, reject } = Promise.withResolvers<OfflineSongsetRecord[]>();
    request.onsuccess = () => resolve((request.result as OfflineSongsetRecord[]) ?? []);
    request.onerror = () => reject(request.error ?? new Error("IndexedDB getAll failed"));
    return await promise;
  });
  return records ?? [];
}

/**
 * Writes the songset's offline record. Before opening the readwrite
 * transaction, any prior record with a different renderJobId gets its
 * artifact cache entries invalidated (supersede eviction). The prior read
 * MUST complete before the transaction opens: real IndexedDB auto-commits
 * a transaction left without pending requests across an await. Resolves
 * silently when IndexedDB is unavailable.
 */
export async function putOfflineRecord(record: OfflineSongsetRecord): Promise<void> {
  const prior = await getOfflineRecord(record.songsetId);
  if (prior && prior.renderJobId !== record.renderJobId) {
    await invalidateArtifactCache(prior.renderJobId);
  }

  await withIndexDb(async (db) => {
    const request = db
      .transaction(OFFLINE_INDEX_STORE_NAME, "readwrite")
      .objectStore(OFFLINE_INDEX_STORE_NAME)
      .put(record);
    await requestDone(request);
    return undefined;
  });
}

/**
 * Deletes a songset's offline record and invalidates its artifacts. The
 * prior read (for the renderJobId to evict) MUST complete before the
 * transaction opens: real IndexedDB auto-commits a transaction left
 * without pending requests across an await. Resolves silently when
 * IndexedDB is unavailable.
 */
export async function removeOfflineSongset(songsetId: string): Promise<void> {
  const prior = await getOfflineRecord(songsetId);
  if (prior) {
    await invalidateArtifactCache(prior.renderJobId);
  }

  await withIndexDb(async (db) => {
    const request = db
      .transaction(OFFLINE_INDEX_STORE_NAME, "readwrite")
      .objectStore(OFFLINE_INDEX_STORE_NAME)
      .delete(songsetId);
    await requestDone(request);
    return undefined;
  });
}
