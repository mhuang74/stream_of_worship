import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  duplicateSongset,
  getSongsetEditorData,
  getSongsetPublicView,
  getSongsetRenderMetrics,
  SongsetRenderValidationError,
  updateSongArrangement,
  updateSongsetItem,
} from "@/lib/db/songsets";
import { db } from "@/db";
import { songsets, songsetItems } from "@/db/schema";
import type { SongArrangement } from "@/lib/arrangements";

vi.mock("@/db", () => ({
  db: {
    query: {
      songsets: { findFirst: vi.fn() },
      renderJobs: { findFirst: vi.fn(), findMany: vi.fn() },
      songsetItems: { findFirst: vi.fn(), findMany: vi.fn() },
      userSettings: { findFirst: vi.fn() },
    },
    select: vi.fn(),
    insert: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
  },
}));

vi.mock("nanoid", () => ({
  nanoid: vi.fn(() => "new-id"),
}));

type MockSelectChain = {
  from: ReturnType<typeof vi.fn>;
  leftJoin: ReturnType<typeof vi.fn>;
  innerJoin: ReturnType<typeof vi.fn>;
  where: ReturnType<typeof vi.fn>;
  groupBy: ReturnType<typeof vi.fn>;
  orderBy: ReturnType<typeof vi.fn>;
  limit: ReturnType<typeof vi.fn>;
  offset: ReturnType<typeof vi.fn>;
  then: (resolve: (rows: unknown[]) => unknown) => Promise<unknown>;
};

function createSelectChain(rows: unknown[]): MockSelectChain {
  const promise = Promise.resolve(rows);
  const chain = {} as MockSelectChain;
  for (const method of [
    "from",
    "leftJoin",
    "innerJoin",
    "where",
    "groupBy",
    "orderBy",
    "limit",
    "offset",
  ] as const) {
    chain[method] = vi.fn().mockReturnValue(chain);
  }
  chain.then = (resolve) => promise.then(resolve);
  return chain;
}

function useSelectRows(...rowSets: unknown[][]) {
  const mock = vi.mocked(db.select);
  for (const rows of rowSets) {
    mock.mockReturnValueOnce(
      createSelectChain(rows) as unknown as ReturnType<typeof db.select>
    );
  }
}

interface UpdateCall {
  table: unknown;
  values: Record<string, unknown>;
}

/** Capture every `update(table).set(values).where(...)` payload. */
function mockUpdates(): UpdateCall[] {
  const calls: UpdateCall[] = [];
  vi.mocked(db.update).mockImplementation(((table: unknown) => ({
    set: (values: Record<string, unknown>) => {
      calls.push({ table, values });
      return {
        where: () => ({
          returning: vi.fn().mockResolvedValue([]),
          then: (resolve: (value: unknown) => unknown) =>
            Promise.resolve(undefined).then(resolve),
        }),
      };
    },
  })) as unknown as typeof db.update);
  return calls;
}

type SongsFindFirstResult = NonNullable<
  Awaited<ReturnType<typeof db.query.songsets.findFirst>>
>;
type ItemsFindFirstResult = NonNullable<
  Awaited<ReturnType<typeof db.query.songsetItems.findFirst>>
>;

const introSection = { label: "intro", start: 0, end: 10 };
const verseSection = { label: "verse", start: 10, end: 30 };
const sectionsJson = JSON.stringify([introSection, verseSection]);
const beatsJson = JSON.stringify([0, 5, 10, 15, 20, 25, 30]);

const repeatedIntroArrangement: SongArrangement = {
  version: 1,
  steps: [
    { sectionIndex: 0, label: "intro", startSeconds: 0, endSeconds: 10 },
    { sectionIndex: 0, label: "intro", startSeconds: 0, endSeconds: 10 },
  ],
};

const singleIntroArrangement: SongArrangement = {
  version: 1,
  steps: [{ sectionIndex: 0, label: "intro", startSeconds: 0, endSeconds: 10 }],
};

/** Owned SongsetItem row returned by the guarded `findFirst` lookups. */
function ownedItemRow(extra: Record<string, unknown> = {}) {
  return {
    id: "item-1",
    songsetId: "songset-1",
    songset: { userId: 1 },
    songId: "song-1",
    recordingHashPrefix: "abc",
    position: 0,
    gapBeats: 0,
    crossfadeEnabled: 0,
    crossfadeDurationSeconds: null,
    keyShiftSemitones: 0,
    tempoRatio: 1,
    arrangementSteps: null,
    createdAt: new Date(),
    updatedAt: new Date(),
    song: null,
    recording: null,
    ...extra,
  };
}

/** Item row as selected by the arrangement/duration queries. */
function arrangementItemRow(extra: Record<string, unknown> = {}) {
  return {
    id: "item-1",
    arrangementSteps: null,
    gapBeats: 0,
    crossfadeEnabled: 0,
    crossfadeDurationSeconds: null,
    durationSeconds: 180,
    tempoBpm: 120,
    recordingSections: sectionsJson,
    recordingBeats: beatsJson,
    recordingDeletedAt: null,
    ...extra,
  };
}

function recordingRow() {
  return {
    contentHash: "hash-1",
    durationSeconds: 180,
    tempoBpm: 120,
    musicalKey: "C",
    musicalMode: null,
    keyConfidence: null,
    keyScoreMargin: null,
    keyWindowAgreement: null,
    r2AudioUrl: null,
    theme: null,
    sections: sectionsJson,
    beats: beatsJson,
    lyricMarks: [],
  };
}

describe("updateSongArrangement", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("persists repeated Steps identically and bumps item and Songset timestamps", async () => {
    vi.mocked(db.query.songsetItems.findFirst)
      .mockResolvedValueOnce(ownedItemRow() as unknown as ItemsFindFirstResult)
      .mockResolvedValueOnce(
        ownedItemRow({
          arrangementSteps: JSON.stringify(repeatedIntroArrangement),
          recording: recordingRow(),
        }) as unknown as ItemsFindFirstResult
      );
    useSelectRows([arrangementItemRow()]);
    const updates = mockUpdates();

    const result = await updateSongArrangement(
      "item-1",
      "songset-1",
      1,
      repeatedIntroArrangement
    );

    expect(result).not.toBeNull();
    expect(result!.arrangementInvalid).toBe(false);
    expect(result!.arrangement?.steps).toHaveLength(2);
    // Both occurrences keep identical source ranges.
    expect(result!.arrangement!.steps[0]).toEqual(result!.arrangement!.steps[1]);

    const itemUpdate = updates.find((call) => call.table === songsetItems);
    const songsetUpdate = updates.find((call) => call.table === songsets);
    expect(itemUpdate).toBeDefined();
    expect(songsetUpdate).toBeDefined();
    expect(itemUpdate!.values.arrangementSteps).toBe(
      JSON.stringify(repeatedIntroArrangement)
    );
    expect(itemUpdate!.values.updatedAt).toBeInstanceOf(Date);
    expect(songsetUpdate!.values.updatedAt).toBeInstanceOf(Date);
  });

  it("rejects a stored arrangement that no longer matches changed beats with ARRANGEMENT_STALE", async () => {
    vi.mocked(db.query.songsetItems.findFirst).mockResolvedValueOnce(
      ownedItemRow() as unknown as ItemsFindFirstResult
    );
    useSelectRows([
      arrangementItemRow({
        arrangementSteps: JSON.stringify(singleIntroArrangement),
        recordingBeats: JSON.stringify([0, 4, 8, 12, 16, 20, 24, 28, 32]),
      }),
    ]);
    mockUpdates();

    await expect(
      updateSongArrangement("item-1", "songset-1", 1, singleIntroArrangement)
    ).rejects.toMatchObject({ code: "ARRANGEMENT_STALE" });
  });

  it("rejects a save with missing analysis with ARRANGEMENT_ANALYSIS_UNAVAILABLE", async () => {
    vi.mocked(db.query.songsetItems.findFirst).mockResolvedValueOnce(
      ownedItemRow() as unknown as ItemsFindFirstResult
    );
    useSelectRows([
      arrangementItemRow({
        durationSeconds: null,
        recordingSections: null,
        recordingBeats: null,
      }),
    ]);
    mockUpdates();

    await expect(
      updateSongArrangement("item-1", "songset-1", 1, singleIntroArrangement)
    ).rejects.toMatchObject({ code: "ARRANGEMENT_ANALYSIS_UNAVAILABLE" });
  });

  it("resets with NULL even without analysis and bumps both timestamps", async () => {
    vi.mocked(db.query.songsetItems.findFirst)
      .mockResolvedValueOnce(
        ownedItemRow({
          arrangementSteps: JSON.stringify(singleIntroArrangement),
        }) as unknown as ItemsFindFirstResult
      )
      .mockResolvedValueOnce(
        ownedItemRow({
          arrangementSteps: null,
          recording: recordingRow(),
        }) as unknown as ItemsFindFirstResult
      );
    useSelectRows([
      arrangementItemRow({
        durationSeconds: null,
        recordingSections: null,
        recordingBeats: null,
      }),
    ]);
    const updates = mockUpdates();

    const result = await updateSongArrangement("item-1", "songset-1", 1, null);

    expect(result).not.toBeNull();
    expect(result!.arrangementSteps).toBeNull();
    expect(result!.arrangement).toBeNull();
    const itemUpdate = updates.find((call) => call.table === songsetItems);
    expect(itemUpdate!.values.arrangementSteps).toBeNull();
    expect(itemUpdate!.values.updatedAt).toBeInstanceOf(Date);
    expect(updates.find((call) => call.table === songsets)).toBeDefined();
  });

  it("rejects a 1501-second prospective selected total with SONGSET_TOO_LONG", async () => {
    vi.mocked(db.query.songsetItems.findFirst).mockResolvedValueOnce(
      ownedItemRow() as unknown as ItemsFindFirstResult
    );
    useSelectRows([
      // Draft contributes 10s; the sibling keeps its full 1495s recording.
      arrangementItemRow(),
      arrangementItemRow({
        id: "item-2",
        durationSeconds: 1495,
        recordingSections: null,
        recordingBeats: null,
      }),
    ]);
    mockUpdates();

    await expect(
      updateSongArrangement("item-1", "songset-1", 1, singleIntroArrangement)
    ).rejects.toMatchObject({ code: "SONGSET_TOO_LONG" });
  });

  it("returns null for an unowned item", async () => {
    vi.mocked(db.query.songsetItems.findFirst).mockResolvedValueOnce(
      ownedItemRow({ songset: { userId: 2 } }) as unknown as ItemsFindFirstResult
    );

    const result = await updateSongArrangement("item-1", "songset-1", 1, null);

    expect(result).toBeNull();
  });
});

describe("getSongsetRenderMetrics", () => {
  const songsetRow = { id: "songset-1", userId: 1 };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns the arranged duration and item count", async () => {
    vi.mocked(db.query.songsets.findFirst).mockResolvedValue(
      songsetRow as unknown as SongsFindFirstResult
    );
    useSelectRows([
      arrangementItemRow({
        arrangementSteps: JSON.stringify(singleIntroArrangement),
      }),
      arrangementItemRow({ id: "item-2", durationSeconds: 40 }),
    ]);

    const metrics = await getSongsetRenderMetrics("songset-1", 1);

    // 10s arranged + 0s gap + 40s original.
    expect(metrics.songCount).toBe(2);
    expect(metrics.items).toHaveLength(2);
    expect(metrics.songsetDurationSeconds).toBe(50);
    expect(db.query.songsets.findFirst).toHaveBeenCalled();
  });

  it("throws ARRANGEMENT_ANALYSIS_UNAVAILABLE when a saved arrangement has no analysis", async () => {
    vi.mocked(db.query.songsets.findFirst).mockResolvedValue(
      songsetRow as unknown as SongsFindFirstResult
    );
    useSelectRows([
      arrangementItemRow({
        arrangementSteps: JSON.stringify(singleIntroArrangement),
        recordingSections: null,
        recordingBeats: null,
      }),
    ]);

    await expect(getSongsetRenderMetrics("songset-1", 1)).rejects.toMatchObject({
      code: "ARRANGEMENT_ANALYSIS_UNAVAILABLE",
    });
  });

  it("throws ARRANGEMENT_STALE when analysis changed", async () => {
    vi.mocked(db.query.songsets.findFirst).mockResolvedValue(
      songsetRow as unknown as SongsFindFirstResult
    );
    useSelectRows([
      arrangementItemRow({
        arrangementSteps: JSON.stringify(singleIntroArrangement),
        recordingBeats: JSON.stringify([0, 4, 8, 12, 16, 20, 24, 28, 32]),
      }),
    ]);

    await expect(getSongsetRenderMetrics("songset-1", 1)).rejects.toMatchObject({
      code: "ARRANGEMENT_STALE",
    });
  });

  it("throws ARRANGEMENT_INVALID for malformed persisted JSON", async () => {
    vi.mocked(db.query.songsets.findFirst).mockResolvedValue(
      songsetRow as unknown as SongsFindFirstResult
    );
    useSelectRows([arrangementItemRow({ arrangementSteps: "{not json" })]);

    await expect(getSongsetRenderMetrics("songset-1", 1)).rejects.toMatchObject({
      code: "ARRANGEMENT_INVALID",
    });
  });

  it("throws SONGSET_NOT_FOUND for a missing or unowned Songset", async () => {
    vi.mocked(db.query.songsets.findFirst).mockResolvedValue(null);

    await expect(getSongsetRenderMetrics("songset-1", 1)).rejects.toBeInstanceOf(
      SongsetRenderValidationError
    );
  });
});

describe("updateSongsetItem arrangement lifecycle", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("clears the arrangement and bumps updatedAt when the Recording changes", async () => {
    vi.mocked(db.query.songsetItems.findFirst)
      .mockResolvedValueOnce(
        ownedItemRow({
          recordingHashPrefix: "old",
          arrangementSteps: JSON.stringify(singleIntroArrangement),
        }) as unknown as ItemsFindFirstResult
      )
      .mockResolvedValueOnce(
        ownedItemRow({
          recordingHashPrefix: "new",
          recording: recordingRow(),
        }) as unknown as ItemsFindFirstResult
      );
    const updates = mockUpdates();

    await updateSongsetItem("item-1", "songset-1", 1, {
      recordingHashPrefix: "new",
    });

    const itemUpdate = updates.find((call) => call.table === songsetItems);
    expect(itemUpdate!.values.arrangementSteps).toBeNull();
    expect(itemUpdate!.values.updatedAt).toBeInstanceOf(Date);
  });

  it("preserves the arrangement but still bumps updatedAt for transition-only changes", async () => {
    vi.mocked(db.query.songsetItems.findFirst)
      .mockResolvedValueOnce(
        ownedItemRow({
          recordingHashPrefix: "abc",
          arrangementSteps: JSON.stringify(singleIntroArrangement),
        }) as unknown as ItemsFindFirstResult
      )
      .mockResolvedValueOnce(
        ownedItemRow({
          recordingHashPrefix: "abc",
          arrangementSteps: JSON.stringify(singleIntroArrangement),
          gapBeats: 4,
          recording: recordingRow(),
        }) as unknown as ItemsFindFirstResult
      );
    const updates = mockUpdates();

    await updateSongsetItem("item-1", "songset-1", 1, { gapBeats: 4 });

    const itemUpdate = updates.find((call) => call.table === songsetItems);
    expect(itemUpdate!.values).not.toHaveProperty("arrangementSteps");
    expect(itemUpdate!.values.updatedAt).toBeInstanceOf(Date);
  });

  it("does not clear the arrangement when the same Recording is re-applied", async () => {
    vi.mocked(db.query.songsetItems.findFirst)
      .mockResolvedValueOnce(
        ownedItemRow({
          recordingHashPrefix: "abc",
          arrangementSteps: JSON.stringify(singleIntroArrangement),
        }) as unknown as ItemsFindFirstResult
      )
      .mockResolvedValueOnce(
        ownedItemRow({ recordingHashPrefix: "abc", recording: recordingRow() }) as unknown as ItemsFindFirstResult
      );
    const updates = mockUpdates();

    await updateSongsetItem("item-1", "songset-1", 1, {
      recordingHashPrefix: "abc",
    });

    const itemUpdate = updates.find((call) => call.table === songsetItems);
    expect(itemUpdate!.values).not.toHaveProperty("arrangementSteps");
  });
});

describe("duplicateSongset", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("copies the raw arrangement JSON verbatim", async () => {
    const raw = JSON.stringify(repeatedIntroArrangement);
    const sourceRow = {
      id: "songset-1",
      userId: 1,
      name: "Source",
      description: null,
      createdAt: new Date(),
      updatedAt: new Date(),
      latestRenderJobId: null,
      lastFailedRenderJobId: null,
      lastCompletedRenderJobId: null,
      items: [
        {
          id: "item-1",
          songsetId: "songset-1",
          songId: "song-1",
          recordingHashPrefix: null,
          position: 0,
          gapBeats: 0,
          crossfadeEnabled: 0,
          crossfadeDurationSeconds: null,
          keyShiftSemitones: 0,
          tempoRatio: 1,
          arrangementSteps: raw,
          song: null,
          recording: null,
        },
      ],
    };

    vi.mocked(db.query.songsets.findFirst)
      .mockResolvedValueOnce(sourceRow as unknown as SongsFindFirstResult)
      // computeRenderState inside getSongset
      .mockResolvedValueOnce({
        latestRenderJobId: null,
      } as unknown as SongsFindFirstResult)
      // getSongset for the fresh duplicate
      .mockResolvedValueOnce({
        id: "new-id",
        userId: 1,
        name: "Copy",
        description: null,
        createdAt: new Date(),
        updatedAt: new Date(),
        latestRenderJobId: null,
        lastFailedRenderJobId: null,
        lastCompletedRenderJobId: null,
        items: [],
      } as unknown as SongsFindFirstResult)
      // computeRenderState for the fresh duplicate
      .mockResolvedValueOnce({
        latestRenderJobId: null,
      } as unknown as SongsFindFirstResult);

    const insertedValues: unknown[] = [];
    vi.mocked(db.insert).mockImplementation(((table: unknown) => ({
      values: (values: unknown) => {
        if (table === songsetItems) insertedValues.push(values);
        return {
          returning: vi.fn().mockResolvedValue([]),
          then: (resolve: (value: unknown) => unknown) =>
            Promise.resolve(undefined).then(resolve),
        };
      },
    })) as unknown as typeof db.insert);

    await duplicateSongset("songset-1", 1, "Copy", null);

    expect(insertedValues).toHaveLength(1);
    const rows = insertedValues[0] as Array<{ arrangementSteps: string | null }>;
    expect(rows[0].arrangementSteps).toBe(raw);
  });
});

describe("readers expose arranged durations", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("getSongsetEditorData totals the arranged duration", async () => {
    const songsetRow = {
      id: "songset-1",
      name: "Set",
      description: null,
      createdAt: new Date(),
      updatedAt: new Date(),
      latestRenderJobId: null,
      lastFailedRenderJobId: null,
      lastCompletedRenderJobId: null,
      latestJobStatus: null,
      latestJobCompletedAt: null,
      renderErrorMessage: null,
      latestJobStartedAt: null,
      latestJobCreatedAt: null,
    };
    const itemRow = {
      id: "item-1",
      songId: "song-1",
      recordingHashPrefix: "abc",
      position: 0,
      gapBeats: 0,
      crossfadeEnabled: 0,
      crossfadeDurationSeconds: null,
      keyShiftSemitones: 0,
      tempoRatio: 1,
      arrangementSteps: JSON.stringify(repeatedIntroArrangement),
      updatedAt: new Date(),
      songTitle: "Song A",
      composer: null,
      lyricist: null,
      albumName: null,
      songMusicalKey: null,
      recordingContentHash: "hash-1",
      durationSeconds: 180,
      tempoBpm: 120,
      recordingSections: sectionsJson,
      recordingBeats: beatsJson,
      recordingMusicalKey: "C",
      recordingMusicalMode: null,
      keyConfidence: null,
      keyScoreMargin: null,
      keyWindowAgreement: null,
      r2AudioUrl: null,
      recordingTheme: null,
      recordingDeletedAt: null,
      markedLineCount: 0,
    };
    useSelectRows([songsetRow], [itemRow]);

    const result = await getSongsetEditorData("songset-1", 1);

    expect(result).not.toBeNull();
    // Two repeated 10s Steps: the arranged duration, not the 180s recording.
    expect(result!.durationSeconds).toBe(20);
    expect(result!.items[0].arrangement?.steps).toHaveLength(2);
    expect(result!.items[0].recording?.sections).toEqual([
      introSection,
      verseSection,
    ]);
    expect(result!.items[0].recording?.beats).toHaveLength(7);
  });

  it("getSongsetEditorData exposes invalid arrangements as null durations", async () => {
    const songsetRow = {
      id: "songset-1",
      name: "Set",
      description: null,
      createdAt: new Date(),
      updatedAt: new Date(),
      latestRenderJobId: null,
      lastFailedRenderJobId: null,
      lastCompletedRenderJobId: null,
      latestJobStatus: null,
      latestJobCompletedAt: null,
      renderErrorMessage: null,
      latestJobStartedAt: null,
      latestJobCreatedAt: null,
    };
    const itemRow = {
      id: "item-1",
      songId: "song-1",
      recordingHashPrefix: "abc",
      position: 0,
      gapBeats: 0,
      crossfadeEnabled: 0,
      crossfadeDurationSeconds: null,
      keyShiftSemitones: 0,
      tempoRatio: 1,
      arrangementSteps: "{not json",
      updatedAt: new Date(),
      songTitle: "Song A",
      composer: null,
      lyricist: null,
      albumName: null,
      songMusicalKey: null,
      recordingContentHash: "hash-1",
      durationSeconds: 180,
      tempoBpm: 120,
      recordingSections: sectionsJson,
      recordingBeats: beatsJson,
      recordingMusicalKey: "C",
      recordingMusicalMode: null,
      keyConfidence: null,
      keyScoreMargin: null,
      keyWindowAgreement: null,
      r2AudioUrl: null,
      recordingTheme: null,
      recordingDeletedAt: null,
      markedLineCount: 0,
    };
    useSelectRows([songsetRow], [itemRow]);

    const result = await getSongsetEditorData("songset-1", 1);

    expect(result!.durationSeconds).toBeNull();
    expect(result!.items[0].arrangementInvalid).toBe(true);
    expect(result!.items[0].arrangement).toBeNull();
    expect(result!.items[0].arrangementSteps).toBe("{not json");
  });

  it("getSongsetPublicView totals only visible recordings and drops invalid arrangements", async () => {
    vi.mocked(db.query.songsets.findFirst).mockResolvedValue({
      id: "songset-1",
      name: "Shared",
      description: null,
      updatedAt: new Date(),
      latestRenderJobId: null,
      lastCompletedRenderJobId: null,
    } as unknown as SongsFindFirstResult);
    vi.mocked(db.query.renderJobs.findFirst).mockResolvedValue(null);
    useSelectRows([
      arrangementItemRow({ durationSeconds: 100 }),
      arrangementItemRow({
        id: "item-deleted",
        durationSeconds: 50,
        recordingDeletedAt: new Date(),
      }),
    ]);

    const view = await getSongsetPublicView("songset-1");

    expect(view!.items).toHaveLength(1);
    expect(view!.items[0].durationSeconds).toBe(100);
    // The soft-deleted recording must not contribute to the total.
    expect(view!.totalDurationSeconds).toBe(100);
  });

  it("getSongsetPublicView returns null totals for a structurally invalid arrangement", async () => {
    vi.mocked(db.query.songsets.findFirst).mockResolvedValue({
      id: "songset-1",
      name: "Shared",
      description: null,
      updatedAt: new Date(),
      latestRenderJobId: null,
      lastCompletedRenderJobId: null,
    } as unknown as SongsFindFirstResult);
    vi.mocked(db.query.renderJobs.findFirst).mockResolvedValue(null);
    useSelectRows([arrangementItemRow({ arrangementSteps: "{not json" })]);

    const view = await getSongsetPublicView("songset-1");

    expect(view!.items[0].durationSeconds).toBeNull();
    expect(view!.totalDurationSeconds).toBeNull();
  });
});
