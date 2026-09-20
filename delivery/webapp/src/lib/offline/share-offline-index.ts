/**
 * Share offline index (issue #218 PR2, ADR-0009): one IndexedDB store
 * recording which share tokens have cached artifacts for offline playback.
 *
 * Distinct semantics from the owner's songset index (src/lib/offline/
 * offline-index.ts):
 *
 * - Token-scoped, in its OWN database — never in the songsetId-keyed owner
 *   index. A logged-in owner who downloads both stores the media twice;
 *   the duplication is accepted so owner and share copies cannot clobber
 *   each other's render-pinned records.
 * - A copy is a frozen snapshot of the renderJobId at download time: token
 *   revocation/expiry does not wipe it, and nothing ever evicts a prior
 *   share copy's artifacts on re-download (the landing page's Re-download
 *   supersedes the record; the old artifact entries are invalidated then).
 *
 * Every operation degrades to a no-throw no-op when IndexedDB is
 * unavailable — mirroring the owner index's silent-failure convention.
 */

import { invalidateArtifactCache } from "./artifact-cache";
import { deleteShareControllerDocument } from "./document-cache";

export const SHARE_OFFLINE_INDEX_DB_NAME = "sow-share-offline-index";
export const SHARE_OFFLINE_INDEX_STORE_NAME = "shares";
const SHARE_OFFLINE_INDEX_DB_VERSION = 1;

export interface OfflineShareRecord {
  /** The share token — the record's key. Revocation/expiry never wipes it. */
  token: string;
  /** The renderJobId frozen at download time. */
  renderJobId: string;
  songsetName: string;
  cachedMp3: boolean;
  cachedMp4: boolean;
  cachedChapters: boolean;
  cachedAt: string;
  /** Entry i is songset item i's recording contentHash; null when the item has no recording. */
  chapterContentHashes: (string | null)[];
}

function isIndexAvailable(): boolean {
  return typeof window !== "undefined" && "indexedDB" in window && window.indexedDB !== null;
}

/** Opens (creating on first use) the sow-share-offline-index database. */
function openIndexDb(): Promise<IDBDatabase> {
  const { promise, resolve, reject } = Promise.withResolvers<IDBDatabase>();
  const request = window.indexedDB.open(
    SHARE_OFFLINE_INDEX_DB_NAME,
    SHARE_OFFLINE_INDEX_DB_VERSION
  );

  request.onupgradeneeded = (event) => {
    const db = (event.target as IDBOpenDBRequest).result as IDBDatabase;
    if (!db.objectStoreNames.contains(SHARE_OFFLINE_INDEX_STORE_NAME)) {
      db.createObjectStore(SHARE_OFFLINE_INDEX_STORE_NAME, { keyPath: "token" });
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
  if (!isIndexAvailable()) return null;
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

/** Returns the share's offline record, or null when absent/unavailable. */
export async function getShareOfflineRecord(token: string): Promise<OfflineShareRecord | null> {
  return withIndexDb(async (db) => {
    const request = db
      .transaction(SHARE_OFFLINE_INDEX_STORE_NAME, "readonly")
      .objectStore(SHARE_OFFLINE_INDEX_STORE_NAME)
      .get(token);
    const { promise, resolve, reject } = Promise.withResolvers<OfflineShareRecord | undefined>();
    request.onsuccess = () => resolve(request.result as OfflineShareRecord | undefined);
    request.onerror = () => reject(request.error ?? new Error("IndexedDB get failed"));
    const result = await promise;
    return result ?? null;
  });
}

/**
 * Writes the share's offline record. Before opening the readwrite
 * transaction, any prior record with a different renderJobId gets its
 * artifact cache entries invalidated (supersede eviction — the Re-download
 * path). The prior read MUST complete before the transaction opens: real
 * IndexedDB auto-commits a transaction left without pending requests across
 * an await. Resolves silently when IndexedDB is unavailable.
 */
export async function putShareOfflineRecord(record: OfflineShareRecord): Promise<void> {
  const prior = await getShareOfflineRecord(record.token);
  if (prior && prior.renderJobId !== record.renderJobId) {
    await invalidateArtifactCache(prior.renderJobId);
    await deleteShareControllerDocument(record.token);
  }

  await withIndexDb(async (db) => {
    const request = db
      .transaction(SHARE_OFFLINE_INDEX_STORE_NAME, "readwrite")
      .objectStore(SHARE_OFFLINE_INDEX_STORE_NAME)
      .put(record);
    await requestDone(request);
    return undefined;
  });
}

/**
 * Deletes a share's offline record and invalidates its artifacts. The prior
 * read (for the renderJobId to evict) MUST complete before the transaction
 * opens: real IndexedDB auto-commits a transaction left without pending
 * requests across an await. Resolves silently when IndexedDB is unavailable.
 */
export async function removeShareOfflineRecord(token: string): Promise<void> {
  const prior = await getShareOfflineRecord(token);
  if (prior) {
    await invalidateArtifactCache(prior.renderJobId);
    await deleteShareControllerDocument(token);
  }

  await withIndexDb(async (db) => {
    const request = db
      .transaction(SHARE_OFFLINE_INDEX_STORE_NAME, "readwrite")
      .objectStore(SHARE_OFFLINE_INDEX_STORE_NAME)
      .delete(token);
    await requestDone(request);
    return undefined;
  });
}
