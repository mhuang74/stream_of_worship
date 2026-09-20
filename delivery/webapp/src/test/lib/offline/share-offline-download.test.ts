import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  downloadShareOfflineArtifacts,
  ShareNoArtifactsError,
} from "@/lib/offline/download-share-offline";
import { putShareOfflineRecord } from "@/lib/offline/share-offline-index";
import {
  cacheArtifacts,
  requestPersistentStorage,
} from "@/lib/offline/artifact-cache";

// The share download path's index/document writes are separate modules with
// their own tests; this file pins the download wiring.
vi.mock("@/lib/offline/artifact-cache", () => ({
  requestPersistentStorage: vi.fn().mockResolvedValue(true),
  cacheArtifacts: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("@/lib/offline/share-offline-index", () => ({
  putShareOfflineRecord: vi.fn().mockResolvedValue(undefined),
}));

const { mockCacheShareControllerDocument } = vi.hoisted(() => ({
  mockCacheShareControllerDocument: vi.fn().mockResolvedValue(true),
}));
vi.mock("@/lib/offline/document-cache", () => ({
  cacheShareControllerDocument: mockCacheShareControllerDocument,
}));

const mockedCacheArtifacts = vi.mocked(cacheArtifacts);
const mockedRequestPersistentStorage = vi.mocked(requestPersistentStorage);
const mockedPutShareRecord = vi.mocked(putShareOfflineRecord);

const INPUT = {
  token: "tok-1",
  songsetName: "Shared Set",
  renderJobId: "job-1",
  mp4Url: "https://r2.example.com/output.mp4",
  chaptersUrl: "https://r2.example.com/chapters.json",
  chapterContentHashes: ["hash-a", null],
};

describe("downloadShareOfflineArtifacts", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("caches the presigned artifacts and records the share-scoped entry", async () => {
    await downloadShareOfflineArtifacts(INPUT);

    expect(mockedRequestPersistentStorage).toHaveBeenCalled();
    expect(mockedCacheArtifacts).toHaveBeenCalledWith(
      "job-1",
      { mp3Url: undefined, mp4Url: INPUT.mp4Url, chaptersUrl: INPUT.chaptersUrl },
      undefined
    );
    expect(mockedPutShareRecord).toHaveBeenCalledWith(
      expect.objectContaining({
        token: "tok-1",
        renderJobId: "job-1",
        songsetName: "Shared Set",
        cachedMp4: true,
        cachedChapters: true,
        chapterContentHashes: ["hash-a", null],
      })
    );
    // The share controller document is pre-cached with the copy (issue #206
    // machinery on the share path).
    expect(mockCacheShareControllerDocument).toHaveBeenCalledWith("tok-1");
  });

  it("throws ShareNoArtifactsError before any index write when no artifact URLs", async () => {
    await expect(
      downloadShareOfflineArtifacts({ ...INPUT, mp4Url: null, chaptersUrl: null })
    ).rejects.toBeInstanceOf(ShareNoArtifactsError);
    expect(mockedCacheArtifacts).not.toHaveBeenCalled();
    expect(mockedPutShareRecord).not.toHaveBeenCalled();
  });

  it("resolves even when the index write fails (artifacts already cached)", async () => {
    mockedPutShareRecord.mockRejectedValueOnce(new Error("idb down"));

    await expect(downloadShareOfflineArtifacts(INPUT)).resolves.toBeUndefined();
    expect(mockedCacheArtifacts).toHaveBeenCalled();
  });
});
