import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  createOfflineBlobUrl,
  resolveOfflinePlayback,
} from "@/lib/offline/offline-playback";
import type { OfflineSongsetRecord } from "@/lib/offline/offline-index";

vi.mock("@/lib/offline/offline-index", () => ({
  getOfflineRecord: vi.fn(),
}));

import { getOfflineRecord } from "@/lib/offline/offline-index";

const mockGetOfflineRecord = vi.mocked(getOfflineRecord);

type ArtifactType = "mp3" | "mp4" | "chapters";

const RECORD: OfflineSongsetRecord = {
  songsetId: "ss-1",
  renderJobId: "job-1",
  songsetName: "Sunday Set",
  cachedMp3: true,
  cachedMp4: true,
  cachedChapters: true,
  cachedAt: "2026-09-15T00:00:00.000Z",
  chapterContentHashes: ["hash-a", null, "hash-c"],
};

const CHAPTERS_MANIFEST = {
  chapters: [
    {
      position: 0,
      songTitle: "Amazing Grace",
      startSeconds: 0,
      endSeconds: 180,
      lines: [],
    },
  ],
  totalDurationSeconds: 180,
  generatedAt: "2026-09-15T00:00:00.000Z",
};

/** Cache Storage stub holding artifact bodies under artifact-cache's keys. */
function installArtifactCache(bodies: Partial<Record<ArtifactType, unknown>>) {
  const entries = new Map<string, Response>();
  for (const [type, body] of Object.entries(bodies)) {
    entries.set(
      `/sow-artifact-cache/job-1/${type}`,
      typeof body === "string" ? new Response(body) : Response.json(body)
    );
  }

  Object.defineProperty(window, "caches", {
    value: {
      open: () => Promise.resolve({ match: (key: string) => Promise.resolve(entries.get(key)) }),
    },
    configurable: true,
  });
}

function installServiceWorker(controlling: boolean) {
  Object.defineProperty(navigator, "serviceWorker", {
    value: controlling ? { controller: { scriptURL: "/sw.js" } } : undefined,
    configurable: true,
  });
}

describe("resolveOfflinePlayback", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetOfflineRecord.mockResolvedValue(RECORD);
    installServiceWorker(true);
    installArtifactCache({});
    Object.defineProperty(URL, "createObjectURL", {
      value: vi.fn(() => "blob:cached-artifact"),
      configurable: true,
      writable: true,
    });
  });

  afterEach(() => {
    Reflect.deleteProperty(window, "caches");
    Reflect.deleteProperty(navigator, "serviceWorker");
    Reflect.deleteProperty(URL, "createObjectURL");
  });

  it("resolves nothing when the songset has no offline record", async () => {
    mockGetOfflineRecord.mockResolvedValue(null);

    expect(await resolveOfflinePlayback("ss-1")).toBeNull();
  });

  it("boots the cached MP4 through the artifact proxy URL", async () => {
    installArtifactCache({ mp4: "video-bytes", chapters: CHAPTERS_MANIFEST });

    expect(await resolveOfflinePlayback("ss-1")).toEqual({
      renderJobId: "job-1",
      songsetName: "Sunday Set",
      kind: "video",
      src: "/api/r2/artifact/job-1/output.mp4",
      viaProxy: true,
      chapters: CHAPTERS_MANIFEST.chapters,
      chapterRecordingHashes: ["hash-a", null, "hash-c"],
    });
  });

  it("boots audio playback when only the MP3 is cached", async () => {
    installArtifactCache({ mp3: "audio-bytes" });

    const playback = await resolveOfflinePlayback("ss-1");

    expect(playback?.kind).toBe("audio");
    expect(playback?.src).toBe("/api/r2/artifact/job-1/output.mp3");
  });

  it("resolves nothing when the record's artifacts are gone from the cache", async () => {
    // The record's cached* flags are download-time bookkeeping; Cache Storage
    // is what actually plays.
    expect(await resolveOfflinePlayback("ss-1")).toBeNull();
  });

  it("uses a blob URL over the cached bytes when no service worker controls the document", async () => {
    installServiceWorker(false);
    installArtifactCache({ mp4: "video-bytes" });

    const playback = await resolveOfflinePlayback("ss-1");

    expect(playback?.src).toBe("blob:cached-artifact");
    expect(playback?.viaProxy).toBe(false);
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
  });

  it("boots media even when the cached chapters manifest cannot be parsed", async () => {
    installArtifactCache({ mp4: "video-bytes", chapters: "{ not json" });

    const playback = await resolveOfflinePlayback("ss-1");

    expect(playback?.src).toBe("/api/r2/artifact/job-1/output.mp4");
    expect(playback?.chapters).toEqual([]);
  });

  it("skips chapters that were never cached", async () => {
    installArtifactCache({ mp4: "video-bytes" });

    expect((await resolveOfflinePlayback("ss-1"))?.chapters).toEqual([]);
  });
});

describe("createOfflineBlobUrl", () => {
  beforeEach(() => {
    installArtifactCache({});
    Object.defineProperty(URL, "createObjectURL", {
      value: vi.fn(() => "blob:cached-artifact"),
      configurable: true,
      writable: true,
    });
  });

  afterEach(() => {
    Reflect.deleteProperty(window, "caches");
    Reflect.deleteProperty(URL, "createObjectURL");
  });

  it("builds a blob URL from the cached artifact bytes", async () => {
    installArtifactCache({ mp4: "video-bytes" });

    expect(await createOfflineBlobUrl("job-1", "video")).toBe("blob:cached-artifact");
  });

  it("resolves null when the artifact is not cached", async () => {
    expect(await createOfflineBlobUrl("job-1", "video")).toBeNull();
  });
});
