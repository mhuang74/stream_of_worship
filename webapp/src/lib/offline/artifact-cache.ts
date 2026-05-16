export const ARTIFACT_CACHE_NAME = "sow-artifacts";
export const WARN_STORAGE_BYTES = 500 * 1024 * 1024; // 500 MB
export const HARD_LIMIT_BYTES = 1024 * 1024 * 1024; // 1 GB

export interface CacheableArtifacts {
  mp3Url?: string | null;
  mp4Url?: string | null;
  chaptersUrl?: string | null;
}

export interface ArtifactCacheStatus {
  isCached: boolean;
  renderJobId: string;
}

export interface StorageBudget {
  usageBytes: number;
  quotaBytes: number;
  isWarning: boolean;
  isOverLimit: boolean;
}

// Stable, non-expiring cache key derived from render_job_id — not a real URL.
function artifactCacheKey(renderJobId: string, type: "mp3" | "mp4" | "chapters"): string {
  return `/sow-artifact-cache/${renderJobId}/${type}`;
}

/**
 * Returns true if offline caching is supported on the current device.
 * On iOS, requires 17.4+; all other platforms are always supported.
 */
export function isOfflineSupportedOnCurrentDevice(): boolean {
  if (typeof navigator === "undefined") return false;

  const ua = navigator.userAgent;
  const isIOS = /iPad|iPhone|iPod/.test(ua);
  if (!isIOS) return true;

  const match = ua.match(/OS (\d+)_(\d+)/);
  if (!match) return false;

  const major = parseInt(match[1], 10);
  const minor = parseInt(match[2], 10);
  return major > 17 || (major === 17 && minor >= 4);
}

/**
 * Requests persistent storage to prevent cache eviction.
 * Should be called on the first cache action (iOS 17.4+).
 */
export async function requestPersistentStorage(): Promise<boolean> {
  if (typeof navigator === "undefined") return false;
  if (!("storage" in navigator) || !("persist" in navigator.storage)) return false;

  try {
    return await navigator.storage.persist();
  } catch {
    return false;
  }
}

/** Returns current storage usage/quota with warning and hard-limit flags. */
export async function getStorageBudget(): Promise<StorageBudget> {
  const empty: StorageBudget = {
    usageBytes: 0,
    quotaBytes: 0,
    isWarning: false,
    isOverLimit: false,
  };

  if (typeof navigator === "undefined") return empty;
  if (!("storage" in navigator) || !("estimate" in navigator.storage)) return empty;

  try {
    const estimate = await navigator.storage.estimate();
    const usageBytes = estimate.usage ?? 0;
    const quotaBytes = estimate.quota ?? 0;

    return {
      usageBytes,
      quotaBytes,
      isWarning: usageBytes >= WARN_STORAGE_BYTES,
      isOverLimit: usageBytes >= HARD_LIMIT_BYTES,
    };
  } catch {
    return empty;
  }
}

/** Returns true if the render job's primary artifact (mp3, then mp4) is cached. */
export async function getArtifactCacheStatus(
  renderJobId: string,
  artifacts: CacheableArtifacts
): Promise<ArtifactCacheStatus> {
  if (typeof window === "undefined" || !("caches" in window)) {
    return { isCached: false, renderJobId };
  }

  try {
    const cache = await caches.open(ARTIFACT_CACHE_NAME);

    if (artifacts.mp3Url) {
      const hit = await cache.match(artifactCacheKey(renderJobId, "mp3"));
      return { isCached: !!hit, renderJobId };
    }

    if (artifacts.mp4Url) {
      const hit = await cache.match(artifactCacheKey(renderJobId, "mp4"));
      return { isCached: !!hit, renderJobId };
    }

    return { isCached: false, renderJobId };
  } catch {
    return { isCached: false, renderJobId };
  }
}

/**
 * Downloads and stores artifacts under stable render-job-based cache keys.
 * Throws if Cache Storage is unavailable or the storage hard limit is reached.
 * Calls onProgress(0–100) after each artifact is stored.
 */
export async function cacheArtifacts(
  renderJobId: string,
  artifacts: CacheableArtifacts,
  onProgress?: (percent: number) => void
): Promise<void> {
  if (typeof window === "undefined" || !("caches" in window) || !caches) {
    throw new Error("Cache Storage API not available");
  }

  const budget = await getStorageBudget();
  if (budget.isOverLimit) {
    throw new Error("Storage limit exceeded (1 GB). Please free up space first.");
  }

  const work: Array<{ url: string; key: string }> = [];
  if (artifacts.mp3Url) {
    work.push({ url: artifacts.mp3Url, key: artifactCacheKey(renderJobId, "mp3") });
  }
  if (artifacts.mp4Url) {
    work.push({ url: artifacts.mp4Url, key: artifactCacheKey(renderJobId, "mp4") });
  }
  if (artifacts.chaptersUrl) {
    work.push({ url: artifacts.chaptersUrl, key: artifactCacheKey(renderJobId, "chapters") });
  }

  if (work.length === 0) {
    throw new Error("No artifacts to cache");
  }

  const cache = await caches.open(ARTIFACT_CACHE_NAME);

  for (let i = 0; i < work.length; i++) {
    const { url, key } = work[i];
    const response = await fetch(url);

    if (!response.ok) {
      throw new Error(`Failed to fetch artifact: ${url} (${response.status})`);
    }

    await cache.put(key, response);
    onProgress?.(Math.round(((i + 1) / work.length) * 100));
  }
}

/** Removes all cached artifacts for the given render job. */
export async function invalidateArtifactCache(renderJobId: string): Promise<void> {
  if (typeof window === "undefined" || !("caches" in window)) return;

  try {
    const cache = await caches.open(ARTIFACT_CACHE_NAME);
    await Promise.all([
      cache.delete(artifactCacheKey(renderJobId, "mp3")),
      cache.delete(artifactCacheKey(renderJobId, "mp4")),
      cache.delete(artifactCacheKey(renderJobId, "chapters")),
    ]);
  } catch {
    // Silently ignore cache deletion errors
  }
}
