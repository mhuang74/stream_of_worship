import { describe, it, expect, vi, beforeEach } from "vitest";

/* eslint-disable @typescript-eslint/no-explicit-any */

vi.mock("@/db", () => {
  const createChain = (resolveWith: any[] = []) => ({
    from: vi.fn().mockReturnThis(),
    leftJoin: vi.fn().mockReturnThis(),
    where: vi.fn().mockReturnThis(),
    orderBy: vi.fn().mockResolvedValue(resolveWith),
  });
  return {
    db: {
      select: vi.fn(() => createChain()),
    },
  };
});

vi.mock("@/db/schema", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/db/schema")>();
  return { ...actual };
});

vi.mock("@/lib/render/job-manager", () => ({
  startRenderJob: vi.fn().mockResolvedValue(undefined),
  updateRenderProgress: vi.fn().mockResolvedValue(undefined),
  completeRenderJob: vi.fn().mockResolvedValue(undefined),
  failRenderJob: vi.fn().mockResolvedValue(undefined),
  getRenderJob: vi.fn(),
}));

vi.mock("@/lib/render/audio-engine", () => ({
  AudioEngine: vi.fn(),
}));

vi.mock("@/lib/render/asset-fetcher", () => {
  class MockAssetFetcher {
    async getTempDir() {
      return "/tmp/render-test";
    }
    async cleanupTemp() {}
  }
  return { AssetFetcher: MockAssetFetcher };
});

vi.mock("@/lib/render/chapters", () => ({
  generateChaptersManifest: vi.fn(),
}));

vi.mock("@/lib/render/uploader", () => ({
  R2Uploader: vi.fn(),
}));

import { executeRenderPipeline } from "@/lib/render/pipeline";
import * as jobManagerModule from "@/lib/render/job-manager";
import * as audioEngineModule from "@/lib/render/audio-engine";
import { AssetFetcher } from "@/lib/render/asset-fetcher";
import { generateChaptersManifest } from "@/lib/render/chapters";
import { R2Uploader } from "@/lib/render/uploader";
import * as dbModule from "@/db";

const mockJob = {
  id: "job-1",
  songsetId: "songset-1",
  userId: 1,
  status: "queued",
  phase: "preparing",
  phaseIndex: 0,
  totalPhases: 5,
  percentComplete: 0,
  estimatedSecondsLeft: null,
  elapsedSeconds: 0,
  errorMessage: null,
  template: "dark",
  resolution: "720p",
  audioEnabled: true,
  videoEnabled: false,
  fontSizePreset: "M",
  includeTitleCard: false,
  titleCardDurationSeconds: null,
  mp3R2Key: null,
  mp4R2Key: null,
  chaptersR2Key: null,
  createdAt: new Date(),
  updatedAt: new Date(),
  completedAt: null,
};

const mockSongsetItems = [
  {
    id: "item-1",
    songsetId: "songset-1",
    songId: "song-1",
    recordingHashPrefix: "rec-1",
    position: 0,
    gapBeats: 2,
    crossfadeEnabled: 0,
    crossfadeDurationSeconds: null,
    keyShiftSemitones: 0,
    tempoRatio: 1,
    tempoBpm: 120,
    durationSeconds: 180,
    songTitle: "Song One",
  },
];

const mockAudioResult = {
  segments: [{ item: { id: "item-1" }, startTimeSeconds: 0, durationSeconds: 180 }],
  totalDurationSeconds: 180,
};

const mockChaptersManifest = { chapters: [], totalDurationSeconds: 180 };

const mockUploadResult = {
  mp3R2Key: "renders/job-1/output.mp3",
  mp4R2Key: undefined,
  chaptersR2Key: "renders/job-1/chapters.json",
  uploadedAt: new Date(),
};

describe("executeRenderPipeline", () => {
  let assetFetcherMock: InstanceType<typeof AssetFetcher>;

  beforeEach(() => {
    vi.clearAllMocks();
    assetFetcherMock = new AssetFetcher();
    vi.mocked(dbModule.db.select).mockImplementation((() => {
      const chain = {
        from: vi.fn().mockReturnThis(),
        leftJoin: vi.fn().mockReturnThis(),
        where: vi.fn().mockReturnThis(),
        orderBy: vi.fn().mockResolvedValue(mockSongsetItems),
      };
      return chain;
    }) as any);
    vi.mocked(audioEngineModule.AudioEngine).mockImplementation(function (this: any) {
      this.generateSongsetAudio = vi.fn().mockResolvedValue(mockAudioResult);
    } as any);
    vi.mocked(R2Uploader).mockImplementation(function (this: any) {
      this.uploadRenderArtifacts = vi.fn().mockResolvedValue(mockUploadResult);
    } as any);
    vi.mocked(generateChaptersManifest).mockResolvedValue(mockChaptersManifest);
    vi.mocked(jobManagerModule.getRenderJob).mockResolvedValue(mockJob);
  });

  it("throws error when songset has no items", async () => {
    vi.mocked(dbModule.db.select).mockImplementation((() => {
      const chain = {
        from: vi.fn().mockReturnThis(),
        leftJoin: vi.fn().mockReturnThis(),
        where: vi.fn().mockReturnThis(),
        orderBy: vi.fn().mockResolvedValue([]),
      };
      return chain;
    }) as any);

    await expect(executeRenderPipeline("job-1", 1)).rejects.toThrow("Songset has no items");
    expect(jobManagerModule.failRenderJob).toHaveBeenCalledWith("job-1", 1, "Songset has no items");
  });

  it("completes successfully for audio-only path", async () => {
    await executeRenderPipeline("job-1", 1);

    expect(jobManagerModule.startRenderJob).toHaveBeenCalledWith("job-1", 1);
    expect(jobManagerModule.updateRenderProgress).toHaveBeenCalled();
    expect(jobManagerModule.completeRenderJob).toHaveBeenCalled();
    expect(jobManagerModule.failRenderJob).not.toHaveBeenCalled();
  });

  it("throws error when job is cancelled", async () => {
    vi.mocked(jobManagerModule.getRenderJob).mockResolvedValue({ ...mockJob, status: "cancelled" });

    await expect(executeRenderPipeline("job-3", 1)).rejects.toThrow("Render job job-3 was cancelled");
    expect(typeof assetFetcherMock.cleanupTemp).toBe("function");
  });

  it("calls failRenderJob on error", async () => {
    vi.mocked(audioEngineModule.AudioEngine).mockImplementation(function (this: any) {
      this.generateSongsetAudio = vi.fn().mockRejectedValue(new Error("Audio engine failed"));
    } as any);

    await expect(executeRenderPipeline("job-4", 1)).rejects.toThrow("Audio engine failed");
    expect(jobManagerModule.failRenderJob).toHaveBeenCalledWith("job-4", 1, "Audio engine failed");
  });

  it("handles upload failures", async () => {
    vi.mocked(R2Uploader).mockImplementation(function (this: any) {
      this.uploadRenderArtifacts = vi.fn().mockRejectedValue(new Error("Upload failed"));
    } as any);

    await expect(executeRenderPipeline("job-9", 1)).rejects.toThrow("Upload failed");
    expect(jobManagerModule.failRenderJob).toHaveBeenCalledWith("job-9", 1, "Upload failed");
  });

  it("handles chapters generation failures", async () => {
    vi.mocked(generateChaptersManifest).mockRejectedValue(new Error("Chapters generation failed"));

    await expect(executeRenderPipeline("job-10", 1)).rejects.toThrow("Chapters generation failed");
    expect(jobManagerModule.failRenderJob).toHaveBeenCalledWith("job-10", 1, "Chapters generation failed");
  });

  it("handles job not found", async () => {
    vi.mocked(jobManagerModule.getRenderJob).mockResolvedValue(null);

    await expect(executeRenderPipeline("job-not-found", 1)).rejects.toThrow("Render job job-not-found not found");
  });
});
