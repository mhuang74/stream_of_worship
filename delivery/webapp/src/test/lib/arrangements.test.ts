import { describe, it, expect } from "vitest";
import {
  ARRANGEMENT_UNAVAILABLE_OR_STALE_MESSAGE,
  buildArrangementTimeline,
  buildSnappedSteps,
  calculateGapSeconds,
  getItemDurationSeconds,
  getSongsetDurationSeconds,
  parseRecordingBeats,
  parseRecordingSections,
  parseSongArrangement,
  validateSongArrangement,
  type ArrangementStep,
  type RecordingSection,
} from "@/lib/arrangements";
import fixtureJson from "../fixtures/arrangements.cross_language.json";

interface CrossLanguageFixture {
  durationSeconds: number;
  sections: RecordingSection[];
  beats: number[];
  expectedSteps: ArrangementStep[];
}

const fixture = fixtureJson as CrossLanguageFixture;

function arrangementJson(steps: ArrangementStep[]): string {
  return JSON.stringify({ version: 1, steps });
}

function fixtureSteps(): ArrangementStep[] {
  const sections = parseRecordingSections(JSON.stringify(fixture.sections));
  const beats = parseRecordingBeats(JSON.stringify(fixture.beats));
  const steps = buildSnappedSteps(sections, beats, fixture.durationSeconds);
  expect(steps).not.toBeNull();
  return steps as ArrangementStep[];
}

function expectStepsWithinTolerance(
  actual: ArrangementStep[] | null,
  expected: ArrangementStep[],
): void {
  expect(actual).not.toBeNull();
  const steps = actual as ArrangementStep[];
  expect(steps).toHaveLength(expected.length);
  steps.forEach((step, index) => {
    expect(step.sectionIndex).toBe(expected[index].sectionIndex);
    expect(step.label).toBe(expected[index].label);
    expect(Math.abs(step.startSeconds - expected[index].startSeconds)).toBeLessThanOrEqual(1e-9);
    expect(Math.abs(step.endSeconds - expected[index].endSeconds)).toBeLessThanOrEqual(1e-9);
  });
}

describe("shared cross-language fixture", () => {
  it("snaps to the expected bounds (cross-language 1e-9 proof)", () => {
    expectStepsWithinTolerance(fixtureSteps(), fixture.expectedSteps);
  });

  it("rounds the half-up 6th-decimal boundary identically", () => {
    const tag = fixtureSteps().find((step) => step.label === "tag");
    expect(tag).toBeDefined();
    expect(Math.abs((tag as ArrangementStep).startSeconds - 2.000001)).toBeLessThanOrEqual(1e-9);
    expect(Math.abs((tag as ArrangementStep).endSeconds - 22.0)).toBeLessThanOrEqual(1e-9);
  });

  it("validates an arrangement saved from the freshly snapped fixture", () => {
    const sections = parseRecordingSections(JSON.stringify(fixture.sections));
    const beats = parseRecordingBeats(JSON.stringify(fixture.beats));
    const result = validateSongArrangement(
      arrangementJson(fixture.expectedSteps),
      sections,
      beats,
      fixture.durationSeconds,
    );
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.arrangement?.steps).toHaveLength(fixture.expectedSteps.length);
  });
});

describe("parseRecordingSections", () => {
  it("returns null for SQL NULL and unreadable analysis", () => {
    expect(parseRecordingSections(null)).toBeNull();
    expect(parseRecordingSections("not json")).toBeNull();
    expect(parseRecordingSections("{}")).toBeNull();
    expect(parseRecordingSections(JSON.stringify([{ label: "a" }]))).toBeNull();
    expect(parseRecordingSections(JSON.stringify([{ label: "", start: 0, end: 1 }]))).toBeNull();
    expect(parseRecordingSections(JSON.stringify([{ label: "a", start: "0", end: 1 }]))).toBeNull();
  });

  it("parses sections, allowing extra analysis keys and touching/overlapping ranges", () => {
    const value = JSON.stringify([
      { label: "intro", start: 0, end: 2, duration: 2 },
      { label: "verse", start: 2, end: 4 },
      { label: "tag", start: 1, end: 3 },
    ]);
    expect(parseRecordingSections(value)).toEqual([
      { label: "intro", start: 0, end: 2 },
      { label: "verse", start: 2, end: 4 },
      { label: "tag", start: 1, end: 3 },
    ]);
  });
});

describe("parseRecordingBeats", () => {
  it("returns an empty list for missing or unreadable beats", () => {
    expect(parseRecordingBeats(null)).toEqual([]);
    expect(parseRecordingBeats("nope")).toEqual([]);
    expect(parseRecordingBeats("{}")).toEqual([]);
    expect(parseRecordingBeats("[]")).toEqual([]);
  });

  it("sorts, dedupes, and drops non-finite values", () => {
    expect(parseRecordingBeats("[4,1,1,3,130,-2,1e999,2.5]")).toEqual([
      -2, 1, 2.5, 3, 4, 130,
    ]);
  });

  it("keeps a beat at exactly zero and at the recording duration", () => {
    expect(parseRecordingBeats("[0,10]")).toEqual([0, 10]);
  });
});

describe("buildSnappedSteps", () => {
  it("snaps each bound to the nearest beat", () => {
    const steps = buildSnappedSteps(
      [{ label: "a", start: 1.2, end: 3.4 }],
      [1, 2, 3, 4],
      10,
    );
    expect(steps).toEqual([
      { sectionIndex: 0, label: "a", startSeconds: 1, endSeconds: 3 },
    ]);
  });

  it("chooses the earlier beat on an exact tie", () => {
    const steps = buildSnappedSteps([{ label: "a", start: 11, end: 13 }], [10, 12], 20);
    expect(steps).toEqual([
      { sectionIndex: 0, label: "a", startSeconds: 10, endSeconds: 12 },
    ]);
  });

  it("handles unsorted and duplicated beats", () => {
    const steps = buildSnappedSteps(
      [{ label: "a", start: 0.9, end: 3.1 }],
      [4, 1, 3, 2, 1, 3],
      10,
    );
    expect(steps).toEqual([
      { sectionIndex: 0, label: "a", startSeconds: 1, endSeconds: 3 },
    ]);
  });

  it("returns null when a snap collapses a section to zero length", () => {
    expect(buildSnappedSteps([{ label: "a", start: 30.1, end: 30.2 }], [30, 31], 60)).toBeNull();
  });

  it("returns null without beats or without valid sections", () => {
    expect(buildSnappedSteps([{ label: "a", start: 1, end: 2 }], [], 10)).toBeNull();
    expect(buildSnappedSteps(null, [1, 2], 10)).toBeNull();
    expect(buildSnappedSteps([], [1, 2], 10)).toBeNull();
    expect(buildSnappedSteps([{ label: "a", start: 2, end: 2 }], [1, 2], 10)).toBeNull();
  });

  it("skips invalid sections but preserves original indices", () => {
    const steps = buildSnappedSteps(
      [
        { label: "", start: 1, end: 2 },
        { label: "good", start: 1, end: 3 },
        { label: "out-of-range", start: 5, end: 99 },
      ],
      [1, 2, 3],
      10,
    );
    expect(steps).toEqual([
      { sectionIndex: 1, label: "good", startSeconds: 1, endSeconds: 3 },
    ]);
  });

  it("clamps snapped bounds to the recording duration", () => {
    const steps = buildSnappedSteps([{ label: "a", start: 0.4, end: 9.6 }], [0, 10], 10);
    expect(steps).toEqual([
      { sectionIndex: 0, label: "a", startSeconds: 0, endSeconds: 10 },
    ]);
  });

  it("ignores out-of-duration beats when snapping", () => {
    const steps = buildSnappedSteps([{ label: "a", start: 0.4, end: 9.6 }], [-5, 0, 10, 130], 10);
    expect(steps).toEqual([
      { sectionIndex: 0, label: "a", startSeconds: 0, endSeconds: 10 },
    ]);
  });

  it("supports touching and overlapping sections", () => {
    const steps = buildSnappedSteps(
      [
        { label: "a", start: 0, end: 2 },
        { label: "b", start: 2, end: 4 },
        { label: "c", start: 1, end: 3 },
      ],
      [0, 1, 2, 3, 4],
      10,
    );
    expect(steps?.map((step) => [step.startSeconds, step.endSeconds])).toEqual([
      [0, 2],
      [2, 4],
      [1, 3],
    ]);
  });
});

describe("parseSongArrangement", () => {
  it("distinguishes SQL NULL from malformed JSON", () => {
    expect(parseSongArrangement(null)).toEqual({ present: false });
    expect(parseSongArrangement("not json")).toEqual({ present: true, invalid: true });
    expect(parseSongArrangement("null")).toEqual({ present: true, invalid: true });
    expect(parseSongArrangement("[]")).toEqual({ present: true, invalid: true });
  });

  it("parses a strict arrangement, allowing duplicate section indices", () => {
    const steps: ArrangementStep[] = [
      { sectionIndex: 0, label: "intro", startSeconds: 0, endSeconds: 4.5 },
      { sectionIndex: 0, label: "intro", startSeconds: 0, endSeconds: 4.5 },
    ];
    expect(parseSongArrangement(arrangementJson(steps))).toEqual({
      present: true,
      arrangement: { version: 1, steps },
    });
  });

  it("rejects wrong top-level shape, version, or counts", () => {
    const step = { sectionIndex: 0, label: "a", startSeconds: 0, endSeconds: 1 };
    expect(parseSongArrangement(JSON.stringify({ version: 2, steps: [step] }))).toEqual({
      present: true,
      invalid: true,
    });
    expect(parseSongArrangement(JSON.stringify({ version: "1", steps: [step] }))).toEqual({
      present: true,
      invalid: true,
    });
    expect(parseSongArrangement(JSON.stringify({ version: 1, steps: [], extra: true }))).toEqual({
      present: true,
      invalid: true,
    });
    expect(parseSongArrangement(JSON.stringify({ version: 1, steps: [] }))).toEqual({
      present: true,
      invalid: true,
    });
    const tooMany = Array.from({ length: 65 }, () => step);
    expect(parseSongArrangement(JSON.stringify({ version: 1, steps: tooMany }))).toEqual({
      present: true,
      invalid: true,
    });
    const maxSteps = Array.from({ length: 64 }, () => step);
    const parsed = parseSongArrangement(JSON.stringify({ version: 1, steps: maxSteps }));
    expect(parsed.present && "arrangement" in parsed).toBe(true);
  });

  it("measures label length in code points, matching the worker", () => {
    // A surrogate pair counts as one character, so 100 emoji are accepted and
    // 101 are rejected exactly as Python's len() would decide.
    const accepted = {
      sectionIndex: 0,
      label: "😀".repeat(100),
      startSeconds: 0,
      endSeconds: 1,
    };
    expect(
      parseSongArrangement(JSON.stringify({ version: 1, steps: [accepted] }))
    ).toEqual({
      present: true,
      arrangement: { version: 1, steps: [accepted] },
    });

    const rejected = { ...accepted, label: "😀".repeat(101) };
    expect(
      parseSongArrangement(JSON.stringify({ version: 1, steps: [rejected] }))
    ).toEqual({ present: true, invalid: true });
  });

  it("rejects malformed step fields", () => {
    const badSteps = [
      { sectionIndex: -1, label: "a", startSeconds: 0, endSeconds: 1 },
      { sectionIndex: 1.5, label: "a", startSeconds: 0, endSeconds: 1 },
      { sectionIndex: "0", label: "a", startSeconds: 0, endSeconds: 1 },
      { sectionIndex: true, label: "a", startSeconds: 0, endSeconds: 1 },
      { sectionIndex: 0, label: "", startSeconds: 0, endSeconds: 1 },
      { sectionIndex: 0, label: "a".repeat(101), startSeconds: 0, endSeconds: 1 },
      { sectionIndex: 0, label: "a", startSeconds: "0", endSeconds: 1 },
      { sectionIndex: 0, label: "a", startSeconds: 0, endSeconds: null },
      { sectionIndex: 0, label: "a", startSeconds: -1, endSeconds: 1 },
      { sectionIndex: 0, label: "a", startSeconds: 2, endSeconds: 1 },
      { sectionIndex: 0, label: "a", startSeconds: 0, endSeconds: 1, extra: 1 },
    ];
    for (const step of badSteps) {
      expect(parseSongArrangement(JSON.stringify({ version: 1, steps: [step] }))).toEqual({
        present: true,
        invalid: true,
      });
    }
    expect(
      parseSongArrangement(
        JSON.stringify({
          version: 1,
          steps: [{ sectionIndex: 0, label: "a", startSeconds: 0, endSeconds: 1e999 }],
        }),
      ),
    ).toEqual({ present: true, invalid: true });
  });
});

describe("validateSongArrangement", () => {
  const sections: RecordingSection[] = [
    { label: "intro", start: 0.4, end: 4.3 },
    { label: "verse", start: 4.6, end: 30.1 },
  ];
  const beats = [0, 4.5, 30];
  const duration = 60;
  const snapped: ArrangementStep[] = [
    { sectionIndex: 0, label: "intro", startSeconds: 0, endSeconds: 4.5 },
    { sectionIndex: 1, label: "verse", startSeconds: 4.5, endSeconds: 30 },
  ];

  it("accepts SQL NULL even when analysis is missing", () => {
    expect(validateSongArrangement(null, null, [], 0)).toEqual({ ok: true, arrangement: null });
  });

  it("reports ARRANGEMENT_INVALID for malformed or structurally invalid JSON", () => {
    expect(validateSongArrangement("not json", sections, beats, duration)).toEqual({
      ok: false,
      code: "ARRANGEMENT_INVALID",
    });
    expect(validateSongArrangement("{}", sections, beats, duration)).toEqual({
      ok: false,
      code: "ARRANGEMENT_INVALID",
    });
  });

  it("reports ANALYSIS_UNAVAILABLE when saved analysis cannot produce the arrangement", () => {
    expect(validateSongArrangement(arrangementJson(snapped), null, beats, duration)).toEqual({
      ok: false,
      code: "ANALYSIS_UNAVAILABLE",
    });
    expect(validateSongArrangement(arrangementJson(snapped), sections, [], duration)).toEqual({
      ok: false,
      code: "ANALYSIS_UNAVAILABLE",
    });
    const collapsed = validateSongArrangement(
      arrangementJson(snapped),
      [{ label: "intro", start: 30.1, end: 30.2 }],
      [30, 31],
      60,
    );
    expect(collapsed).toEqual({ ok: false, code: "ANALYSIS_UNAVAILABLE" });
  });

  it("accepts matching and repeated steps", () => {
    const repeated = [snapped[0], snapped[0], snapped[1]];
    const result = validateSongArrangement(arrangementJson(repeated), sections, beats, duration);
    expect(result).toEqual({
      ok: true,
      arrangement: { version: 1, steps: repeated },
    });
    expect(validateSongArrangement(arrangementJson(snapped), sections, beats, duration).ok).toBe(
      true,
    );
  });

  it("reports ARRANGEMENT_STALE for a stale label, index, or bounds", () => {
    const staleLabel = [{ ...snapped[0], label: "wrong" }];
    expect(validateSongArrangement(arrangementJson(staleLabel), sections, beats, duration)).toEqual(
      { ok: false, code: "ARRANGEMENT_STALE" },
    );
    const staleIndex = [{ ...snapped[0], sectionIndex: 99 }];
    expect(validateSongArrangement(arrangementJson(staleIndex), sections, beats, duration)).toEqual(
      { ok: false, code: "ARRANGEMENT_STALE" },
    );
    const rawBounds = [{ ...snapped[0], startSeconds: 0.4, endSeconds: 4.3 }];
    expect(validateSongArrangement(arrangementJson(rawBounds), sections, beats, duration)).toEqual(
      { ok: false, code: "ARRANGEMENT_STALE" },
    );
    const outOfRange = [{ ...snapped[0], endSeconds: duration + 1 }];
    expect(
      validateSongArrangement(arrangementJson(outOfRange), sections, beats, duration),
    ).toEqual({ ok: false, code: "ARRANGEMENT_STALE" });
  });

  it("absorbs float noise up to the inclusive 1e-6 tolerance and no further", () => {
    const noisy = [{ ...snapped[0], startSeconds: snapped[0].startSeconds + 5e-7 }];
    expect(validateSongArrangement(arrangementJson(noisy), sections, beats, duration).ok).toBe(
      true,
    );
    const tooFar = [{ ...snapped[0], startSeconds: snapped[0].startSeconds + 2e-6 }];
    expect(validateSongArrangement(arrangementJson(tooFar), sections, beats, duration)).toEqual({
      ok: false,
      code: "ARRANGEMENT_STALE",
    });
  });

  it("enforces the 64-Step maximum (never truncating)", () => {
    const steps64 = Array.from({ length: 64 }, () => snapped[0]);
    expect(validateSongArrangement(arrangementJson(steps64), sections, beats, duration).ok).toBe(
      true,
    );
    const steps65 = Array.from({ length: 65 }, () => snapped[0]);
    expect(validateSongArrangement(arrangementJson(steps65), sections, beats, duration)).toEqual({
      ok: false,
      code: "ARRANGEMENT_INVALID",
    });
  });

  it("exports the repair-or-reset message", () => {
    expect(ARRANGEMENT_UNAVAILABLE_OR_STALE_MESSAGE).toBe(
      "Song arrangement is unavailable or stale; repair the Recording analysis or reset the arrangement",
    );
  });
});

describe("durations", () => {
  const arrangedSteps: ArrangementStep[] = [
    { sectionIndex: 0, label: "intro", startSeconds: 0, endSeconds: 4.5 },
    { sectionIndex: 2, label: "chorus", startSeconds: 30, endSeconds: 60 },
  ];

  it("uses the Recording duration for untouched items", () => {
    expect(getItemDurationSeconds({ recordingDurationSeconds: 200 })).toBe(200);
    expect(getItemDurationSeconds({ recordingDurationSeconds: null })).toBeNull();
    expect(getItemDurationSeconds({ recording: { durationSeconds: 42 } })).toBe(42);
  });

  it("uses the sum of Step lengths for valid custom arrangements", () => {
    expect(
      getItemDurationSeconds({
        recordingDurationSeconds: 200,
        arrangementSteps: arrangementJson(arrangedSteps),
      }),
    ).toBeCloseTo(34.5, 9);
  });

  it("returns null for structurally invalid arrangements", () => {
    expect(
      getItemDurationSeconds({ recordingDurationSeconds: 200, arrangementSteps: "not json" }),
    ).toBeNull();
  });

  it("defines the whole-Songset total as the last item's end time", () => {
    // default gap is 2 beats -> 2s per join when BPM is unknown
    expect(
      getSongsetDurationSeconds([
        { recordingDurationSeconds: 10 },
        { recordingDurationSeconds: 20 },
      ]),
    ).toBeCloseTo(32, 9);

    expect(
      getSongsetDurationSeconds([
        { recordingDurationSeconds: 10 },
        {
          recordingDurationSeconds: 20,
          crossfadeEnabled: true,
          crossfadeDurationSeconds: 5,
        },
      ]),
    ).toBeCloseTo(25, 9);

    expect(
      getSongsetDurationSeconds([
        { recordingDurationSeconds: 10 },
        { recordingDurationSeconds: 20, gapBeats: 4, tempoBpm: 60 },
      ]),
    ).toBeCloseTo(34, 9);
  });

  it("clamps a crossfade to a short incoming item's arranged duration", () => {
    expect(
      getSongsetDurationSeconds([
        { recordingDurationSeconds: 10 },
        {
          recordingDurationSeconds: 3,
          crossfadeEnabled: true,
          crossfadeDurationSeconds: 10,
        },
      ]),
    ).toBeCloseTo(10, 9);
  });

  it("returns null when any present arrangement is structurally invalid", () => {
    expect(
      getSongsetDurationSeconds([
        { recordingDurationSeconds: 10 },
        { recordingDurationSeconds: 20, arrangementSteps: "{}" },
      ]),
    ).toBeNull();
  });

  it("uses arranged durations for the total", () => {
    expect(
      getSongsetDurationSeconds([
        { recordingDurationSeconds: 200, arrangementSteps: arrangementJson(arrangedSteps) },
        { recordingDurationSeconds: 10 },
      ]),
    ).toBeCloseTo(34.5 + 2 + 10, 9);
  });
});

describe("calculateGapSeconds", () => {
  it("mirrors audio_engine.calculate_gap_ms", () => {
    expect(calculateGapSeconds({})).toBe(2);
    expect(calculateGapSeconds({ gapBeats: 4, tempoBpm: 120 })).toBe(2);
    expect(calculateGapSeconds({ gapBeats: 1.5 })).toBe(1.5);
    expect(calculateGapSeconds({ crossfadeEnabled: true, crossfadeDurationSeconds: 3 })).toBe(0);
    expect(calculateGapSeconds({ crossfadeEnabled: true })).toBe(2);
    expect(calculateGapSeconds({ tempoBpm: 0 })).toBe(2);
  });
});

describe("buildArrangementTimeline", () => {
  const arrangedSteps: ArrangementStep[] = [
    { sectionIndex: 0, label: "intro", startSeconds: 0, endSeconds: 4.5 },
    { sectionIndex: 2, label: "chorus", startSeconds: 30, endSeconds: 60 },
  ];

  it("plays an untouched item as one full-recording clip", () => {
    const timeline = buildArrangementTimeline([
      { itemId: "a", recordingHashPrefix: "hash-a", recordingDurationSeconds: 10 },
    ]);
    expect(timeline.totalSeconds).toBe(10);
    expect(timeline.items[0]).toMatchObject({
      startSeconds: 0,
      durationSeconds: 10,
      gapBeforeSeconds: 0,
      crossfadeSeconds: 0,
    });
    expect(timeline.items[0].clips).toEqual([
      {
        recordingHashPrefix: "hash-a",
        sectionIndex: -1,
        sourceStartSeconds: 0,
        sourceEndSeconds: 10,
        startSeconds: 0,
        endSeconds: 10,
      },
    ]);
  });

  it("reads the untouched duration from a SongsetItemDetail-shaped item", () => {
    const timeline = buildArrangementTimeline([
      { itemId: "a", recordingHashPrefix: "hash-a", recording: { durationSeconds: 10 } },
    ]);
    expect(timeline.totalSeconds).toBe(10);
    expect(timeline.items[0].clips[0].endSeconds).toBe(10);
  });

  it("joins Steps inside an item directly, without gaps or fades", () => {
    const timeline = buildArrangementTimeline([
      {
        itemId: "a",
        recordingHashPrefix: "hash-a",
        recordingDurationSeconds: 120,
        arrangementSteps: arrangementJson(arrangedSteps),
      },
    ]);
    expect(timeline.totalSeconds).toBeCloseTo(34.5, 9);
    const clips = timeline.items[0].clips;
    expect(clips).toHaveLength(2);
    expect(clips[1].startSeconds).toBe(clips[0].endSeconds);
    expect(timeline.items[0].gapBeforeSeconds).toBe(0);
    expect(timeline.items[0].crossfadeSeconds).toBe(0);
    expect(clips[1]).toMatchObject({
      sectionIndex: 2,
      sourceStartSeconds: 30,
      sourceEndSeconds: 60,
    });
  });

  it("keeps inter-item gaps and clamps crossfades to the incoming duration", () => {
    const timeline = buildArrangementTimeline([
      { itemId: "a", recordingHashPrefix: "hash-a", recordingDurationSeconds: 10 },
      {
        itemId: "b",
        recordingHashPrefix: "hash-b",
        recordingDurationSeconds: 3,
        crossfadeEnabled: true,
        crossfadeDurationSeconds: 10,
      },
    ]);
    expect(timeline.items[1]).toMatchObject({
      startSeconds: 7,
      durationSeconds: 3,
      gapBeforeSeconds: 0,
      crossfadeSeconds: 3,
    });
    expect(timeline.totalSeconds).toBeCloseTo(10, 9);
  });

  it("uses the default gap between items without transitions", () => {
    const timeline = buildArrangementTimeline([
      { itemId: "a", recordingHashPrefix: "hash-a", recordingDurationSeconds: 10 },
      { itemId: "b", recordingHashPrefix: "hash-b", recordingDurationSeconds: 20 },
    ]);
    expect(timeline.items[1]).toMatchObject({
      startSeconds: 12,
      durationSeconds: 20,
      gapBeforeSeconds: 2,
      crossfadeSeconds: 0,
    });
    expect(timeline.totalSeconds).toBeCloseTo(32, 9);
    expect(timeline.items[1].clips[0].startSeconds).toBe(12);
  });

  it("exposes no clips and zero duration for structurally invalid arrangements", () => {
    const timeline = buildArrangementTimeline([
      {
        itemId: "a",
        recordingHashPrefix: "hash-a",
        recordingDurationSeconds: 10,
        arrangementSteps: "broken",
      },
    ]);
    expect(timeline.items[0]).toMatchObject({ durationSeconds: 0, clips: [] });
  });

  it("omits clips for items without a selected Recording", () => {
    const timeline = buildArrangementTimeline([
      { itemId: "a", recordingHashPrefix: null, recordingDurationSeconds: 10 },
    ]);
    expect(timeline.items[0].clips).toEqual([]);
    expect(timeline.totalSeconds).toBe(10);
  });
});
