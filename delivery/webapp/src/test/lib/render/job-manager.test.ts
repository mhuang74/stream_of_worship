import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  createRenderJob,
  getRenderJob,
  updateRenderProgress,
  completeRenderJob,
  failRenderJob,
  cancelRenderJob,
  startRenderJob,
  getPhaseIndex,
  recoverOrphanedJobs,
} from "@/lib/render/job-manager";
import { db } from "@/db";
import { songsets } from "@/db/schema";

/* eslint-disable @typescript-eslint/no-explicit-any */

vi.mock("@/db", () => {
  const selectChain = {
    from: vi.fn().mockReturnThis(),
    leftJoin: vi.fn().mockReturnThis(),
    where: vi.fn().mockResolvedValue([]),
  };
  const dbMock = {
    query: {
      songsets: {
        findFirst: vi.fn(),
      },
      renderJobs: {
        findFirst: vi.fn(),
      },
    },
    insert: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    select: vi.fn(() => selectChain),
    transaction: vi.fn(),
  };
  return {
    db: dbMock,
  };
});

vi.mock("nanoid", () => ({
  nanoid: vi.fn(() => "mock-job-id"),
}));

const mockSongset = {
  id: "songset-1",
  userId: 1,
  name: "Test Songset",
  description: null,
  createdAt: new Date(),
  updatedAt: new Date(),
  latestRenderJobId: null,
  lastFailedRenderJobId: null,
  lastCompletedRenderJobId: null,
};

const mockRenderJob = {
  id: "mock-job-id",
  songsetId: "songset-1",
  userId: 1,
  status: "queued",
  phase: "preparing",
  phaseIndex: 0,
  totalPhases: 5,
  elapsedSeconds: 0,
  errorMessage: null,
  estimatedTotalSeconds: null,
  totalDurationSeconds: null,
  startedAt: null,
  template: "dark",
  resolution: "720p",
  audioEnabled: true,
  videoEnabled: true,
  fontSizePreset: "M",
  fontFamily: "noto_serif_tc",
  includeTitleCard: false,
  titleCardDurationSeconds: null,
  mp3R2Key: null,
  mp4R2Key: null,
  chaptersR2Key: null,
  songCount: null,
  songsetDurationSeconds: null,
  createdAt: new Date(),
  updatedAt: new Date(),
  completedAt: null,
};

describe("getPhaseIndex", () => {
  it("returns correct index for each phase", () => {
    expect(getPhaseIndex("preparing")).toBe(0);
    expect(getPhaseIndex("mixing_audio")).toBe(1);
    expect(getPhaseIndex("rendering_frames")).toBe(2);
    expect(getPhaseIndex("encoding_video")).toBe(3);
    expect(getPhaseIndex("uploading")).toBe(4);
    expect(getPhaseIndex("completed")).toBe(5);
  });
});

describe("createRenderJob", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("creates job with default values", async () => {
    vi.mocked(db.query.songsets.findFirst).mockResolvedValue(mockSongset);
    
    const mockInsert = vi.fn().mockReturnValue({
      values: vi.fn().mockReturnValue({
        returning: vi.fn().mockResolvedValue([mockRenderJob]),
      }),
    });
    vi.mocked(db.insert).mockImplementation(mockInsert as any);

    const mockUpdate = vi.fn().mockReturnValue({
      set: vi.fn().mockReturnValue({
        where: vi.fn().mockResolvedValue(undefined),
      }),
    });
    vi.mocked(db.update).mockImplementation(mockUpdate as any);

    const job = await createRenderJob(1, { songsetId: "songset-1" });

    expect(job.id).toBe("mock-job-id");
    expect(job.songsetId).toBe("songset-1");
    expect(job.userId).toBe(1);
    expect(job.status).toBe("queued");
    expect(job.phase).toBe("preparing");
    expect(job.phaseIndex).toBe(0);
    expect(job.totalPhases).toBe(5);
    expect(job.template).toBe("dark");
    expect(job.resolution).toBe("720p");
    expect(job.audioEnabled).toBe(true);
    expect(job.videoEnabled).toBe(true);
    expect(job.fontSizePreset).toBe("M");
    expect(job.includeTitleCard).toBe(false);
  });

  it("creates job with custom options", async () => {
    vi.mocked(db.query.songsets.findFirst).mockResolvedValue(mockSongset);
    
    const customJob = {
      ...mockRenderJob,
      template: "gradient_warm",
      resolution: "1080p",
      fontSizePreset: "L",
      includeTitleCard: true,
      titleCardDurationSeconds: 15,
    };
    
    const mockInsert = vi.fn().mockReturnValue({
      values: vi.fn().mockReturnValue({
        returning: vi.fn().mockResolvedValue([customJob]),
      }),
    });
    vi.mocked(db.insert).mockImplementation(mockInsert as any);

    const mockUpdate = vi.fn().mockReturnValue({
      set: vi.fn().mockReturnValue({
        where: vi.fn().mockResolvedValue(undefined),
      }),
    });
    vi.mocked(db.update).mockImplementation(mockUpdate as any);

    const job = await createRenderJob(1, {
      songsetId: "songset-1",
      template: "gradient_warm",
      resolution: "1080p",
      fontSizePreset: "L",
      includeTitleCard: true,
      titleCardDurationSeconds: 15,
    });

    expect(job.template).toBe("gradient_warm");
    expect(job.resolution).toBe("1080p");
    expect(job.fontSizePreset).toBe("L");
    expect(job.includeTitleCard).toBe(true);
    expect(job.titleCardDurationSeconds).toBe(15);
  });

  it("throws error when songset not found", async () => {
    vi.mocked(db.query.songsets.findFirst).mockResolvedValue(null);

    await expect(
      createRenderJob(1, { songsetId: "nonexistent" })
    ).rejects.toThrow("Songset not found or access denied");
  });

  it("updates songset with latest render job id", async () => {
    vi.mocked(db.query.songsets.findFirst).mockResolvedValue(mockSongset);
    
    const mockInsert = vi.fn().mockReturnValue({
      values: vi.fn().mockReturnValue({
        returning: vi.fn().mockResolvedValue([mockRenderJob]),
      }),
    });
    vi.mocked(db.insert).mockImplementation(mockInsert as any);

    const mockUpdate = vi.fn().mockReturnValue({
      set: vi.fn().mockReturnValue({
        where: vi.fn().mockResolvedValue(undefined),
      }),
    });
    vi.mocked(db.update).mockImplementation(mockUpdate as any);

    await createRenderJob(1, { songsetId: "songset-1" });

    expect(db.update).toHaveBeenCalledWith(songsets);
  });
});

describe("getRenderJob", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns job when found", async () => {
    vi.mocked(db.query.renderJobs.findFirst).mockResolvedValue(mockRenderJob);

    const job = await getRenderJob("mock-job-id", 1);

    expect(job).not.toBeNull();
    expect(job?.id).toBe("mock-job-id");
    expect(job?.songsetId).toBe("songset-1");
    expect(job?.status).toBe("queued");
  });

  it("returns null when job not found", async () => {
    vi.mocked(db.query.renderJobs.findFirst).mockResolvedValue(null);

    const job = await getRenderJob("nonexistent", 1);

    expect(job).toBeNull();
  });
});

describe("recoverOrphanedJobs", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 0 when no orphaned jobs exist", async () => {
    const selectChain = {
      from: vi.fn().mockReturnThis(),
      where: vi.fn().mockResolvedValue([]),
    };
    vi.mocked(db.select).mockReturnValue(selectChain as any);

    const count = await recoverOrphanedJobs();
    expect(count).toBe(0);
  });

  it("marks orphaned running jobs as failed", async () => {
    const orphanedJobs = [
      { id: "job-1", songsetId: "set-1" },
      { id: "job-2", songsetId: "set-2" },
    ];
    const selectChain = {
      from: vi.fn().mockReturnThis(),
      where: vi.fn().mockResolvedValue(orphanedJobs),
    };
    vi.mocked(db.select).mockReturnValue(selectChain as any);

    const mockUpdate = vi.fn().mockReturnValue({
      set: vi.fn().mockReturnValue({
        where: vi.fn().mockReturnValue({
          returning: vi.fn().mockResolvedValue([{ id: "job-1" }]),
        }),
      }),
    });
    vi.mocked(db.update).mockImplementation(mockUpdate as any);

    const count = await recoverOrphanedJobs();
    expect(count).toBe(2);
    expect(db.update).toHaveBeenCalledTimes(4);
  });
});

describe("updateRenderProgress", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("updates phase and phaseIndex", async () => {
    vi.mocked(db.query.renderJobs.findFirst).mockResolvedValue(mockRenderJob);
    
    const mockUpdate = vi.fn().mockReturnValue({
      set: vi.fn().mockReturnValue({
        where: vi.fn().mockReturnValue({
          returning: vi.fn().mockResolvedValue([
            { ...mockRenderJob, phase: "mixing_audio", phaseIndex: 1 },
          ]),
        }),
      }),
    });
    vi.mocked(db.update).mockImplementation(mockUpdate as any);

    const job = await updateRenderProgress(1, "mock-job-id", {
      phase: "mixing_audio",
    });

    expect(job?.phase).toBe("mixing_audio");
    expect(job?.phaseIndex).toBe(1);
  });

  it("updates elapsedSeconds", async () => {
    vi.mocked(db.query.renderJobs.findFirst).mockResolvedValue(mockRenderJob);
    
    const mockUpdate = vi.fn().mockReturnValue({
      set: vi.fn().mockReturnValue({
        where: vi.fn().mockReturnValue({
          returning: vi.fn().mockResolvedValue([
            { ...mockRenderJob, elapsedSeconds: 120 },
          ]),
        }),
      }),
    });
    vi.mocked(db.update).mockImplementation(mockUpdate as any);

    const job = await updateRenderProgress(1, "mock-job-id", {
      elapsedSeconds: 120,
    });

    expect(job?.elapsedSeconds).toBe(120);
  });

  it("updates estimatedTotalSeconds and totalDurationSeconds", async () => {
    vi.mocked(db.query.renderJobs.findFirst).mockResolvedValue(mockRenderJob);
    
    const mockUpdate = vi.fn().mockReturnValue({
      set: vi.fn().mockReturnValue({
        where: vi.fn().mockReturnValue({
          returning: vi.fn().mockResolvedValue([
            { ...mockRenderJob, estimatedTotalSeconds: 180, totalDurationSeconds: 120 },
          ]),
        }),
      }),
    });
    vi.mocked(db.update).mockImplementation(mockUpdate as any);

    const job = await updateRenderProgress(1, "mock-job-id", {
      estimatedTotalSeconds: 180,
      totalDurationSeconds: 120,
    });

    expect(job?.estimatedTotalSeconds).toBe(180);
    expect(job?.totalDurationSeconds).toBe(120);
  });

  it("updates startedAt", async () => {
    vi.mocked(db.query.renderJobs.findFirst).mockResolvedValue(mockRenderJob);
    
    const startedAt = new Date();
    const mockUpdate = vi.fn().mockReturnValue({
      set: vi.fn().mockReturnValue({
        where: vi.fn().mockReturnValue({
          returning: vi.fn().mockResolvedValue([
            { ...mockRenderJob, startedAt },
          ]),
        }),
      }),
    });
    vi.mocked(db.update).mockImplementation(mockUpdate as any);

    const job = await updateRenderProgress(1, "mock-job-id", {
      startedAt,
    });

    expect(job?.startedAt).toEqual(startedAt);
  });

  it("returns null when job not found", async () => {
    vi.mocked(db.query.renderJobs.findFirst).mockResolvedValue(null);

    const job = await updateRenderProgress(1, "nonexistent", {
      phase: "mixing_audio",
    });

    expect(job).toBeNull();
  });
});

describe("completeRenderJob", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("marks job as completed with output keys", async () => {
    vi.mocked(db.query.renderJobs.findFirst).mockResolvedValue({
      ...mockRenderJob,
      startedAt: new Date("2024-01-01T00:00:00Z"),
    });
    
    const mockTxUpdate = vi.fn().mockReturnValue({
      set: vi.fn().mockReturnValue({
        where: vi.fn().mockReturnValue({
          returning: vi.fn().mockResolvedValue([
            {
              ...mockRenderJob,
              status: "completed",
              phase: "completed",
              phaseIndex: 5,
              mp3R2Key: "audio.mp3",
              mp4R2Key: "video.mp4",
              chaptersR2Key: "chapters.json",
              completedAt: new Date(),
            },
          ]),
        }),
      }),
    });
    vi.mocked(db.transaction).mockImplementation(async (cb: any) => {
      const tx = { update: mockTxUpdate };
      return cb(tx);
    });

    const job = await completeRenderJob("mock-job-id", 1, {
      mp3R2Key: "audio.mp3",
      mp4R2Key: "video.mp4",
      chaptersR2Key: "chapters.json",
    });

    expect(job?.status).toBe("completed");
    expect(job?.phase).toBe("completed");
    expect(job?.phaseIndex).toBe(5);
    expect(job?.mp3R2Key).toBe("audio.mp3");
    expect(job?.mp4R2Key).toBe("video.mp4");
    expect(job?.chaptersR2Key).toBe("chapters.json");
    expect(job?.completedAt).not.toBeNull();
  });

  it("computes elapsedSeconds from startedAt", async () => {
    const startedAt = new Date("2024-01-01T00:00:00Z");
    vi.mocked(db.query.renderJobs.findFirst).mockResolvedValue({
      ...mockRenderJob,
      startedAt,
    });
    
    const mockTxUpdate = vi.fn().mockImplementation(() => ({
      set: vi.fn().mockImplementation((updates: any) => {
        if (updates.elapsedSeconds !== undefined) {
          expect(updates.elapsedSeconds).toBeGreaterThan(0);
        }
        return {
          where: vi.fn().mockReturnValue({
            returning: vi.fn().mockResolvedValue([
              { ...mockRenderJob, status: "completed", elapsedSeconds: updates.elapsedSeconds, songsetId: "songset-1" },
            ]),
          }),
        };
      }),
    }));
    vi.mocked(db.transaction).mockImplementation(async (cb: any) => {
      const tx = { update: mockTxUpdate };
      return cb(tx);
    });

    const job = await completeRenderJob("mock-job-id", 1, {});
    expect(job?.status).toBe("completed");
  });

  it("handles null startedAt gracefully", async () => {
    vi.mocked(db.query.renderJobs.findFirst).mockResolvedValue({
      ...mockRenderJob,
      startedAt: null,
    });
    
    const mockTxUpdate = vi.fn().mockReturnValue({
      set: vi.fn().mockReturnValue({
        where: vi.fn().mockReturnValue({
          returning: vi.fn().mockResolvedValue([
            { ...mockRenderJob, status: "completed", elapsedSeconds: null },
          ]),
        }),
      }),
    });
    vi.mocked(db.transaction).mockImplementation(async (cb: any) => {
      const tx = { update: mockTxUpdate };
      return cb(tx);
    });

    const job = await completeRenderJob("mock-job-id", 1, {});
    expect(job?.status).toBe("completed");
  });

  it("returns null when job not found", async () => {
    const mockTxUpdate = vi.fn().mockReturnValue({
      set: vi.fn().mockReturnValue({
        where: vi.fn().mockReturnValue({
          returning: vi.fn().mockResolvedValue([]),
        }),
      }),
    });
    vi.mocked(db.transaction).mockImplementation(async (cb: any) => {
      const tx = { update: mockTxUpdate };
      return cb(tx);
    });

    const job = await completeRenderJob("nonexistent", 1, {});

    expect(job).toBeNull();
  });
});

describe("failRenderJob", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("marks job as failed with error message", async () => {
    const mockUpdate = vi.fn();
    
    // First call for renderJobs
    mockUpdate.mockReturnValueOnce({
      set: vi.fn().mockReturnValue({
        where: vi.fn().mockReturnValue({
          returning: vi.fn().mockResolvedValue([
            {
              ...mockRenderJob,
              status: "failed",
              errorMessage: "FFmpeg encoding failed",
            },
          ]),
        }),
      }),
    });
    
    // Second call for songsets
    mockUpdate.mockReturnValueOnce({
      set: vi.fn().mockReturnValue({
        where: vi.fn().mockResolvedValue(undefined),
      }),
    });
    
    vi.mocked(db.update).mockImplementation(mockUpdate as any);

    const job = await failRenderJob(1, "mock-job-id", "FFmpeg encoding failed");

    expect(job?.status).toBe("failed");
    expect(job?.errorMessage).toBe("FFmpeg encoding failed");
  });

  it("updates songset with failed job reference", async () => {
    const mockUpdate = vi.fn();
    
    mockUpdate.mockReturnValueOnce({
      set: vi.fn().mockReturnValue({
        where: vi.fn().mockReturnValue({
          returning: vi.fn().mockResolvedValue([
            { ...mockRenderJob, status: "failed", errorMessage: "Error" },
          ]),
        }),
      }),
    });
    
    mockUpdate.mockReturnValueOnce({
      set: vi.fn().mockReturnValue({
        where: vi.fn().mockResolvedValue(undefined),
      }),
    });
    
    vi.mocked(db.update).mockImplementation(mockUpdate as any);

    await failRenderJob(1, "mock-job-id", "Error");

    expect(db.update).toHaveBeenCalledWith(songsets);
  });
});

describe("cancelRenderJob", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("cancels queued job", async () => {
    vi.mocked(db.query.renderJobs.findFirst).mockResolvedValue(mockRenderJob);
    
    const mockUpdate = vi.fn().mockReturnValue({
      set: vi.fn().mockReturnValue({
        where: vi.fn().mockReturnValue({
          returning: vi.fn().mockResolvedValue([
            { ...mockRenderJob, status: "cancelled" },
          ]),
        }),
      }),
    });
    vi.mocked(db.update).mockImplementation(mockUpdate as any);

    const job = await cancelRenderJob(1, "mock-job-id");

    expect(job?.status).toBe("cancelled");
  });

  it("cancels running job", async () => {
    vi.mocked(db.query.renderJobs.findFirst).mockResolvedValue({
      ...mockRenderJob,
      status: "running",
    });
    
    const mockUpdate = vi.fn().mockReturnValue({
      set: vi.fn().mockReturnValue({
        where: vi.fn().mockReturnValue({
          returning: vi.fn().mockResolvedValue([
            { ...mockRenderJob, status: "cancelled" },
          ]),
        }),
      }),
    });
    vi.mocked(db.update).mockImplementation(mockUpdate as any);

    const job = await cancelRenderJob(1, "mock-job-id");

    expect(job?.status).toBe("cancelled");
  });

  it("throws error when job is completed", async () => {
    vi.mocked(db.query.renderJobs.findFirst).mockResolvedValue({
      ...mockRenderJob,
      status: "completed",
    });

    await expect(cancelRenderJob(1, "mock-job-id")).rejects.toThrow(
      "Cannot cancel job with status: completed"
    );
  });

  it("throws error when job is failed", async () => {
    vi.mocked(db.query.renderJobs.findFirst).mockResolvedValue({
      ...mockRenderJob,
      status: "failed",
    });

    await expect(cancelRenderJob(1, "mock-job-id")).rejects.toThrow(
      "Cannot cancel job with status: failed"
    );
  });

  it("returns null when job not found", async () => {
    vi.mocked(db.query.renderJobs.findFirst).mockResolvedValue(null);

    const job = await cancelRenderJob(1, "nonexistent");

    expect(job).toBeNull();
  });

  it("throws error when job is already cancelled", async () => {
    vi.mocked(db.query.renderJobs.findFirst).mockResolvedValue({
      ...mockRenderJob,
      status: "cancelled",
    });

    await expect(cancelRenderJob(1, "mock-job-id")).rejects.toThrow(
      "Cannot cancel job with status: cancelled"
    );
  });
});

describe("startRenderJob", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("marks job as running", async () => {
    const mockUpdate = vi.fn().mockReturnValue({
      set: vi.fn().mockReturnValue({
        where: vi.fn().mockReturnValue({
          returning: vi.fn().mockResolvedValue([
            { ...mockRenderJob, status: "running" },
          ]),
        }),
      }),
    });
    vi.mocked(db.update).mockImplementation(mockUpdate as any);

    const job = await startRenderJob(1, "mock-job-id");

    expect(job?.status).toBe("running");
  });

  it("returns null when job not found", async () => {
    const mockUpdate = vi.fn().mockReturnValue({
      set: vi.fn().mockReturnValue({
        where: vi.fn().mockReturnValue({
          returning: vi.fn().mockResolvedValue([]),
        }),
      }),
    });
    vi.mocked(db.update).mockImplementation(mockUpdate as any);

    const job = await startRenderJob(1, "nonexistent");

    expect(job).toBeNull();
  });
});

describe("fontFamily normalization in mapRowToRenderJob", () => {
  it("normalizes unknown fontFamily to noto_serif_tc", async () => {
    vi.mocked(db.query.renderJobs.findFirst).mockResolvedValue({
      ...mockRenderJob,
      fontFamily: "bad_value",
    } as any);

    const job = await getRenderJob("mock-job-id", 1);

    expect(job?.fontFamily).toBe("noto_serif_tc");
  });

  it("normalizes null fontFamily to noto_serif_tc", async () => {
    vi.mocked(db.query.renderJobs.findFirst).mockResolvedValue({
      ...mockRenderJob,
      fontFamily: null,
    } as any);

    const job = await getRenderJob("mock-job-id", 1);

    expect(job?.fontFamily).toBe("noto_serif_tc");
  });

  it("preserves valid fontFamily values", async () => {
    vi.mocked(db.query.renderJobs.findFirst).mockResolvedValue({
      ...mockRenderJob,
      fontFamily: "lxgw_wenkai_tc",
    } as any);

    const job = await getRenderJob("mock-job-id", 1);

    expect(job?.fontFamily).toBe("lxgw_wenkai_tc");
  });
});
