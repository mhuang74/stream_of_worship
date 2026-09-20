/**
 * Share offline download (issue #218 PR2, ADR-0009): the single code path
 * the share landing page's Download button goes through.
 *
 * Mirrors download-offline.ts (the owner path) with share semantics:
 *
 * - The source URLs are the share API's presigned R2 URLs — no authenticated
 *   proxy fetch exists on the anonymous share path (ADR-0009 introduces no
 *   authenticated endpoints there).
 * - Artifacts land in the SAME sow-artifacts cache, under the SAME
 *   renderJobId-keyed entries the service worker serves — but the INDEX
 *   record is share-scoped (sow-share-offline-index), never the
 *   songsetId-keyed owner index. Owner and share copies coexist; where both
 *   pin the same renderJobId the bytes are shared, not duplicated.
 * - A copy is a frozen snapshot: the renderJobId resolved at download time
 *   is pinned in the record and never re-resolved.
 */

import {
  cacheArtifacts,
  requestPersistentStorage,
  type CacheableArtifacts,
} from "./artifact-cache";
import { putShareOfflineRecord, type OfflineShareRecord } from "./share-offline-index";
import { cacheShareControllerDocument } from "./document-cache";

export class ShareNoArtifactsError extends Error {
  constructor() {
    super("Share render has no downloadable artifacts");
    this.name = "ShareNoArtifactsError";
  }
}

export interface DownloadShareOfflineInput {
  token: string;
  songsetName: string;
  renderJobId: string;
  /** Presigned URLs from the share API response (playback.*Url). */
  mp3Url?: string | null;
  mp4Url?: string | null;
  chaptersUrl?: string | null;
  chapterContentHashes?: (string | null)[] | null;
}

export type DownloadProgressCallback = (percent: number) => void;

/**
 * Downloads and caches a share render's artifacts, then records the
 * share-scoped index entry. Throws when Cache Storage is unavailable or
 * every artifact URL is missing (ShareNoArtifactsError) — before any index
 * write. onProgress(0–100) forwards cacheArtifacts progress.
 */
export async function downloadShareOfflineArtifacts(
  input: DownloadShareOfflineInput,
  onProgress?: DownloadProgressCallback
): Promise<void> {
  const artifacts: CacheableArtifacts = {
    mp3Url: input.mp3Url,
    mp4Url: input.mp4Url,
    chaptersUrl: input.chaptersUrl,
  };

  if (!artifacts.mp3Url && !artifacts.mp4Url && !artifacts.chaptersUrl) {
    throw new ShareNoArtifactsError();
  }

  await requestPersistentStorage();
  await cacheArtifacts(input.renderJobId, artifacts, onProgress);

  const record: OfflineShareRecord = {
    token: input.token,
    renderJobId: input.renderJobId,
    songsetName: input.songsetName,
    cachedMp3: Boolean(artifacts.mp3Url),
    cachedMp4: Boolean(artifacts.mp4Url),
    cachedChapters: Boolean(artifacts.chaptersUrl),
    cachedAt: new Date().toISOString(),
    chapterContentHashes: Array.isArray(input.chapterContentHashes)
      ? input.chapterContentHashes
      : [],
  };

  try {
    await putShareOfflineRecord(record);
  } catch {
    // Index write is bookkeeping: the artifacts are already cached, so a
    // failing IndexedDB write must not fail the download.
  }

  // Pre-cache the share controller document (issue #206 machinery, share
  // path): the offline tap is a full document navigation. Best-effort —
  // playback works without it, a failure here only degrades the cold start.
  try {
    await cacheShareControllerDocument(input.token);
  } catch {
    // Never fail the download on a document-warm failure.
  }
}
