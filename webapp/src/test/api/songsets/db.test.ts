import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  computeRenderState,
  listSongsets,
  getSongset,
  createSongset,
  updateSongset,
  deleteSongset,
  addSongsetItem,
  updateSongsetItem,
  deleteSongsetItem,
} from "@/lib/db/songsets";
import { db } from "@/db";

/* eslint-disable @typescript-eslint/no-explicit-any */

vi.mock("@/db", () => ({
  db: {
    query: {
      songsets: {
        findFirst: vi.fn(),
        findMany: vi.fn(),
      },
      renderJobs: {
        findFirst: vi.fn(),
      },
      songsetItems: {
        findFirst: vi.fn(),
      },
    },
    select: vi.fn(),
    insert: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
  },
}));

vi.mock("drizzle-orm", async (importOriginal) => {
  const actual = await importOriginal<typeof import("drizzle-orm")>();
  return {
    ...actual,
    eq: vi.fn(),
    and: vi.fn(),
    desc: vi.fn(),
    sql: vi.fn((str: string) => str),
  };
});

vi.mock("nanoid", () => ({
  nanoid: vi.fn(() => "test-id"),
}));

describe("computeRenderState", () => {
  it("returns unrendered when no latest render job", async () => {
    vi.mocked(db.query.songsets.findFirst).mockResolvedValue({
      id: "songset-1",
      latestRenderJobId: null,
      lastFailedRenderJobId: null,
    } as any);

    const state = await computeRenderState("songset-1");
    expect(state).toBe("unrendered");
  });

  it("returns fresh when latest job completed successfully", async () => {
    vi.mocked(db.query.songsets.findFirst).mockResolvedValue({
      id: "songset-1",
      latestRenderJobId: "job-1",
      lastFailedRenderJobId: null,
    } as any);

    vi.mocked(db.query.renderJobs.findFirst).mockResolvedValue({
      id: "job-1",
      status: "completed",
    } as any);

    const state = await computeRenderState("songset-1");
    expect(state).toBe("fresh");
  });

  it("returns failed when latest job failed", async () => {
    vi.mocked(db.query.songsets.findFirst).mockResolvedValue({
      id: "songset-1",
      latestRenderJobId: "job-1",
      lastFailedRenderJobId: "job-1",
    } as any);

    vi.mocked(db.query.renderJobs.findFirst).mockResolvedValue({
      id: "job-1",
      status: "failed",
    } as any);

    const state = await computeRenderState("songset-1");
    expect(state).toBe("failed");
  });

  it("returns rendering when job is queued", async () => {
    vi.mocked(db.query.songsets.findFirst).mockResolvedValue({
      id: "songset-1",
      latestRenderJobId: "job-1",
      lastFailedRenderJobId: null,
    } as any);

    vi.mocked(db.query.renderJobs.findFirst).mockResolvedValue({
      id: "job-1",
      status: "queued",
    } as any);

    const state = await computeRenderState("songset-1");
    expect(state).toBe("rendering");
  });

  it("returns rendering when job is running", async () => {
    vi.mocked(db.query.songsets.findFirst).mockResolvedValue({
      id: "songset-1",
      latestRenderJobId: "job-1",
      lastFailedRenderJobId: null,
    } as any);

    vi.mocked(db.query.renderJobs.findFirst).mockResolvedValue({
      id: "job-1",
      status: "running",
    } as any);

    const state = await computeRenderState("songset-1");
    expect(state).toBe("rendering");
  });

  it("throws error when songset not found", async () => {
    vi.mocked(db.query.songsets.findFirst).mockResolvedValue(null);

    await expect(computeRenderState("songset-1")).rejects.toThrow(
      "Songset not found"
    );
  });

  it("returns stale when a newer songset item was created after render", async () => {
    const completedAt = new Date("2024-01-01");
    vi.mocked(db.query.songsets.findFirst).mockResolvedValue({
      id: "songset-1",
      latestRenderJobId: "job-1",
      lastFailedRenderJobId: null,
      updatedAt: completedAt,
    } as any);

    vi.mocked(db.query.renderJobs.findFirst).mockResolvedValue({
      id: "job-1",
      status: "completed",
      completedAt,
    } as any);

    vi.mocked(db.query.songsetItems.findFirst).mockResolvedValue({
      id: "item-1",
      createdAt: new Date("2024-01-02"),
    } as any);

    const state = await computeRenderState("songset-1");
    expect(state).toBe("stale");
  });

  it("returns stale when songset was updated after render completed", async () => {
    const completedAt = new Date("2024-01-01");
    vi.mocked(db.query.songsets.findFirst).mockResolvedValue({
      id: "songset-1",
      latestRenderJobId: "job-1",
      lastFailedRenderJobId: null,
      updatedAt: new Date("2024-01-02"),
    } as any);

    vi.mocked(db.query.renderJobs.findFirst).mockResolvedValue({
      id: "job-1",
      status: "completed",
      completedAt,
    } as any);

    vi.mocked(db.query.songsetItems.findFirst).mockResolvedValue(null);

    const state = await computeRenderState("songset-1");
    expect(state).toBe("stale");
  });
});

describe("listSongsets", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns paginated songsets with render state", async () => {
    const mockSongsets = [
      {
        id: "songset-1",
        name: "Test Songset 1",
        description: null,
        createdAt: new Date("2024-01-01"),
        updatedAt: new Date("2024-01-02"),
        latestRenderJobId: null,
        lastFailedRenderJobId: null,
        items: [],
      },
    ];

    vi.mocked(db.query.songsets.findMany).mockResolvedValue(mockSongsets as any);
    vi.mocked(db.select).mockReturnValue({
      from: vi.fn().mockReturnValue({
        where: vi.fn().mockReturnValue({
          then: vi.fn().mockImplementation((cb) => cb([{ count: 1 }])),
        }),
      }),
    } as any);

    // Mock db.query.songsets.findFirst to return the songset when computeRenderState is called
    vi.mocked(db.query.songsets.findFirst).mockResolvedValue(mockSongsets[0] as any);

    const result = await listSongsets(1, 50, 0);

    expect(result.songsets).toHaveLength(1);
    expect(result.songsets[0].name).toBe("Test Songset 1");
    expect(result.total).toBe(1);
  });

  it("applies limit and offset", async () => {
    vi.mocked(db.query.songsets.findMany).mockResolvedValue([] as any);
    vi.mocked(db.select).mockReturnValue({
      from: vi.fn().mockReturnValue({
        where: vi.fn().mockReturnValue({
          then: vi.fn().mockImplementation((cb) => cb([{ count: 0 }])),
        }),
      }),
    } as any);

    await listSongsets(1, 10, 5);

    expect(db.query.songsets.findMany).toHaveBeenCalledWith(
      expect.objectContaining({
        limit: 10,
        offset: 5,
      })
    );
  });
});

describe("getSongset", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns songset with items", async () => {
    const mockSongset = {
      id: "songset-1",
      name: "Test Songset",
      description: "Test description",
      createdAt: new Date("2024-01-01"),
      updatedAt: new Date("2024-01-02"),
      latestRenderJobId: null,
      lastFailedRenderJobId: null,
      items: [
        {
          id: "item-1",
          songId: "song-1",
          recordingHashPrefix: null,
          position: 0,
          gapBeats: 2.0,
          crossfadeEnabled: 0,
          crossfadeDurationSeconds: null,
          keyShiftSemitones: 0,
          tempoRatio: 1.0,
          song: {
            id: "song-1",
            title: "Test Song",
            composer: null,
            lyricist: null,
            albumName: null,
            musicalKey: null,
          },
          recording: null,
        },
      ],
    };

    vi.mocked(db.query.songsets.findFirst).mockResolvedValue(mockSongset as any);

    const result = await getSongset("songset-1", 1);

    expect(result).not.toBeNull();
    expect(result?.name).toBe("Test Songset");
    expect(result?.items).toHaveLength(1);
  });

  it("returns null when songset not found", async () => {
    vi.mocked(db.query.songsets.findFirst).mockResolvedValue(null);

    const result = await getSongset("songset-1", 1);

    expect(result).toBeNull();
  });

  it("returns null when songset belongs to different user", async () => {
    vi.mocked(db.query.songsets.findFirst).mockResolvedValue(null);

    const result = await getSongset("songset-1", 999);

    expect(result).toBeNull();
  });
});

describe("createSongset", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("creates songset with name and description", async () => {
    const mockSongset = {
      id: "songset-1",
      name: "New Songset",
      description: "Test description",
      createdAt: new Date(),
      updatedAt: new Date(),
      latestRenderJobId: null,
      lastFailedRenderJobId: null,
    };

    vi.mocked(db.insert).mockReturnValue({
      values: vi.fn().mockReturnValue({
        returning: vi.fn().mockResolvedValue([mockSongset]),
      }),
    } as any);

    // Mock db.query.songsets.findFirst to return the songset when computeRenderState is called
    vi.mocked(db.query.songsets.findFirst).mockResolvedValue(mockSongset as any);

    const result = await createSongset(1, {
      name: "New Songset",
      description: "Test description",
    });

    expect(result.name).toBe("New Songset");
    expect(result.description).toBe("Test description");
    expect(result.renderState).toBe("unrendered");
  });

  it("creates songset with only name", async () => {
    const mockSongset = {
      id: "songset-1",
      name: "New Songset",
      description: null,
      createdAt: new Date(),
      updatedAt: new Date(),
      latestRenderJobId: null,
      lastFailedRenderJobId: null,
    };

    vi.mocked(db.insert).mockReturnValue({
      values: vi.fn().mockReturnValue({
        returning: vi.fn().mockResolvedValue([mockSongset]),
      }),
    } as any);

    // Mock db.query.songsets.findFirst to return the songset when computeRenderState is called
    vi.mocked(db.query.songsets.findFirst).mockResolvedValue(mockSongset as any);

    const result = await createSongset(1, {
      name: "New Songset",
    });

    expect(result.name).toBe("New Songset");
    expect(result.description).toBeNull();
  });
});

describe("updateSongset", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("updates songset name and description", async () => {
    const mockSongset = {
      id: "songset-1",
      name: "Updated Songset",
      description: "Updated description",
      createdAt: new Date(),
      updatedAt: new Date(),
      latestRenderJobId: null,
      lastFailedRenderJobId: null,
    };

    // First call: ownership check; second call: post-update re-fetch with items
    vi.mocked(db.query.songsets.findFirst)
      .mockResolvedValueOnce(mockSongset as any)
      .mockResolvedValueOnce({ ...mockSongset, items: [] } as any);

    vi.mocked(db.update).mockReturnValue({
      set: vi.fn().mockReturnValue({
        where: vi.fn().mockReturnValue({
          returning: vi.fn().mockResolvedValue([mockSongset]),
        }),
      }),
    } as any);

    const result = await updateSongset("songset-1", 1, {
      name: "Updated Songset",
      description: "Updated description",
    });

    expect(result).not.toBeNull();
    expect(result?.name).toBe("Updated Songset");
  });

  it("returns null when songset not found", async () => {
    vi.mocked(db.query.songsets.findFirst).mockResolvedValue(null);

    const result = await updateSongset("songset-1", 1, {
      name: "Updated Songset",
    });

    expect(result).toBeNull();
  });
});

describe("deleteSongset", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("deletes existing songset", async () => {
    const mockSongset = {
      id: "songset-1",
      name: "Test Songset",
    };

    vi.mocked(db.query.songsets.findFirst).mockResolvedValue(mockSongset as any);
    vi.mocked(db.delete).mockReturnValue({
      where: vi.fn().mockResolvedValue(undefined),
    } as any);

    const result = await deleteSongset("songset-1", 1);

    expect(result).toBe(true);
  });

  it("returns false when songset not found", async () => {
    vi.mocked(db.query.songsets.findFirst).mockResolvedValue(null);

    const result = await deleteSongset("songset-1", 1);

    expect(result).toBe(false);
  });
});

describe("addSongsetItem", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("adds item to songset", async () => {
    const mockSongset = {
      id: "songset-1",
      userId: 1,
    };

    const mockItem = {
      id: "item-1",
      songId: "song-1",
      recordingHashPrefix: null,
      position: 0,
      gapBeats: 2.0,
      crossfadeEnabled: 0,
      crossfadeDurationSeconds: null,
      keyShiftSemitones: 0,
      tempoRatio: 1.0,
      song: {
        id: "song-1",
        title: "Test Song",
        composer: null,
        lyricist: null,
        albumName: null,
        musicalKey: null,
      },
      recording: null,
    };

    vi.mocked(db.query.songsets.findFirst).mockResolvedValue(mockSongset as any);
    vi.mocked(db.insert).mockReturnValue({
      values: vi.fn().mockReturnValue({
        returning: vi.fn().mockResolvedValue([mockItem]),
      }),
    } as any);
    vi.mocked(db.query.songsetItems.findFirst).mockResolvedValue(mockItem as any);

    const result = await addSongsetItem("songset-1", 1, {
      songId: "song-1",
      position: 0,
    });

    expect(result).not.toBeNull();
    expect(result?.songId).toBe("song-1");
  });

  it("returns null when songset not found", async () => {
    vi.mocked(db.query.songsets.findFirst).mockResolvedValue(null);

    const result = await addSongsetItem("songset-1", 1, {
      songId: "song-1",
      position: 0,
    });

    expect(result).toBeNull();
  });
});

describe("updateSongsetItem", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("updates songset item", async () => {
    const mockItem = {
      id: "item-1",
      songsetId: "songset-1",
      songset: { userId: 1 },
      songId: "song-1",
      recordingHashPrefix: null,
      position: 0,
      gapBeats: 2.0,
      crossfadeEnabled: 0,
      crossfadeDurationSeconds: null,
      keyShiftSemitones: 0,
      tempoRatio: 1.0,
      song: {
        id: "song-1",
        title: "Test Song",
        composer: null,
        lyricist: null,
        albumName: null,
        musicalKey: null,
      },
      recording: null,
    };

    const mockUpdatedItem = {
      ...mockItem,
      position: 1,
    };

    // First call returns the original item, second call returns the updated item
    vi.mocked(db.query.songsetItems.findFirst)
      .mockResolvedValueOnce(mockItem as any)
      .mockResolvedValueOnce(mockUpdatedItem as any);

    const mockReturning = vi.fn().mockResolvedValue([mockUpdatedItem]);
    const mockWhere = vi.fn().mockReturnValue({ returning: mockReturning });
    const mockSet = vi.fn().mockReturnValue({ where: mockWhere });
    vi.mocked(db.update).mockReturnValue({ set: mockSet } as any);

    const result = await updateSongsetItem("item-1", "songset-1", 1, {
      position: 1,
    });

    expect(result).not.toBeNull();
    expect(result?.position).toBe(1);
  });

  it("returns null when item not found", async () => {
    vi.mocked(db.query.songsetItems.findFirst).mockResolvedValue(null);

    const result = await updateSongsetItem("item-1", "songset-1", 1, {
      position: 1,
    });

    expect(result).toBeNull();
  });

  it("returns null when item belongs to different songset", async () => {
    const mockItem = {
      id: "item-1",
      songsetId: "songset-2",
      songset: { userId: 1 },
    };

    vi.mocked(db.query.songsetItems.findFirst).mockResolvedValue(mockItem as any);

    const result = await updateSongsetItem("item-1", "songset-1", 1, {
      position: 1,
    });

    expect(result).toBeNull();
  });
});

describe("deleteSongsetItem", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("deletes songset item", async () => {
    const mockItem = {
      id: "item-1",
      songsetId: "songset-1",
      songset: { userId: 1 },
    };

    vi.mocked(db.query.songsetItems.findFirst).mockResolvedValue(mockItem as any);
    vi.mocked(db.delete).mockReturnValue({
      where: vi.fn().mockResolvedValue(undefined),
    } as any);

    const result = await deleteSongsetItem("item-1", "songset-1", 1);

    expect(result).toBe(true);
  });

  it("returns false when item not found", async () => {
    vi.mocked(db.query.songsetItems.findFirst).mockResolvedValue(null);

    const result = await deleteSongsetItem("item-1", "songset-1", 1);

    expect(result).toBe(false);
  });
});
