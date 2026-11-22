import { describe, expect, it } from "vitest";

import {
  buildArrangementTimeline,
  getSongsetDurationSeconds,
  type TimelineItemSource,
} from "@/lib/arrangements";
import { toSongsetLyricLines } from "@/lib/arrangements-listen";
import { chapterLineHeadings } from "@/lib/render/chapter-sections";
import type { Chapter } from "@/lib/render/chapters";
import type { LRCLine } from "@/lib/render/lrc-parser";

function item(
  itemId: string,
  overrides: Partial<TimelineItemSource> = {},
): TimelineItemSource {
  return {
    itemId,
    recordingHashPrefix: `hash-${itemId}`,
    recordingDurationSeconds: 100,
    arrangementSteps: null,
    gapBeats: 0,
    crossfadeEnabled: 0,
    crossfadeDurationSeconds: null,
    tempoBpm: null,
    ...overrides,
  };
}

describe("layoutSongsetItems with effective Glue", () => {
  it("shifts the incoming start and total by exactly the staged seconds", () => {
    const timeline = buildArrangementTimeline([
      item("a", { recordingDurationSeconds: 100, glueTakeId: "take-1", glueSeconds: 3 }),
      item("b", { recordingDurationSeconds: 80 }),
    ]);

    expect(timeline.items[0].startSeconds).toBe(0);
    expect(timeline.items[1].startSeconds).toBe(103);
    expect(timeline.items[1].gapBeforeSeconds).toBe(0);
    expect(timeline.items[1].crossfadeSeconds).toBe(0);
    expect(timeline.items[1].glueWindowBefore).toEqual({
      takeId: "take-1",
      startSeconds: 100,
      endSeconds: 103,
    });
    expect(timeline.totalSeconds).toBe(183);
    expect(getSongsetDurationSeconds([
      { recordingDurationSeconds: 100, glueTakeId: "take-1", glueSeconds: 3 },
      { recordingDurationSeconds: 80 },
    ])).toBe(183);
  });

  it("suppresses the Junction's gap and crossfade, not the next one", () => {
    const timeline = buildArrangementTimeline([
      item("a", {
        recordingDurationSeconds: 100,
        glueTakeId: "take-1",
        glueSeconds: 3,
        // Outgoing glue owns this junction; the next junction's gap is B's.
      }),
      item("b", {
        recordingDurationSeconds: 80,
      }),
      item("c", {
        recordingDurationSeconds: 50,
        gapBeats: 2,
        tempoBpm: 120,
        crossfadeEnabled: 1,
        crossfadeDurationSeconds: 5,
      }),
    ]);

    // Glue junction: no gap/crossfade.
    expect(timeline.items[1].startSeconds).toBe(103);
    expect(timeline.items[1].gapBeforeSeconds).toBe(0);
    expect(timeline.items[1].crossfadeSeconds).toBe(0);
    // B -> C is ordinary; C owns the incoming crossfade, which suppresses
    // the gap exactly as before.
    expect(timeline.items[2].gapBeforeSeconds).toBe(0);
    expect(timeline.items[2].crossfadeSeconds).toBe(5);
    expect(timeline.items[2].startSeconds).toBe(103 + 80 - 5);
  });

  it("shifts twice for two effective Glues, independently", () => {
    const timeline = buildArrangementTimeline([
      item("a", { recordingDurationSeconds: 100, glueTakeId: "take-1", glueSeconds: 3 }),
      item("b", { recordingDurationSeconds: 80, glueTakeId: "take-2", glueSeconds: 2.5 }),
      item("c", { recordingDurationSeconds: 60 }),
    ]);

    expect(timeline.items[1].startSeconds).toBe(103);
    expect(timeline.items[2].startSeconds).toBe(185.5);
    expect(timeline.items[2].glueWindowBefore).toEqual({
      takeId: "take-2",
      startSeconds: 183,
      endSeconds: 185.5,
    });
    expect(timeline.totalSeconds).toBe(245.5);
  });

  it("falls back to ordinary layout for a missing take id or non-positive seconds", () => {
    const missingTake = buildArrangementTimeline([
      item("a", { glueTakeId: null, glueSeconds: 3 }),
      item("b", { recordingDurationSeconds: 80, gapBeats: 2, tempoBpm: 120 }),
    ]);
    expect(missingTake.items[1].glueWindowBefore).toBeNull();
    expect(missingTake.items[1].gapBeforeSeconds).toBe(1);
    expect(missingTake.items[1].startSeconds).toBe(101);

    const noSeconds = buildArrangementTimeline([
      item("a", { glueTakeId: "take-1", glueSeconds: 0 }),
      item("b", { recordingDurationSeconds: 80, gapBeats: 2, tempoBpm: 120 }),
    ]);
    expect(noSeconds.items[1].glueWindowBefore).toBeNull();
    expect(noSeconds.items[1].startSeconds).toBe(101);

    const nullSeconds = buildArrangementTimeline([
      item("a", { glueTakeId: "take-1", glueSeconds: null }),
      item("b", { recordingDurationSeconds: 80, gapBeats: 2, tempoBpm: 120 }),
    ]);
    expect(nullSeconds.items[1].glueWindowBefore).toBeNull();
    expect(nullSeconds.items[1].startSeconds).toBe(101);
  });

  it("shifts a lyric line by the Glue seconds through the single layout", () => {
    const timeline = buildArrangementTimeline([
      item("a", { recordingDurationSeconds: 100, glueTakeId: "take-1", glueSeconds: 3 }),
      item("b", { recordingDurationSeconds: 80 }),
    ]);
    const bClips = timeline.items[1].clips;
    const lines: LRCLine[] = [{ timeSeconds: 5, text: "B first line" }];

    const placed = toSongsetLyricLines(bClips, lines, null);
    expect(placed).toHaveLength(1);
    expect(placed[0].localTimeSeconds).toBe(5);
    // B starts at 103 s, so its 5 s line is 108 s on the Songset timeline.
    expect(placed[0].timelineSeconds).toBe(108);
  });

  it("places controller chapters at the Glue-shifted starts", () => {
    const items: TimelineItemSource[] = [
      item("a", {
        recordingDurationSeconds: 100,
        glueTakeId: "take-1",
        glueSeconds: 3,
        recordingSections: [{ label: "Verse", start: 0, end: 100 }],
        recordingBeats: [],
      }),
      item("b", {
        recordingDurationSeconds: 80,
        recordingSections: [{ label: "Chorus", start: 0, end: 80 }],
        recordingBeats: [],
      }),
    ];
    const shifted: Chapter[] = [
      {
        position: 0,
        songTitle: "A",
        startSeconds: 0,
        endSeconds: 100,
        lines: [{ text: "a1", startSeconds: 5 }],
      },
      {
        position: 1,
        songTitle: "B",
        startSeconds: 103,
        endSeconds: 183,
        lines: [{ text: "b1", startSeconds: 108 }],
      },
    ];

    expect(chapterLineHeadings(shifted, items)).toEqual([["Verse"], ["Chorus"]]);

    // A controller manifest still using B's unshifted start does not line up
    // with the current Glue timeline and must render flat, never mislabel.
    const unshifted = [shifted[0]!, { ...shifted[1]!, startSeconds: 100 }];
    expect(chapterLineHeadings(unshifted, items)).toBeNull();
  });
});
