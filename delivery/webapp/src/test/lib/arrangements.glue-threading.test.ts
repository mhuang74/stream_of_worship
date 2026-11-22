import { describe, it, expect } from "vitest";

import { buildArrangementTimeline } from "@/lib/arrangements";
import {
  toListenTimelineSources,
  type ListenSongsetItemPayload,
} from "@/lib/arrangements-listen";

const RECORDING = {
  contentHash: "a".repeat(64),
  tempoBpm: 120,
  durationSeconds: 100,
  sections: [{ label: "Verse", start: 0, end: 100 }],
  beats: [0, 1, 2, 3],
};

function payload(overrides: Partial<ListenSongsetItemPayload> = {}): ListenSongsetItemPayload {
  return {
    id: "item-a",
    recordingHashPrefix: null,
    arrangementSteps: null,
    gapBeats: 0,
    crossfadeEnabled: 0,
    crossfadeDurationSeconds: 0,
    song: { title: "Song A" },
    recording: RECORDING,
    ...overrides,
  };
}

describe("Glue threading without layout changes", () => {
  it("reports the predecessor's take and window on the incoming item", () => {
    const timeline = buildArrangementTimeline([
      {
        itemId: "item-a",
        arrangementSteps: null,
        gapBeats: 0,
        crossfadeEnabled: 0,
        crossfadeDurationSeconds: 0,
        recordingDurationSeconds: 100,
        glueTakeId: "take-1",
        glueSeconds: 3,
      },
      {
        itemId: "item-b",
        arrangementSteps: null,
        gapBeats: 0,
        crossfadeEnabled: 0,
        crossfadeDurationSeconds: 0,
        recordingDurationSeconds: 80,
      },
    ]);

    expect(timeline.items[0].glueTakeIdBefore).toBeNull();
    expect(timeline.items[1].glueTakeIdBefore).toBe("take-1");
    expect(timeline.items[1].glueWindowBefore).toEqual({
      takeId: "take-1",
      startSeconds: 100,
      endSeconds: 103,
    });
    // Layout arithmetic is untouched until #319: B still starts at A's end.
    expect(timeline.items[1].startSeconds).toBe(100);
  });

  it("leaves the window null without an effective take", () => {
    const timeline = buildArrangementTimeline([
      { itemId: "item-a", arrangementSteps: null, gapBeats: 0, crossfadeEnabled: 0, crossfadeDurationSeconds: 0, recordingDurationSeconds: 100 },
      { itemId: "item-b", arrangementSteps: null, gapBeats: 0, crossfadeEnabled: 0, crossfadeDurationSeconds: 0, recordingDurationSeconds: 80 },
    ]);
    expect(timeline.items[1].glueTakeIdBefore).toBeNull();
    expect(timeline.items[1].glueWindowBefore).toBeNull();
  });

  it("passes glueTakeId and glueSeconds into listen timeline sources", () => {
    const sources = toListenTimelineSources([
      payload({ id: "item-a", glueTakeId: "take-1", glueSeconds: 3 }),
      payload({ id: "item-b" }),
    ]);
    expect(sources[0].glueTakeId).toBe("take-1");
    expect(sources[0].glueSeconds).toBe(3);
    expect(sources[1].glueTakeId).toBeNull();
    expect(sources[1].glueSeconds).toBeNull();
  });
});
