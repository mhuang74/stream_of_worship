/**
 * Shared offline download helper (issue #203): the single code path any
 * surface that downloads a songset for offline use goes through. Extracted
 * from OfflineStatus's download body; auto-cache (issue #202) reuses it.
 *
 * Steps: fetch the proxy URLs (and per-chapter content hashes) from
 * /api/offline/cache, store the artifacts in Cache Storage via
 * cacheArtifacts (throws on failure — callers own toasting), then write the
 * offline-index record (best-effort; a failing index write must not fail
 * the download itself).
 */

import {
  cacheArtifacts,
  requestPersistentStorage,
  type CacheableArtifacts,
} from "./artifact-cache";
import { putOfflineRecord, type OfflineSongsetRecord } from "./offline-index";
import { cacheControllerDocument, cacheOfflineListDocument } from "./document-cache";

export class NoArtifactsError extends Error {
  constructor() {
    super("No artifacts available to cache");
    this.name = "NoArtifactsError";
  }
}

export interface DownloadOfflineInput {
  songsetId: string;
  songsetName: string;
  renderJobId: string;
}

export type DownloadProgressCallback = (percent: number) => void;

interface ProxyUrlResponse {
  mp3Url?: string | null;
  mp4Url?: string | null;
  chaptersUrl?: string | null;
  chapterContentHashes?: (string | null)[] | null;
}

async function fetchProxyResponse(renderJobId: string): Promise<ProxyUrlResponse> {
  const apiUrl = `/api/offline/cache?renderJobId=${encodeURIComponent(renderJobId)}`;
  const response = await fetch(apiUrl);

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}) as Record<string, unknown>);
    const message =
      typeof errorData.error === "string" ? errorData.error : "Failed to get download URLs";
    throw new Error(message);
  }

  return (await response.json()) as ProxyUrlResponse;
}

/**
 * Downloads and caches a songset's rendered artifacts, then records the
 * offline index entry. Throws when Cache Storage is unavailable, the API
 * call fails, or every artifact URL is missing (NoArtifactsError) — before
 * any index write. onProgress(0–100) forwards cacheArtifacts progress.
 */
export async function downloadOfflineArtifacts(
  input: DownloadOfflineInput,
  onProgress?: DownloadProgressCallback
): Promise<void> {
  const proxyUrls = await fetchProxyResponse(input.renderJobId);
  const artifacts: CacheableArtifacts = {
    mp3Url: proxyUrls.mp3Url,
    mp4Url: proxyUrls.mp4Url,
    chaptersUrl: proxyUrls.chaptersUrl,
  };

  if (!artifacts.mp3Url && !artifacts.mp4Url && !artifacts.chaptersUrl) {
    throw new NoArtifactsError();
  }

  await requestPersistentStorage();
  await cacheArtifacts(input.renderJobId, artifacts, onProgress);

  const record: OfflineSongsetRecord = {
    songsetId: input.songsetId,
    renderJobId: input.renderJobId,
    songsetName: input.songsetName,
    cachedMp3: Boolean(artifacts.mp3Url),
    cachedMp4: Boolean(artifacts.mp4Url),
    cachedChapters: Boolean(artifacts.chaptersUrl),
    cachedAt: new Date().toISOString(),
    chapterContentHashes: Array.isArray(proxyUrls.chapterContentHashes)
      ? proxyUrls.chapterContentHashes
      : [],
  };

  try {
    await putOfflineRecord(record);
  } catch {
    // Index write is bookkeeping: the artifacts are already cached, so a
    // failing IndexedDB write must not fail the download.
  }

  // Pre-cache the controller document + its assets (issue #206): the offline
  // Start Worship tap is a full document navigation, which needs the HTML and
  // its scripts/styles already in the SW's sow-pages cache. Best-effort —
  // playback works without it, so a failure here degrades the cold start to
  // the offline fallback page rather than failing a download whose artifacts
  // are already in place.
  await cacheControllerDocument(input.songsetId).catch(() => {});
  await cacheOfflineListDocument().catch(() => {});
}
