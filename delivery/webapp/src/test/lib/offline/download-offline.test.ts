import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  downloadOfflineArtifacts,
  NoArtifactsError,
  type DownloadOfflineInput,
} from "@/lib/offline/download-offline";
import {
  cacheArtifacts,
  requestPersistentStorage,
} from "@/lib/offline/artifact-cache";
import { putOfflineRecord } from "@/lib/offline/offline-index";

vi.mock("@/lib/offline/artifact-cache", () => ({
  requestPersistentStorage: vi.fn().mockResolvedValue(true),
  cacheArtifacts: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("@/lib/offline/offline-index", () => ({
  putOfflineRecord: vi.fn().mockResolvedValue(undefined),
}));


const mockedCacheArtifacts = vi.mocked(cacheArtifacts);
const mockedPutOfflineRecord = vi.mocked(putOfflineRecord);

function makeInput(overrides: Partial<DownloadOfflineInput> = {}): DownloadOfflineInput {
  return {
    songsetId: "set-1",
    songsetName: "Sunday Worship",
    renderJobId: "job-1",
    ...overrides,
  };
}

function mockCacheApi() {
  const mockCache = {
    match: vi.fn().mockResolvedValue(null),
    put: vi.fn().mockResolvedValue(undefined),
    keys: vi.fn().mockResolvedValue([]),
    delete: vi.fn().mockResolvedValue(true),
  };
  Object.defineProperty(global, "caches", {
    value: { open: vi.fn().mockResolvedValue(mockCache) },
    writable: true,
    configurable: true,
  });
  return mockCache;
}

// --------------------------------------------------------------------------
// Success path
// --------------------------------------------------------------------------

describe("downloadOfflineArtifacts", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockCacheApi();

    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          renderJobId: "job-1",
          mp3Url: "/api/r2/artifact/job-1/output.mp3",
          mp4Url: "/api/r2/artifact/job-1/output.mp4",
          chaptersUrl: "/api/r2/artifact/job-1/chapters.json",
          chapterContentHashes: ["hash-a", "hash-b"],
        }),
    });
  });

  afterEach(() => vi.restoreAllMocks());

  it("requests persistent storage then fetches proxy URLs and caches artifacts", async () => {
    const onProgress = vi.fn();

    await downloadOfflineArtifacts(makeInput(), onProgress);

    expect(requestPersistentStorage).toHaveBeenCalled();
    expect(global.fetch).toHaveBeenCalledWith("/api/offline/cache?renderJobId=job-1");
    expect(mockedCacheArtifacts).toHaveBeenCalledWith(
      "job-1",
      {
        mp3Url: "/api/r2/artifact/job-1/output.mp3",
        mp4Url: "/api/r2/artifact/job-1/output.mp4",
        chaptersUrl: "/api/r2/artifact/job-1/chapters.json",
      },
      onProgress
    );
  });

  it("writes the offline index record with per-artifact flags and cachedAt", async () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date("2026-09-15T10:00:00.000Z"));

      await downloadOfflineArtifacts(makeInput());

      expect(mockedPutOfflineRecord).toHaveBeenCalledWith({
        songsetId: "set-1",
        renderJobId: "job-1",
        songsetName: "Sunday Worship",
        cachedMp3: true,
        cachedMp4: true,
        cachedChapters: true,
        cachedAt: "2026-09-15T10:00:00.000Z",
        chapterContentHashes: ["hash-a", "hash-b"],
      });
    } finally {
      vi.useRealTimers();
    }
  });

  it("flags only the artifacts the render job provides", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          renderJobId: "job-1",
          mp3Url: "/api/r2/artifact/job-1/output.mp3",
          mp4Url: null,
          chaptersUrl: null,
          chapterContentHashes: ["hash-a"],
        }),
    });

    await downloadOfflineArtifacts(makeInput());

    const record = mockedPutOfflineRecord.mock.calls[0][0];
    expect(record.cachedMp3).toBe(true);
    expect(record.cachedMp4).toBe(false);
    expect(record.cachedChapters).toBe(false);
  });
});

// --------------------------------------------------------------------------
// Failure paths
// --------------------------------------------------------------------------

describe("downloadOfflineArtifacts failures", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockCacheApi();
  });

  afterEach(() => vi.restoreAllMocks());

  it("throws NoArtifactsError when the response has no usable URLs", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          renderJobId: "job-1",
          mp3Url: null,
          mp4Url: null,
          chaptersUrl: null,
          chapterContentHashes: [],
        }),
    });

    await expect(downloadOfflineArtifacts(makeInput())).rejects.toBeInstanceOf(NoArtifactsError);
    expect(mockedCacheArtifacts).not.toHaveBeenCalled();
    expect(mockedPutOfflineRecord).not.toHaveBeenCalled();
  });

  it("throws a fetch error with the API's error message", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      json: () => Promise.resolve({ error: "Render job not found" }),
    });

    await expect(downloadOfflineArtifacts(makeInput())).rejects.toThrow("Render job not found");
    expect(mockedCacheArtifacts).not.toHaveBeenCalled();
  });

  it("propagates cacheArtifacts failures and skips the index write", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          renderJobId: "job-1",
          mp3Url: "/api/r2/artifact/job-1/output.mp3",
          mp4Url: null,
          chaptersUrl: null,
          chapterContentHashes: [],
        }),
    });

    mockedCacheArtifacts.mockRejectedValueOnce(new Error("Network error"));

    await expect(downloadOfflineArtifacts(makeInput())).rejects.toThrow("Network error");
    expect(mockedPutOfflineRecord).not.toHaveBeenCalled();
  });

  it("completes without throwing when the index write fails", async () => {
    mockedPutOfflineRecord.mockRejectedValueOnce(new Error("IDB unavailable"));

    await expect(downloadOfflineArtifacts(makeInput())).resolves.toBeUndefined();
    expect(mockedCacheArtifacts).toHaveBeenCalled();
  });
});
