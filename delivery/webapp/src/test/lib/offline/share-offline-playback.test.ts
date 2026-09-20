import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { resolveShareOfflinePlayback } from "@/lib/offline/offline-playback";
import { getShareOfflineRecord } from "@/lib/offline/share-offline-index";
import type { OfflineShareRecord } from "@/lib/offline/share-offline-index";
import type { Chapter } from "@/lib/render/chapters";

vi.mock("@/lib/offline/share-offline-index", () => ({
  getShareOfflineRecord: vi.fn(),
}));

const mockGetShareRecord = vi.mocked(getShareOfflineRecord);

// Real artifact-cache + stubbed Cache Storage: the resolver test proves the
// record → artifact cache → media source chain. The record comes from the
// (mocked) token-keyed share index; the bytes from the artifact cache.
function setServiceWorkerControlling(controlling: boolean) {
  Object.defineProperty(navigator, "serviceWorker", {
    value: controlling ? { controller: { scriptURL: "/sw.js" } } : undefined,
    configurable: true,
  });
}

function installArtifactCache(bodies: { mp4?: string; mp3?: string; chapters?: unknown }) {
  const entries = new Map<string, Response>();
  if (bodies.mp4 !== undefined) {
    entries.set("/sow-artifact-cache/job-1/mp4", new Response(bodies.mp4));
  }
  if (bodies.mp3 !== undefined) {
    entries.set("/sow-artifact-cache/job-1/mp3", new Response(bodies.mp3));
  }
  if (bodies.chapters !== undefined) {
    entries.set("/sow-artifact-cache/job-1/chapters", Response.json(bodies.chapters));
  }
  Object.defineProperty(window, "caches", {
    value: {
      open: () =>
        Promise.resolve({
          match: (key: string) => Promise.resolve(entries.get(key)),
        }),
    },
    configurable: true,
  });
}

function makeRecord(overrides: Partial<OfflineShareRecord> = {}): OfflineShareRecord {
  return {
    token: "tok-1",
    renderJobId: "job-1",
    songsetName: "Shared Set",
    cachedMp3: true,
    cachedMp4: true,
    cachedChapters: true,
    cachedAt: "2026-09-20T10:00:00.000Z",
    chapterContentHashes: ["hash-a"],
    ...overrides,
  };
}

describe("resolveShareOfflinePlayback", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    installArtifactCache({});
    setServiceWorkerControlling(true);
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

  it("resolves null when the share index has no record", async () => {
    mockGetShareRecord.mockResolvedValue(null);
    installArtifactCache({ mp4: "video-bytes" });

    expect(await resolveShareOfflinePlayback("tok-1")).toBeNull();
  });

  it("resolves null when the record exists but the cached bytes are gone", async () => {
    mockGetShareRecord.mockResolvedValue(makeRecord());
    installArtifactCache({});

    expect(await resolveShareOfflinePlayback("tok-1")).toBeNull();
  });

  it("resolves the cached MP4 with the proxy URL, cached chapters, and the frozen renderJobId", async () => {
    mockGetShareRecord.mockResolvedValue(makeRecord());
    installArtifactCache({
      mp4: "video-bytes",
      chapters: {
        chapters: [
          { position: 0, songTitle: "S", startSeconds: 0, endSeconds: 100, lines: [] },
        ] satisfies Chapter[],
      },
    });

    const resolved = await resolveShareOfflinePlayback("tok-1");
    expect(resolved).toMatchObject({
      renderJobId: "job-1",
      songsetName: "Shared Set",
      kind: "video",
      src: "/api/r2/artifact/job-1/output.mp4",
      viaProxy: true,
      chapterRecordingHashes: ["hash-a"],
    });
    expect(resolved?.chapters).toHaveLength(1);
  });

  it("boots audio when only the MP3 was cached", async () => {
    mockGetShareRecord.mockResolvedValue(makeRecord({ cachedMp4: false }));
    installArtifactCache({ mp3: "audio-bytes" });

    const resolved = await resolveShareOfflinePlayback("tok-1");
    expect(resolved).toMatchObject({
      kind: "audio",
      src: "/api/r2/artifact/job-1/output.mp3",
    });
  });

  it("reads the token-keyed share index only — never the songsetId-keyed owner index", async () => {
    // The owner index is a different DB entirely; the share resolver must
    // never see its records (ADR-0009 namespace separation).
    mockGetShareRecord.mockResolvedValue(null);
    installArtifactCache({ mp4: "video-bytes" });

    expect(await resolveShareOfflinePlayback("tok-1")).toBeNull();
  });
});
