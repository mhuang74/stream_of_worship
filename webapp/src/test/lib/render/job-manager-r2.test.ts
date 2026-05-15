import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  updateRenderJobR2Keys,
  updateSongsetLatestRenderJob,
  updateSongsetFailedRenderJob,
  clearSongsetFailedRenderJob,
  getLatestRenderJobForSongset,
} from "@/lib/render/job-manager";
import { db } from "@/db";
import { renderJobs, songsets } from "@/db/schema";

/* eslint-disable @typescript-eslint/no-explicit-any */

// Mock the database
vi.mock("@/db", () => ({
  db: {
    update: vi.fn().mockReturnThis(),
    set: vi.fn().mockReturnThis(),
    where: vi.fn().mockReturnThis(),
    returning: vi.fn(),
    query: {
      songsets: {
        findFirst: vi.fn(),
      },
      renderJobs: {
        findFirst: vi.fn(),
      },
    },
  },
}));

describe("job-manager R2 and songset updates", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("updateRenderJobR2Keys", () => {
    it("updates render job with R2 keys", async () => {
      const mockJob = {
        id: "job-1",
        songsetId: "set-1",
        userId: 1,
        status: "completed",
        phase: "completed",
        phaseIndex: 5,
        totalPhases: 5,
        percentComplete: 100,
        estimatedSecondsLeft: null,
        elapsedSeconds: 120,
        errorMessage: null,
        template: "dark",
        resolution: "720p",
        audioEnabled: true,
        videoEnabled: true,
        fontSizePreset: "M",
        includeTitleCard: false,
        titleCardDurationSeconds: null,
        mp3R2Key: "renders/job-1/output.mp3",
        mp4R2Key: "renders/job-1/output.mp4",
        chaptersR2Key: "renders/job-1/chapters.json",
        createdAt: new Date(),
        updatedAt: new Date(),
        completedAt: new Date(),
      };

      vi.mocked(db.returning).mockResolvedValueOnce([mockJob]);

      const result = await updateRenderJobR2Keys("job-1", 1, {
        mp3R2Key: "renders/job-1/output.mp3",
        mp4R2Key: "renders/job-1/output.mp4",
        chaptersR2Key: "renders/job-1/chapters.json",
      });

      expect(result).not.toBeNull();
      expect(result?.mp3R2Key).toBe("renders/job-1/output.mp3");
      expect(result?.mp4R2Key).toBe("renders/job-1/output.mp4");
      expect(result?.chaptersR2Key).toBe("renders/job-1/chapters.json");

      expect(db.update).toHaveBeenCalledWith(renderJobs);
      expect(db.set).toHaveBeenCalledWith(
        expect.objectContaining({
          mp3R2Key: "renders/job-1/output.mp3",
          mp4R2Key: "renders/job-1/output.mp4",
          chaptersR2Key: "renders/job-1/chapters.json",
        })
      );
    });

    it("updates partial R2 keys", async () => {
      const mockJob = {
        id: "job-1",
        songsetId: "set-1",
        userId: 1,
        status: "completed",
        phase: "completed",
        phaseIndex: 5,
        totalPhases: 5,
        percentComplete: 100,
        estimatedSecondsLeft: null,
        elapsedSeconds: 120,
        errorMessage: null,
        template: "dark",
        resolution: "720p",
        audioEnabled: true,
        videoEnabled: false,
        fontSizePreset: "M",
        includeTitleCard: false,
        titleCardDurationSeconds: null,
        mp3R2Key: "renders/job-1/output.mp3",
        mp4R2Key: null,
        chaptersR2Key: null,
        createdAt: new Date(),
        updatedAt: new Date(),
        completedAt: new Date(),
      };

      vi.mocked(db.returning).mockResolvedValueOnce([mockJob]);

      const result = await updateRenderJobR2Keys("job-1", 1, {
        mp3R2Key: "renders/job-1/output.mp3",
      });

      expect(result?.mp3R2Key).toBe("renders/job-1/output.mp3");
      expect(result?.mp4R2Key).toBeNull();
      expect(result?.chaptersR2Key).toBeNull();
    });

    it("returns null when job not found", async () => {
      vi.mocked(db.returning).mockResolvedValueOnce([]);

      const result = await updateRenderJobR2Keys("nonexistent", 1, {
        mp3R2Key: "renders/test/output.mp3",
      });

      expect(result).toBeNull();
    });
  });

  describe("updateSongsetLatestRenderJob", () => {
    it("updates songset with latest render job ID", async () => {
      vi.mocked(db.returning).mockResolvedValueOnce([{ id: "set-1" }]);

      await updateSongsetLatestRenderJob("set-1", "job-123");

      expect(db.update).toHaveBeenCalledWith(songsets);
      expect(db.set).toHaveBeenCalledWith(
        expect.objectContaining({
          latestRenderJobId: "job-123",
        })
      );
    });
  });

  describe("updateSongsetFailedRenderJob", () => {
    it("updates songset with failed render job ID", async () => {
      vi.mocked(db.returning).mockResolvedValueOnce([{ id: "set-1" }]);

      await updateSongsetFailedRenderJob("set-1", "job-failed");

      expect(db.update).toHaveBeenCalledWith(songsets);
      expect(db.set).toHaveBeenCalledWith(
        expect.objectContaining({
          lastFailedRenderJobId: "job-failed",
        })
      );
    });
  });

  describe("clearSongsetFailedRenderJob", () => {
    it("clears failed render job ID from songset", async () => {
      vi.mocked(db.returning).mockResolvedValueOnce([{ id: "set-1" }]);

      await clearSongsetFailedRenderJob("set-1");

      expect(db.update).toHaveBeenCalledWith(songsets);
      expect(db.set).toHaveBeenCalledWith(
        expect.objectContaining({
          lastFailedRenderJobId: null,
        })
      );
    });
  });

  describe("getLatestRenderJobForSongset", () => {
    it("returns latest render job for songset", async () => {
      const mockSongset = {
        id: "set-1",
        userId: 1,
        name: "Test Set",
        description: null,
        latestRenderJobId: "job-123",
        lastFailedRenderJobId: null,
        createdAt: new Date(),
        updatedAt: new Date(),
      };

      const mockJob = {
        id: "job-123",
        songsetId: "set-1",
        userId: 1,
        status: "completed",
        phase: "completed",
        phaseIndex: 5,
        totalPhases: 5,
        percentComplete: 100,
        estimatedSecondsLeft: null,
        elapsedSeconds: 120,
        errorMessage: null,
        template: "dark",
        resolution: "720p",
        audioEnabled: true,
        videoEnabled: true,
        fontSizePreset: "M",
        includeTitleCard: false,
        titleCardDurationSeconds: null,
        mp3R2Key: "renders/job-123/output.mp3",
        mp4R2Key: "renders/job-123/output.mp4",
        chaptersR2Key: "renders/job-123/chapters.json",
        createdAt: new Date(),
        updatedAt: new Date(),
        completedAt: new Date(),
      };

      vi.mocked(db.query.songsets.findFirst).mockResolvedValueOnce(mockSongset as any);
      vi.mocked(db.query.renderJobs.findFirst).mockResolvedValueOnce(mockJob as any);

      const result = await getLatestRenderJobForSongset("set-1", 1);

      expect(result).not.toBeNull();
      expect(result?.id).toBe("job-123");
      expect(result?.mp3R2Key).toBe("renders/job-123/output.mp3");
    });

    it("returns null when songset has no render job", async () => {
      const mockSongset = {
        id: "set-1",
        userId: 1,
        name: "Test Set",
        description: null,
        latestRenderJobId: null,
        lastFailedRenderJobId: null,
        createdAt: new Date(),
        updatedAt: new Date(),
      };

      vi.mocked(db.query.songsets.findFirst).mockResolvedValueOnce(mockSongset as any);

      const result = await getLatestRenderJobForSongset("set-1", 1);

      expect(result).toBeNull();
    });

    it("returns null when songset not found", async () => {
      vi.mocked(db.query.songsets.findFirst).mockResolvedValueOnce(undefined);

      const result = await getLatestRenderJobForSongset("nonexistent", 1);

      expect(result).toBeNull();
    });
  });
});
