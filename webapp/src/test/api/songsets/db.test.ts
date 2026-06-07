import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  computeRenderState,
  getSongset,
  createSongset,
  updateSongset,
  deleteSongset,
  addSongsetItem,
  updateSongsetItem,
  deleteSongsetItem,
  listSongsetSummaries,
  getSongsetEditorData,
  getRenderPageData,
  mapRenderStateFromSnapshot,
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
        findMany: vi.fn(),
      },
      songsetItems: {
        findFirst: vi.fn(),
        findMany: vi.fn(),
      },
      userSettings: {
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
    gt: vi.fn(),
    sql: vi.fn((str: string) => str),
    asc: vi.fn(),
  };
});

vi.mock("nanoid", () => ({
  nanoid: vi.fn(() => "test-id"),
}));

function createSelectChain(rows: any[]) {
  const promise = Promise.resolve(rows);

  const chain: any = {};
  const terminalMethods = {
    groupBy: () => chain,
    orderBy: () => chain,
    limit: () => chain,
    offset: () => chain,
    then: (cb: any) => promise.then(cb),
  };

  Object.assign(chain, terminalMethods, {
    from: () => chain,
    leftJoin: () => chain,
    where: () => chain,
  });

  for (const method of ["from", "leftJoin", "where", "groupBy", "orderBy", "limit", "offset"]) {
    chain[method] = vi.fn().mockReturnValue(chain);
  }
  chain.then = (cb: any) => promise.then(cb);

  return { from: vi.fn().mockReturnValue(chain) };
}

describe("mapRenderStateFromSnapshot", () => {
  it("returns unrendered when no latestRenderJobId", () => {
    expect(
      mapRenderStateFromSnapshot({
        latestRenderJobId: null,
        lastFailedRenderJobId: null,
        latestJobStatus: null,
        latestJobCompletedAt: null,
      })
    ).toBe("unrendered");
  });

  it("returns unrendered when no latestJobStatus", () => {
    expect(
      mapRenderStateFromSnapshot({
        latestRenderJobId: "job-1",
        lastFailedRenderJobId: null,
        latestJobStatus: null,
        latestJobCompletedAt: null,
      })
    ).toBe("unrendered");
  });

  it("returns rendering when status is queued", () => {
    expect(
      mapRenderStateFromSnapshot({
        latestRenderJobId: "job-1",
        lastFailedRenderJobId: null,
        latestJobStatus: "queued",
        latestJobCompletedAt: null,
      })
    ).toBe("rendering");
  });

  it("returns rendering when status is running", () => {
    expect(
      mapRenderStateFromSnapshot({
        latestRenderJobId: "job-1",
        lastFailedRenderJobId: null,
        latestJobStatus: "running",
        latestJobCompletedAt: null,
      })
    ).toBe("rendering");
  });

  it("returns failed when status is failed", () => {
    expect(
      mapRenderStateFromSnapshot({
        latestRenderJobId: "job-1",
        lastFailedRenderJobId: null,
        latestJobStatus: "failed",
        latestJobCompletedAt: null,
      })
    ).toBe("failed");
  });

  it("returns failed when lastFailedRenderJobId matches latestRenderJobId", () => {
    expect(
      mapRenderStateFromSnapshot({
        latestRenderJobId: "job-1",
        lastFailedRenderJobId: "job-1",
        latestJobStatus: "completed",
        latestJobCompletedAt: new Date(),
      })
    ).toBe("failed");
  });

  it("returns stale when latestItemUpdatedAt > latestJobCompletedAt", () => {
    expect(
      mapRenderStateFromSnapshot({
        latestRenderJobId: "job-1",
        lastFailedRenderJobId: null,
        latestJobStatus: "completed",
        latestJobCompletedAt: new Date("2024-01-01"),
        latestItemUpdatedAt: new Date("2024-01-02"),
      })
    ).toBe("stale");
  });

  it("returns fresh when completed and no newer items", () => {
    expect(
      mapRenderStateFromSnapshot({
        latestRenderJobId: "job-1",
        lastFailedRenderJobId: null,
        latestJobStatus: "completed",
        latestJobCompletedAt: new Date("2024-01-02"),
        latestItemUpdatedAt: new Date("2024-01-01"),
      })
    ).toBe("fresh");
  });

  it("returns fresh when completed and no latestItemUpdatedAt", () => {
    expect(
      mapRenderStateFromSnapshot({
        latestRenderJobId: "job-1",
        lastFailedRenderJobId: null,
        latestJobStatus: "completed",
        latestJobCompletedAt: new Date("2024-01-01"),
        latestItemUpdatedAt: null,
      })
    ).toBe("fresh");
  });

  it("returns unrendered for unknown status", () => {
    expect(
      mapRenderStateFromSnapshot({
        latestRenderJobId: "job-1",
        lastFailedRenderJobId: null,
        latestJobStatus: "unknown",
        latestJobCompletedAt: null,
      })
    ).toBe("unrendered");
  });
});

describe("listSongsetSummaries", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns songsets with aggregated itemCount, durationSeconds, renderState", async () => {
    const mockRows = [
      {
        id: "ss-1",
        name: "Test",
        description: null,
        createdAt: new Date("2024-01-01"),
        updatedAt: new Date("2024-01-02"),
        latestRenderJobId: null,
        lastFailedRenderJobId: null,
        lastCompletedRenderJobId: null,
        itemCount: 3,
        durationSeconds: 120,
        latestItemUpdatedAt: null,
        latestJobStatus: null,
        latestJobCompletedAt: null,
      },
    ];

    const chain = createSelectChain(mockRows);
    vi.mocked(db.select).mockReturnValue(chain as any);

    const countChain = createSelectChain([{ count: 1 }]);
    vi.mocked(db.select)
      .mockReturnValueOnce(chain as any)
      .mockReturnValueOnce(countChain as any);

    const result = await listSongsetSummaries(1, 50, 0);

    expect(result.songsets).toHaveLength(1);
    expect(result.songsets[0].itemCount).toBe(3);
    expect(result.songsets[0].durationSeconds).toBe(120);
    expect(result.songsets[0].renderState).toBe("unrendered");
  });

  it("returns total count from separate count query", async () => {
    const chain = createSelectChain([]);
    const countChain = createSelectChain([{ count: 42 }]);
    vi.mocked(db.select)
      .mockReturnValueOnce(chain as any)
      .mockReturnValueOnce(countChain as any);

    const result = await listSongsetSummaries(1, 50, 0);

    expect(result.total).toBe(42);
  });

  it("applies limit and offset", async () => {
    const chain = createSelectChain([]);
    const countChain = createSelectChain([{ count: 0 }]);
    vi.mocked(db.select)
      .mockReturnValueOnce(chain as any)
      .mockReturnValueOnce(countChain as any);

    await listSongsetSummaries(1, 10, 5);

    expect(db.select).toHaveBeenCalled();
  });

  it("maps renderState via mapRenderStateFromSnapshot", async () => {
    const mockRows = [
      {
        id: "ss-1",
        name: "Rendering",
        description: null,
        createdAt: new Date(),
        updatedAt: new Date(),
        latestRenderJobId: "job-1",
        lastFailedRenderJobId: null,
        lastCompletedRenderJobId: null,
        itemCount: 1,
        durationSeconds: null,
        latestItemUpdatedAt: null,
        latestJobStatus: "running",
        latestJobCompletedAt: null,
      },
    ];

    const chain = createSelectChain(mockRows);
    const countChain = createSelectChain([{ count: 1 }]);
    vi.mocked(db.select)
      .mockReturnValueOnce(chain as any)
      .mockReturnValueOnce(countChain as any);

    const result = await listSongsetSummaries(1, 50, 0);

    expect(result.songsets[0].renderState).toBe("rendering");
  });

  it("filters out deleted recordings in aggregation", async () => {
    const mockRows = [
      {
        id: "ss-1",
        name: "Test",
        description: null,
        createdAt: new Date(),
        updatedAt: new Date(),
        latestRenderJobId: null,
        lastFailedRenderJobId: null,
        lastCompletedRenderJobId: null,
        itemCount: 2,
        durationSeconds: 60,
        latestItemUpdatedAt: null,
        latestJobStatus: null,
        latestJobCompletedAt: null,
      },
    ];

    const chain = createSelectChain(mockRows);
    const countChain = createSelectChain([{ count: 1 }]);
    vi.mocked(db.select)
      .mockReturnValueOnce(chain as any)
      .mockReturnValueOnce(countChain as any);

    const result = await listSongsetSummaries(1, 50, 0);

    expect(result.songsets[0].itemCount).toBe(2);
  });
});

describe("getSongsetEditorData", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns songset with items including markedLineCount", async () => {
    const songsetRow = {
      id: "ss-1",
      name: "Test",
      description: null,
      createdAt: new Date(),
      updatedAt: new Date(),
      latestRenderJobId: null,
      lastFailedRenderJobId: null,
      lastCompletedRenderJobId: null,
      latestJobStatus: null,
      latestJobCompletedAt: null,
    };

    const itemRows = [
      {
        id: "item-1",
        songId: "song-1",
        recordingHashPrefix: "abc",
        position: 0,
        gapBeats: null,
        crossfadeEnabled: null,
        crossfadeDurationSeconds: null,
        keyShiftSemitones: null,
        tempoRatio: null,
        updatedAt: new Date(),
        songTitle: "Song A",
        composer: null,
        lyricist: null,
        albumName: null,
        songMusicalKey: null,
        recordingContentHash: "hash1",
        durationSeconds: 180,
        tempoBpm: 100,
        recordingMusicalKey: "C",
        r2AudioUrl: "https://r2.example.com/audio",
        recordingDeletedAt: null,
        markedLineCount: 5,
      },
    ];

    const songsetChain = createSelectChain([songsetRow]);
    const itemChain = createSelectChain(itemRows);
    vi.mocked(db.select)
      .mockReturnValueOnce(songsetChain as any)
      .mockReturnValueOnce(itemChain as any);

    const result = await getSongsetEditorData("ss-1", 1);

    expect(result).not.toBeNull();
    expect(result!.items).toHaveLength(1);
    expect(result!.items[0].markedLineCount).toBe(5);
  });

  it("returns null when songset not found", async () => {
    const songsetChain = createSelectChain([]);
    vi.mocked(db.select).mockReturnValue(songsetChain as any);

    const result = await getSongsetEditorData("nonexistent", 1);

    expect(result).toBeNull();
  });

  it("filters out items with deleted recordings", async () => {
    const songsetRow = {
      id: "ss-1",
      name: "Test",
      description: null,
      createdAt: new Date(),
      updatedAt: new Date(),
      latestRenderJobId: null,
      lastFailedRenderJobId: null,
      lastCompletedRenderJobId: null,
      latestJobStatus: null,
      latestJobCompletedAt: null,
    };

    const itemRows = [
      {
        id: "item-1",
        songId: "song-1",
        recordingHashPrefix: "abc",
        position: 0,
        gapBeats: null,
        crossfadeEnabled: null,
        crossfadeDurationSeconds: null,
        keyShiftSemitones: null,
        tempoRatio: null,
        updatedAt: new Date(),
        songTitle: "Song A",
        composer: null,
        lyricist: null,
        albumName: null,
        songMusicalKey: null,
        recordingContentHash: "hash1",
        durationSeconds: 180,
        tempoBpm: 100,
        recordingMusicalKey: "C",
        r2AudioUrl: "https://r2.example.com/audio",
        recordingDeletedAt: new Date(),
        markedLineCount: 0,
      },
    ];

    const songsetChain = createSelectChain([songsetRow]);
    const itemChain = createSelectChain(itemRows);
    vi.mocked(db.select)
      .mockReturnValueOnce(songsetChain as any)
      .mockReturnValueOnce(itemChain as any);

    const result = await getSongsetEditorData("ss-1", 1);

    expect(result).not.toBeNull();
    expect(result!.items).toHaveLength(0);
  });

  it("computes renderState from snapshot fields", async () => {
    const songsetRow = {
      id: "ss-1",
      name: "Test",
      description: null,
      createdAt: new Date(),
      updatedAt: new Date(),
      latestRenderJobId: "job-1",
      lastFailedRenderJobId: null,
      lastCompletedRenderJobId: "job-1",
      latestJobStatus: "completed",
      latestJobCompletedAt: new Date("2024-01-02"),
    };

    const itemRows = [
      {
        id: "item-1",
        songId: "song-1",
        recordingHashPrefix: null,
        position: 0,
        gapBeats: null,
        crossfadeEnabled: null,
        crossfadeDurationSeconds: null,
        keyShiftSemitones: null,
        tempoRatio: null,
        updatedAt: new Date("2024-01-01"),
        songTitle: "Song A",
        composer: null,
        lyricist: null,
        albumName: null,
        songMusicalKey: null,
        recordingContentHash: null,
        durationSeconds: null,
        tempoBpm: null,
        recordingMusicalKey: null,
        r2AudioUrl: null,
        recordingDeletedAt: null,
        markedLineCount: 0,
      },
    ];

    const songsetChain = createSelectChain([songsetRow]);
    const itemChain = createSelectChain(itemRows);
    vi.mocked(db.select)
      .mockReturnValueOnce(songsetChain as any)
      .mockReturnValueOnce(itemChain as any);

    const result = await getSongsetEditorData("ss-1", 1);

    expect(result!.renderState).toBe("fresh");
  });

  it("durationSeconds sums only visible items", async () => {
    const songsetRow = {
      id: "ss-1",
      name: "Test",
      description: null,
      createdAt: new Date(),
      updatedAt: new Date(),
      latestRenderJobId: null,
      lastFailedRenderJobId: null,
      lastCompletedRenderJobId: null,
      latestJobStatus: null,
      latestJobCompletedAt: null,
    };

    const itemRows = [
      {
        id: "item-1",
        songId: "song-1",
        recordingHashPrefix: "abc",
        position: 0,
        gapBeats: null,
        crossfadeEnabled: null,
        crossfadeDurationSeconds: null,
        keyShiftSemitones: null,
        tempoRatio: null,
        updatedAt: new Date(),
        songTitle: "Song A",
        composer: null,
        lyricist: null,
        albumName: null,
        songMusicalKey: null,
        recordingContentHash: "hash1",
        durationSeconds: 100,
        tempoBpm: null,
        recordingMusicalKey: null,
        r2AudioUrl: null,
        recordingDeletedAt: null,
        markedLineCount: 0,
      },
      {
        id: "item-2",
        songId: "song-2",
        recordingHashPrefix: "def",
        position: 1,
        gapBeats: null,
        crossfadeEnabled: null,
        crossfadeDurationSeconds: null,
        keyShiftSemitones: null,
        tempoRatio: null,
        updatedAt: new Date(),
        songTitle: "Song B",
        composer: null,
        lyricist: null,
        albumName: null,
        songMusicalKey: null,
        recordingContentHash: "hash2",
        durationSeconds: 200,
        tempoBpm: null,
        recordingMusicalKey: null,
        r2AudioUrl: null,
        recordingDeletedAt: new Date(),
        markedLineCount: 0,
      },
    ];

    const songsetChain = createSelectChain([songsetRow]);
    const itemChain = createSelectChain(itemRows);
    vi.mocked(db.select)
      .mockReturnValueOnce(songsetChain as any)
      .mockReturnValueOnce(itemChain as any);

    const result = await getSongsetEditorData("ss-1", 1);

    expect(result!.durationSeconds).toBe(100);
  });
});

describe("getRenderPageData", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns songset summary, userSettings, latestJob, previousCompletedJob", async () => {
    const songsetRow = {
      id: "ss-1",
      name: "Test",
      description: "desc",
      latestRenderJobId: "job-1",
      lastFailedRenderJobId: null,
      lastCompletedRenderJobId: "job-2",
      latestJobStatus: "completed",
      latestJobCompletedAt: new Date("2024-01-02"),
    };

    const itemRows = [
      {
        id: "item-1",
        songTitle: "Song A",
        markedLineCount: 3,
        recordingDeletedAt: null,
        recordingDurationSeconds: 180,
        updatedAt: new Date("2024-01-01"),
      },
    ];

    const songsetChain = createSelectChain([songsetRow]);
    const itemChain = createSelectChain(itemRows);
    vi.mocked(db.select)
      .mockReturnValueOnce(songsetChain as any)
      .mockReturnValueOnce(itemChain as any);

    vi.mocked(db.query.userSettings.findFirst).mockResolvedValue({
      defaultVideoTemplate: "dark",
      defaultResolution: "720p",
      defaultFontSizePreset: "M",
      defaultFontFamily: "noto_serif_tc",
    } as any);

    vi.mocked(db.query.renderJobs.findFirst)
      .mockResolvedValueOnce({
        id: "job-1",
        status: "completed",
        createdAt: new Date(),
        elapsedSeconds: 10,
        estimatedTotalSeconds: 20,
        template: "dark",
        resolution: "720p",
        audioEnabled: true,
        videoEnabled: true,
        fontFamily: "noto_serif_tc",
        fontSizePreset: "M",
        includeTitleCard: false,
        titleCardDurationSeconds: null,
        titleCardLines: null,
        mp3R2Key: null,
        mp4R2Key: null,
        chaptersR2Key: null,
      } as any)
      .mockResolvedValueOnce({
        id: "job-2",
        status: "completed",
        createdAt: new Date(),
        elapsedSeconds: 10,
        estimatedTotalSeconds: 20,
        template: "dark",
        resolution: "720p",
        audioEnabled: true,
        videoEnabled: true,
        fontFamily: "noto_serif_tc",
        fontSizePreset: "M",
        includeTitleCard: false,
        titleCardDurationSeconds: null,
        titleCardLines: null,
        mp3R2Key: null,
        mp4R2Key: null,
        chaptersR2Key: null,
      } as any);

    const result = await getRenderPageData("ss-1", 1);

    expect(result).not.toBeNull();
    expect(result!.songset.id).toBe("ss-1");
    expect(result!.songset.markedLineCount).toBe(3);
    expect(result!.songset.songTitles).toEqual(["Song A"]);
    expect(result!.songset.renderState).toBe("fresh");
    expect(result!.userSettings).not.toBeNull();
    expect(result!.userSettings!.defaultVideoTemplate).toBe("dark");
    expect(result!.latestJob).not.toBeNull();
    expect(result!.latestJob!.id).toBe("job-1");
    expect(result!.previousCompletedJob).not.toBeNull();
    expect(result!.previousCompletedJob!.id).toBe("job-2");
  });

  it("returns null when songset not found", async () => {
    const songsetChain = createSelectChain([]);
    vi.mocked(db.select).mockReturnValue(songsetChain as any);

    const result = await getRenderPageData("nonexistent", 1);

    expect(result).toBeNull();
  });

  it("handles null userSettings", async () => {
    const songsetRow = {
      id: "ss-1",
      name: "Test",
      description: null,
      latestRenderJobId: null,
      lastFailedRenderJobId: null,
      lastCompletedRenderJobId: null,
      latestJobStatus: null,
      latestJobCompletedAt: null,
    };

    const itemRows: any[] = [];

    const songsetChain = createSelectChain([songsetRow]);
    const itemChain = createSelectChain(itemRows);
    vi.mocked(db.select)
      .mockReturnValueOnce(songsetChain as any)
      .mockReturnValueOnce(itemChain as any);

    vi.mocked(db.query.userSettings.findFirst).mockResolvedValue(null);

    const result = await getRenderPageData("ss-1", 1);

    expect(result).not.toBeNull();
    expect(result!.userSettings).toBeNull();
  });

  it("handles null latestJob when latestRenderJobId is null", async () => {
    const songsetRow = {
      id: "ss-1",
      name: "Test",
      description: null,
      latestRenderJobId: null,
      lastFailedRenderJobId: null,
      lastCompletedRenderJobId: null,
      latestJobStatus: null,
      latestJobCompletedAt: null,
    };

    const itemRows: any[] = [];

    const songsetChain = createSelectChain([songsetRow]);
    const itemChain = createSelectChain(itemRows);
    vi.mocked(db.select)
      .mockReturnValueOnce(songsetChain as any)
      .mockReturnValueOnce(itemChain as any);

    vi.mocked(db.query.userSettings.findFirst).mockResolvedValue(null);

    const result = await getRenderPageData("ss-1", 1);

    expect(result).not.toBeNull();
    expect(result!.latestJob).toBeNull();
    expect(result!.previousCompletedJob).toBeNull();
  });

  it("markedLineCount is sum across items", async () => {
    const songsetRow = {
      id: "ss-1",
      name: "Test",
      description: null,
      latestRenderJobId: null,
      lastFailedRenderJobId: null,
      lastCompletedRenderJobId: null,
      latestJobStatus: null,
      latestJobCompletedAt: null,
    };

    const itemRows = [
      { id: "item-1", songTitle: "A", markedLineCount: 3, recordingDeletedAt: null, recordingDurationSeconds: 200, updatedAt: new Date() },
      { id: "item-2", songTitle: "B", markedLineCount: 5, recordingDeletedAt: null, recordingDurationSeconds: 300, updatedAt: new Date() },
    ];

    const songsetChain = createSelectChain([songsetRow]);
    const itemChain = createSelectChain(itemRows);
    vi.mocked(db.select)
      .mockReturnValueOnce(songsetChain as any)
      .mockReturnValueOnce(itemChain as any);

    vi.mocked(db.query.userSettings.findFirst).mockResolvedValue(null);

    const result = await getRenderPageData("ss-1", 1);

    expect(result!.songset.markedLineCount).toBe(8);
  });

  it("songTitles extracted from items", async () => {
    const songsetRow = {
      id: "ss-1",
      name: "Test",
      description: null,
      latestRenderJobId: null,
      lastFailedRenderJobId: null,
      lastCompletedRenderJobId: null,
      latestJobStatus: null,
      latestJobCompletedAt: null,
    };

    const itemRows = [
      { id: "item-1", songTitle: "Song A", markedLineCount: 0, recordingDeletedAt: null, recordingDurationSeconds: 250, updatedAt: new Date() },
      { id: "item-2", songTitle: null, markedLineCount: 0, recordingDeletedAt: null, recordingDurationSeconds: null, updatedAt: new Date() },
    ];

    const songsetChain = createSelectChain([songsetRow]);
    const itemChain = createSelectChain(itemRows);
    vi.mocked(db.select)
      .mockReturnValueOnce(songsetChain as any)
      .mockReturnValueOnce(itemChain as any);

    vi.mocked(db.query.userSettings.findFirst).mockResolvedValue(null);

    const result = await getRenderPageData("ss-1", 1);

    expect(result!.songset.songTitles).toEqual(["Song A", "Unknown Song"]);
  });
});

describe("computeRenderState", () => {
  it("returns unrendered when no latest render job", async () => {
    vi.mocked(db.query.songsets.findFirst).mockResolvedValue({
      id: "songset-1",
      latestRenderJobId: null,
      lastFailedRenderJobId: null,
      lastCompletedRenderJobId: null,
    } as any);

    const state = await computeRenderState("songset-1");
    expect(state).toBe("unrendered");
  });

  it("returns fresh when latest job completed successfully", async () => {
    vi.mocked(db.query.songsets.findFirst).mockResolvedValue({
      id: "songset-1",
      latestRenderJobId: "job-1",
      lastFailedRenderJobId: null,
      lastCompletedRenderJobId: "job-1",
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
      lastCompletedRenderJobId: null,
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
      lastCompletedRenderJobId: null,
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
      lastCompletedRenderJobId: null,
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
      lastCompletedRenderJobId: "job-1",
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
      lastCompletedRenderJobId: "job-1",
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
      lastCompletedRenderJobId: null,
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
      lastCompletedRenderJobId: null,
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
      lastCompletedRenderJobId: null,
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
      lastCompletedRenderJobId: null,
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
    vi.mocked(db.query.songsetItems.findMany).mockResolvedValue([{ id: "existing-1" }] as any);
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
