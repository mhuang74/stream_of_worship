/**
 * Offline playback boot (issue #205): decides what the controller can play
 * when the API chain is unavailable — from the offline index (#203) plus the
 * artifact cache, with no network fetches of its own.
 *
 * Two things the controller needs beyond "is there a record":
 *
 * - Which artifact actually serves the media. A cached MP4 wins; an MP3-only
 *   render boots as audio playback. Cache Storage is the source of truth —
 *   the index record's `cached*` flags are download-time bookkeeping and may
 *   be stale.
 * - Which URL to hand the media element. The artifact proxy URL is preferred:
 *   the service worker (#204) answers it from Cache Storage with Range
 *   support, so a hundreds-of-MB MP4 seeks without being held in memory. When
 *   nothing controls the document the proxy URL would hit the network and
 *   fail, so the cached bytes are exposed as a blob URL instead — the
 *   pre-decided fallback promoted to a deterministic path.
 *
 * Chapters are per-artifact best-effort: a missing or unparseable cached
 * manifest yields no chapters, never a failed media boot.
 */

import { normalizeChaptersManifest, type Chapter } from "@/lib/render/chapters";
import { matchCachedArtifact } from "./artifact-cache";
import { getOfflineRecord } from "./offline-index";

export type OfflineMediaKind = "video" | "audio";

export interface OfflinePlayback {
  renderJobId: string;
  songsetName: string;
  kind: OfflineMediaKind;
  /** Media element source: the artifact proxy URL, or a blob URL of the cached bytes. */
  src: string;
  /**
   * True while `src` is the artifact proxy URL (served by the service worker
   * from Cache Storage). False when it is already a blob URL — the media
   * element then owns a buffer of the whole artifact, so there is no cheaper
   * fallback left.
   */
  viaProxy: boolean;
  chapters: Chapter[];
  /** Position-aligned recording content hashes: the Lyrics Feedback key. */
  chapterRecordingHashes: (string | null)[];
}

const FILE_NAMES: Record<OfflineMediaKind, string> = {
  video: "output.mp4",
  audio: "output.mp3",
};

/**
 * Artifact proxy URL. The service worker maps this exact shape onto the cache
 * key the download path wrote (see public/sw-artifact-serving.js).
 */
function artifactProxyUrl(renderJobId: string, kind: OfflineMediaKind): string {
  return `/api/r2/artifact/${renderJobId}/${FILE_NAMES[kind]}`;
}

/** True when a service worker controls this document (its artifact route serves the proxy URL). */
function isServiceWorkerControlling(): boolean {
  return typeof navigator !== "undefined" && navigator.serviceWorker?.controller != null;
}

async function blobUrlFor(cached: Response): Promise<string | null> {
  try {
    return URL.createObjectURL(await cached.blob());
  } catch {
    return null;
  }
}

/**
 * Blob URL over a cached artifact's bytes. Used as the media element's
 * fallback when a proxy URL fails to play — the controller re-issues the load
 * against this instead of showing the failure overlay.
 */
export async function createOfflineBlobUrl(
  renderJobId: string,
  kind: OfflineMediaKind
): Promise<string | null> {
  const cached = await matchCachedArtifact(renderJobId, kind === "video" ? "mp4" : "mp3");
  return cached ? blobUrlFor(cached) : null;
}

/** Cached chapters manifest; empty when it was never cached or fails to parse. */
async function cachedChapters(renderJobId: string): Promise<Chapter[]> {
  const cached = await matchCachedArtifact(renderJobId, "chapters");
  if (!cached) return [];

  try {
    return normalizeChaptersManifest(await cached.json()).chapters;
  } catch {
    return [];
  }
}

async function sourceFor(
  renderJobId: string,
  kind: OfflineMediaKind,
  cached: Response
): Promise<{ src: string; viaProxy: boolean } | null> {
  if (isServiceWorkerControlling()) {
    return { src: artifactProxyUrl(renderJobId, kind), viaProxy: true };
  }

  const src = await blobUrlFor(cached);
  return src ? { src, viaProxy: false } : null;
}

/**
 * Resolves offline playback for a songset, or null when it cannot be played
 * offline (no index record, no cached media, or no way to serve the bytes).
 */
export async function resolveOfflinePlayback(
  songsetId: string
): Promise<OfflinePlayback | null> {
  const record = await getOfflineRecord(songsetId);
  if (!record) return null;

  const video = await matchCachedArtifact(record.renderJobId, "mp4");
  const audio = video ? null : await matchCachedArtifact(record.renderJobId, "mp3");
  const cached = video ?? audio;
  if (!cached) return null;

  const source = await sourceFor(record.renderJobId, video ? "video" : "audio", cached);
  if (!source) return null;

  return {
    renderJobId: record.renderJobId,
    songsetName: record.songsetName,
    kind: video ? "video" : "audio",
    src: source.src,
    viaProxy: source.viaProxy,
    chapters: await cachedChapters(record.renderJobId),
    chapterRecordingHashes: record.chapterContentHashes.map((hash) => hash ?? null),
  };
}
