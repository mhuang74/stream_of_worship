import { describe, it, expect, vi, beforeAll, afterAll } from "vitest";

// The journey deliberately mocks only the database boundary. Descriptor,
// quote math, the resolver, layout, preflight, and the render snapshot are the
// production modules; every row below is seeded from the Traditional Chinese
// fixture. This file never reads or sets SOW_ELEVENLABS_API_KEY and every
// network call is poisoned, so a paid provider call cannot silently occur.

vi.mock("@/db", () => ({ db: { select: vi.fn() } }));
vi.mock("@/db/mutation-db", () => ({ mutationDb: { transaction: vi.fn() } }));

import fixture from "../fixtures/tc-songset.cross_language.json";
import { db } from "@/db";
import { buildGlueDescriptor } from "@/lib/glue/descriptor";
import { getGlueQuote, type GlueResolverItem } from "@/lib/glue/quote";
import { buildGlueQuota } from "@/lib/glue/quota";
import {
  resolveGlueJunctions,
  type GlueTakeRow,
} from "@/lib/glue/resolve";
import {
  buildArrangementTimeline,
  getSongsetDurationSeconds,
  type TimelineItemSource,
} from "@/lib/arrangements";
import {
  buildGlueRenderPreflight,
  createRenderGlueSnapshot,
  type GlueRenderItemIdentity,
  type GlueRenderTransaction,
  type RenderGluePlan,
} from "@/lib/db/songsets";
import { parseLRC } from "@/lib/render/lrc-parser";

type FixtureSong = (typeof fixture.songs)[number];
type Row = Record<string, unknown>;

const OWNER_ID = 42;
const SONGSET_ID = fixture.songset.id;
const DESCRIPTOR_KEY = fixture.glue[0].descriptorKey;
const DESCRIPTOR_JSON = fixture.glue[0].descriptorJson;
const TAKE_ID = fixture.glue[0].takeId;
const R2_KEY = fixture.glue[0].r2Key;
const STAGED_SECONDS = fixture.glue[0].stagedSeconds;
const FROM_UPDATED_AT = "2026-11-22T10:00:00.000Z";
const TO_UPDATED_AT = "2026-11-22T10:01:00.000Z";
const PRECISE_UPDATED_AT = ["2026-11-22T10:00:00.000099Z", "2026-11-22T10:01:00.000000Z"];

const API_KEY_AT_LOAD = process.env.SOW_ELEVENLABS_API_KEY;
const fetchSpy = vi.fn(() => {
  throw new Error("network access is disabled in the journey");
});

beforeAll(() => {
  vi.stubGlobal("fetch", fetchSpy);
});

afterAll(() => {
  vi.unstubAllGlobals();
});

/** Thenable Drizzle select chain: every builder method returns the chain. */
function selectChain(rows: Row[]) {
  const chain = {
    from: () => chain,
    innerJoin: () => chain,
    leftJoin: () => chain,
    where: () => chain,
    orderBy: () => chain,
    groupBy: () => chain,
    limit: () => chain,
    for: () => chain,
    then: (
      resolve: (value: Row[]) => unknown,
      reject?: (reason: unknown) => unknown
    ) => Promise.resolve(rows).then(resolve, reject),
  };
  return chain;
}

/** Each `db.select(...)` call consumes the next seeded row set. */
function installDbRows(queue: Row[][]): void {
  const pending = [...queue];
  vi.mocked(db.select).mockImplementation(
    (() => selectChain(pending.shift() ?? [])) as never
  );
}

function installTxRows(queue: Row[][]): GlueRenderTransaction {
  const pending = [...queue];
  return {
    select: vi.fn(() => selectChain(pending.shift() ?? [])),
  } as unknown as GlueRenderTransaction;
}

function resolverItem(
  source: FixtureSong,
  updatedAt: string,
  glueActiveTakeId: string | null
): GlueResolverItem {
  return {
    itemId: source.itemId,
    position: source.position,
    userId: OWNER_ID,
    glueEnabled: true,
    glueBeats: 4,
    glueActiveTakeId,
    updatedAt,
    arrangementSteps: JSON.stringify(source.arrangement),
    recording: {
      contentHash: source.recording.contentHash,
      durationSeconds: source.recording.durationSeconds,
      tempoBpm: source.analysis.tempoBpm,
      sections: source.analysis.sections,
      beats: source.analysis.beats,
    },
  };
}

function timelineSource(
  source: FixtureSong,
  glue: { takeId: string; seconds: number } | null
): TimelineItemSource {
  return {
    itemId: source.itemId,
    recordingHashPrefix: source.recording.hashPrefix,
    recordingDurationSeconds: source.recording.durationSeconds,
    arrangementSteps: JSON.stringify(source.arrangement),
    gapBeats: source.gapBeats,
    crossfadeEnabled: source.crossfadeEnabled,
    crossfadeDurationSeconds: source.crossfadeDurationSeconds,
    tempoBpm: source.analysis.tempoBpm,
    recordingSections: source.analysis.sections,
    recordingBeats: source.analysis.beats,
    glueTakeId: glue?.takeId ?? null,
    glueSeconds: glue?.seconds ?? null,
  };
}

function takeRow(overrides: Partial<GlueTakeRow> = {}): GlueTakeRow {
  return {
    id: TAKE_ID,
    userId: OWNER_ID,
    descriptorKey: DESCRIPTOR_KEY,
    descriptorJson: DESCRIPTOR_JSON,
    ordinal: 1,
    status: "ready",
    r2Key: R2_KEY,
    seconds: STAGED_SECONDS,
    audioSha256: fixture.glue[0].sha256,
    metricsJson: JSON.stringify({ audioSha256: fixture.glue[0].sha256 }),
    reasonsJson: "[]",
    createdAt: FROM_UPDATED_AT,
    ...overrides,
  };
}

const ITEM_IDENTITIES: GlueRenderItemIdentity[] = fixture.songs.map((source) => ({
  id: source.itemId,
  position: source.position,
  title: source.title,
}));

const QUOTA = buildGlueQuota([]);

/**
 * UTF-8 byte equality: any normalization, transliteration, or truncation of a
 * title or lyric line changes the bytes even when the glyphs look identical.
 */
function expectByteIdentical(actual: string, expected: string): void {
  expect(Buffer.from(actual, "utf8").equals(Buffer.from(expected, "utf8"))).toBe(true);
  expect(actual).toBe(expected);
}

let plan: RenderGluePlan;

describe("AI transition journey over the Traditional Chinese fixture", () => {
  it("derives the fixture's canonical descriptor key from its Recordings, Arrangements, and beats", () => {
    expect(fixture.glue[0].beats).toBe(4);
    expect(fixture.glue[0].toTempoBpm).toBe(fixture.songs[1].analysis.tempoBpm);

    const built = buildGlueDescriptor({
      beats: 4,
      toTempoBpm: fixture.songs[1].analysis.tempoBpm,
      from: {
        contentHash: fixture.songs[0].recording.contentHash,
        steps: fixture.songs[0].arrangement.steps.map((step) => [step.startSeconds, step.endSeconds]),
      },
      to: {
        contentHash: fixture.songs[1].recording.contentHash,
        steps: fixture.songs[1].arrangement.steps.map((step) => [step.startSeconds, step.endSeconds]),
      },
    });

    expect(built.key).toMatch(/^[0-9a-f]{64}$/);
    expect(built.key).toBe(DESCRIPTOR_KEY);
    expect(built.canonicalJson).toBe(DESCRIPTOR_JSON);
    // Titles are display-only: the identity text carries no Chinese title.
    for (const source of fixture.songs) {
      expect(built.canonicalJson).not.toContain(source.title);
    }
  });

  it("quotes the initial paid attempt at 3.0 s and a full quota", async () => {
    // loadGlueItemRows, loadExcerptKeySets (nothing cached), readGlueQuota.
    installDbRows([
      fixture.songs.map((source) => ({
        songsetId: SONGSET_ID,
        itemId: source.itemId,
        position: source.position,
        userId: OWNER_ID,
        glueEnabled: true,
        glueBeats: 4,
        glueActiveTakeId: source.position === 0 ? TAKE_ID : null,
        updatedAt: new Date(
          source.position === 0 ? FROM_UPDATED_AT : TO_UPDATED_AT
        ),
        arrangementSteps: JSON.stringify(source.arrangement),
        contentHash: source.recording.contentHash,
        durationSeconds: source.recording.durationSeconds,
        tempoBpm: source.analysis.tempoBpm,
        sections: JSON.stringify(source.analysis.sections),
        beats: JSON.stringify(source.analysis.beats),
        recordingDeletedAt: null,
      })),
      [],
      [],
    ]);

    const quote = await getGlueQuote({
      songsetId: SONGSET_ID,
      userId: OWNER_ID,
      fromItemId: fixture.songs[0].itemId,
      takeMode: "initial",
    });
    expect(quote).not.toBeNull();
    if (quote === null) return;

    expect(quote.descriptorKey).toBe(DESCRIPTOR_KEY);
    expect(quote.generationSeconds).toBe(3.0);
    expect(quote.generationSeconds).toBe(fixture.glue[0].generationSeconds);
    // Both excerpts are 12 s of arranged audio, so upload credits are the
    // module's 15-credits-per-second rate times 24 s; generation is 15 x 3 s.
    expect(quote.uncachedSides.map((side) => side.side)).toEqual(["A", "B"]);
    for (const side of quote.uncachedSides) {
      expect(side.seconds).toBe(12);
      expect(side.credits).toBe(15 * side.seconds);
    }
    expect(quote.uploadCredits).toBe(15 * 24);
    expect(quote.generationCredits).toBe(15 * 3);
    expect(quote.estimatedNewCredits).toBe(quote.uploadCredits + quote.generationCredits);
    expect(quote.blockedSides).toEqual([]);
    expect(quote.quotaLimit).toBe(5);
    expect(quote.quotaUsed).toBe(0);
    expect(quote.quotaRemaining).toBe(5);
    expect(quote.quotaResetAt).toBeNull();
    expect(quote.expectedFromUpdatedAt).toBe(FROM_UPDATED_AT);
    expect(quote.expectedToUpdatedAt).toBe(TO_UPDATED_AT);
  });

  it("resolves the selected take as ready and lays out A 0-12, Glue 12-15, B 15-33", () => {
    const readyItems = [
      resolverItem(fixture.songs[0], FROM_UPDATED_AT, TAKE_ID),
      resolverItem(fixture.songs[1], TO_UPDATED_AT, null),
    ];
    const readyJunctions = resolveGlueJunctions({
      items: readyItems,
      takes: [takeRow()],
      jobs: [],
      quota: QUOTA,
      cachedExcerptKeys: new Set<string>(),
      rejectedExcerptKeys: new Set<string>(),
    });

    expect(readyJunctions).toHaveLength(2);
    const outgoing = readyJunctions[0];
    expect(outgoing.fromItemId).toBe(fixture.songs[0].itemId);
    expect(outgoing.toItemId).toBe(fixture.songs[1].itemId);
    expect(outgoing.descriptorKey).toBe(DESCRIPTOR_KEY);
    expect(outgoing.state).toBe("ready");
    expect(outgoing.selectedTake).toMatchObject({
      id: TAKE_ID,
      seconds: STAGED_SECONDS,
      r2Key: R2_KEY,
    });
    // The last item has no successor and never fabricates a Junction.
    expect(readyJunctions[1]).toMatchObject({
      toItemId: null,
      state: "disabled",
      unavailableReason: "no-next",
      selectedTake: null,
    });

    const withGlue = [
      timelineSource(fixture.songs[0], { takeId: TAKE_ID, seconds: STAGED_SECONDS }),
      timelineSource(fixture.songs[1], null),
    ];
    const timeline = buildArrangementTimeline(withGlue);
    const [first, second] = timeline.items;
    expect(first.startSeconds).toBe(0);
    expect(first.durationSeconds).toBe(12);
    expect(second.startSeconds).toBe(15);
    expect(second.endSeconds ?? second.startSeconds + second.durationSeconds).toBe(33);
    expect(second.durationSeconds).toBe(18);
    expect(timeline.items.map((item) => item.glueWindowBefore)).toEqual([
      null,
      { takeId: TAKE_ID, startSeconds: 12, endSeconds: 15 },
    ]);
    expect(timeline.totalSeconds).toBe(33);
    expect(getSongsetDurationSeconds(withGlue)).toBe(33);
    expect(fixture.expectedTimeline.totalSeconds).toBe(33);

    // Without the effective take the ordinary 2-beat gap at B's 80 BPM puts B
    // at 13.5 s; the staged 3.0 s Glue instead defers it to 15.0 s.
    const withoutGlue = [
      timelineSource(fixture.songs[0], null),
      timelineSource(fixture.songs[1], null),
    ];
    const ordinary = buildArrangementTimeline(withoutGlue);
    expect(ordinary.items[1].startSeconds).toBe(13.5);
    expect(ordinary.items[1].startSeconds).toBe(
      fixture.expectedTimeline.ordinaryGapComparison.wouldBeStartSecondsWithoutGlue
    );
    expect(ordinary.items[0].glueWindowBefore).toBeNull();
  });

  it("passes Traditional Chinese titles and Timed-LRC lines through byte-identically", () => {
    // Unicode round trip: the resolver payload, preflight titles, and LRC
    // parser must not normalize, transliterate, or truncate.
    expectByteIdentical(fixture.songs[0].title, "清晨的讚美");
    expectByteIdentical(fixture.songs[1].title, "祢的信實廣大");

    const unresolvedItems = [
      resolverItem(fixture.songs[0], FROM_UPDATED_AT, null),
      resolverItem(fixture.songs[1], TO_UPDATED_AT, null),
    ];
    const unresolvedJunctions = resolveGlueJunctions({
      items: unresolvedItems,
      takes: [],
      jobs: [],
      quota: QUOTA,
      cachedExcerptKeys: new Set<string>(),
      rejectedExcerptKeys: new Set<string>(),
    });
    const unresolvedPreflight = buildGlueRenderPreflight({
      junctions: unresolvedJunctions,
      items: ITEM_IDENTITIES,
      jobs: [],
    });
    expect(unresolvedPreflight).not.toBeNull();
    if (unresolvedPreflight === null) return;
    expectByteIdentical(unresolvedPreflight.glues[0].fromTitle, fixture.songs[0].title);
    expectByteIdentical(unresolvedPreflight.glues[0].toTitle, fixture.songs[1].title);

    const fixtureLyricTexts = fixture.songs.flatMap((source) =>
      source.lyrics.map((line) => line.text)
    );
    const parsedLyricLines = fixture.songs.flatMap((source) => parseLRC(source.lrc));
    expect(parsedLyricLines.map((line) => line.text)).toEqual(fixtureLyricTexts);
    parsedLyricLines.forEach((line, index) => {
      expectByteIdentical(line.text, fixtureLyricTexts[index]);
    });

    // Titles stay display-only and lyrics never enter machine payloads.
    const machinePayload = JSON.stringify([
      unresolvedJunctions,
      buildGlueDescriptor({
        beats: 4,
        toTempoBpm: fixture.songs[1].analysis.tempoBpm,
        from: {
          contentHash: fixture.songs[0].recording.contentHash,
          steps: fixture.songs[0].arrangement.steps.map((step) => [step.startSeconds, step.endSeconds]),
        },
        to: {
          contentHash: fixture.songs[1].recording.contentHash,
          steps: fixture.songs[1].arrangement.steps.map((step) => [step.startSeconds, step.endSeconds]),
        },
      }),
      unresolvedPreflight.glues.map((glue) => ({
        fromItemId: glue.fromItemId,
        toItemId: glue.toItemId,
        reasonCode: glue.reasonCode,
        jobId: glue.jobId,
        jobPhase: glue.jobPhase,
        retryKind: glue.retryKind,
      })),
    ]);
    for (const source of fixture.songs) {
      expect(machinePayload).not.toContain(source.title);
      for (const line of source.lyrics) {
        expect(machinePayload).not.toContain(line.text);
      }
    }
  });

  it("blocks the render with an ordered preflight until the take is effective", () => {
    const unresolvedJunctions = resolveGlueJunctions({
      items: [
        resolverItem(fixture.songs[0], FROM_UPDATED_AT, null),
        resolverItem(fixture.songs[1], TO_UPDATED_AT, null),
      ],
      takes: [],
      jobs: [],
      quota: QUOTA,
      cachedExcerptKeys: new Set<string>(),
      rejectedExcerptKeys: new Set<string>(),
    });

    const blocked = buildGlueRenderPreflight({
      junctions: unresolvedJunctions,
      items: ITEM_IDENTITIES,
      jobs: [],
    });
    expect(blocked).toEqual({
      code: "GLUE_NOT_READY",
      glues: [
        {
          fromItemId: fixture.songs[0].itemId,
          toItemId: fixture.songs[1].itemId,
          fromTitle: fixture.songs[0].title,
          toTitle: fixture.songs[1].title,
          reasonCode: "GLUE_NOT_READY",
          staleChange: null,
          jobId: null,
          jobPhase: null,
          retryKind: null,
        },
      ],
    });

    const readyJunctions = resolveGlueJunctions({
      items: [
        resolverItem(fixture.songs[0], FROM_UPDATED_AT, TAKE_ID),
        resolverItem(fixture.songs[1], TO_UPDATED_AT, null),
      ],
      takes: [takeRow()],
      jobs: [],
      quota: QUOTA,
      cachedExcerptKeys: new Set<string>(),
      rejectedExcerptKeys: new Set<string>(),
    });
    expect(
      buildGlueRenderPreflight({ junctions: readyJunctions, items: ITEM_IDENTITIES, jobs: [] })
    ).toBeNull();
  });

  it("freezes one ordered snapshot entry from the selected take", async () => {
    const itemRows: Row[] = fixture.songs.map((source) => ({
      id: source.itemId,
      position: source.position,
      title: source.title,
      updatedAt: new Date(source.position === 0 ? FROM_UPDATED_AT : TO_UPDATED_AT),
      updatedAtPrecise: PRECISE_UPDATED_AT[source.position],
      glueEnabled: true,
      glueBeats: 4,
      glueActiveTakeId: source.position === 0 ? TAKE_ID : null,
      arrangementSteps: JSON.stringify(source.arrangement),
      contentHash: source.recording.contentHash,
      durationSeconds: source.recording.durationSeconds,
      tempoBpm: source.analysis.tempoBpm,
      sections: JSON.stringify(source.analysis.sections),
      beats: JSON.stringify(source.analysis.beats),
      recordingDeletedAt: null,
    }));
    const takeRows: Row[] = [
      {
        id: TAKE_ID,
        userId: OWNER_ID,
        descriptorKey: DESCRIPTOR_KEY,
        descriptorJson: DESCRIPTOR_JSON,
        ordinal: 1,
        seed: 11,
        status: "ready",
        r2Key: R2_KEY,
        seconds: STAGED_SECONDS,
        rawResponseR2Key: null,
        rawResponseSha256: null,
        rawResponseBytes: null,
        metricsJson: JSON.stringify({ audioSha256: fixture.glue[0].sha256 }),
        reasonsJson: "[]",
        errorCode: null,
        createdAt: new Date(FROM_UPDATED_AT),
        updatedAt: new Date(FROM_UPDATED_AT),
      },
    ];

    installDbRows([[]]); // readGlueQuota: empty ledger
    plan = await createRenderGlueSnapshot(
      installTxRows([itemRows, takeRows, []]),
      SONGSET_ID,
      OWNER_ID
    );

    expect(plan.preflight).toBeNull();
    expect(plan.snapshot).toHaveLength(1);
    const entry = plan.snapshot[0];
    expect(Object.keys(entry)).toEqual([
      "from_item_id",
      "from_updated_at",
      "to_item_id",
      "to_updated_at",
      "take_id",
      "descriptor_key",
      "r2_key",
      "seconds",
      "sha256",
    ]);
    expect(entry).toEqual({
      from_item_id: fixture.songs[0].itemId,
      from_updated_at: PRECISE_UPDATED_AT[0],
      to_item_id: fixture.songs[1].itemId,
      to_updated_at: PRECISE_UPDATED_AT[1],
      take_id: TAKE_ID,
      descriptor_key: DESCRIPTOR_KEY,
      r2_key: R2_KEY,
      seconds: 3.0,
      sha256: fixture.glue[0].sha256,
    });
    // No title or lyric crosses into the worker snapshot (spec §5.5).
    const serialized = JSON.stringify(plan.snapshot);
    for (const source of fixture.songs) {
      expect(serialized).not.toContain(source.title);
      for (const line of source.lyrics) {
        expect(serialized).not.toContain(line.text);
      }
    }

    // The snapshot a job stored at creation is never rewritten by a later
    // take selection: the stored JSON stays byte-identical while a fresh
    // derivation over the newly selected take yields a different entry.
    const storedSnapshot = JSON.stringify(plan.snapshot);
    const laterTake = takeRow({
      id: "9f1c2f7e-3b4d-4a6e-9c21-5d8b7a6f0e12",
      ordinal: 2,
      r2Key: R2_KEY.replace(TAKE_ID, "9f1c2f7e-3b4d-4a6e-9c21-5d8b7a6f0e12"),
    });
    installDbRows([[]]);
    const laterPlan = await createRenderGlueSnapshot(
      installTxRows([
        itemRows.map((row) =>
          row.id === fixture.songs[0].itemId
            ? { ...row, glueActiveTakeId: laterTake.id }
            : row
        ),
        [
          {
            ...takeRows[0],
            id: laterTake.id,
            ordinal: 2,
            r2Key: laterTake.r2Key,
          },
        ],
        [],
      ]),
      SONGSET_ID,
      OWNER_ID
    );
    expect(laterPlan.snapshot[0].take_id).toBe(laterTake.id);
    expect(JSON.stringify(plan.snapshot)).toBe(storedSnapshot);
    expect(plan.snapshot[0].take_id).toBe(TAKE_ID);
  });

  it("makes no paid provider call anywhere in the journey", () => {
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(process.env.SOW_ELEVENLABS_API_KEY).toBe(API_KEY_AT_LOAD);
  });
});
