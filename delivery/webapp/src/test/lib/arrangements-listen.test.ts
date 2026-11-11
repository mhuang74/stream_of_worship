import { describe, expect, it } from "vitest";
import { toSongsetLyricLines } from "@/lib/arrangements-listen";
import type { TimelineClip } from "@/lib/arrangements";
import type { LRCLine } from "@/lib/render/lrc-parser";

function clip(
  sourceStartSeconds: number,
  sourceEndSeconds: number,
  startSeconds: number,
): TimelineClip {
  return {
    recordingHashPrefix: "hash-1",
    sectionIndex: 0,
    sourceStartSeconds,
    sourceEndSeconds,
    startSeconds,
    endSeconds: startSeconds + (sourceEndSeconds - sourceStartSeconds),
  };
}

function lines(times: number[]): LRCLine[] {
  return times.map((timeSeconds) => ({ timeSeconds, text: `line-${timeSeconds}` }));
}

describe("toSongsetLyricLines", () => {
  it("assigns every line to exactly one tiled clip and offsets it onto the timeline", () => {
    const clips = [clip(0, 10, 0), clip(10, 20, 10)];
    const result = toSongsetLyricLines(clips, lines([0, 5, 10, 15]));

    expect(result.map((line) => line.text)).toEqual([
      "line-0",
      "line-5",
      "line-10",
      "line-15",
    ]);
    // Each line keeps its own local time and is shifted by its clip's offset.
    expect(result.map((line) => line.localTimeSeconds)).toEqual([0, 5, 10, 15]);
    expect(result.map((line) => line.timelineSeconds)).toEqual([0, 5, 10, 15]);
    result.forEach((line) => {
      expect(line.globalTimeSeconds).toBe(line.timelineSeconds);
    });
  });

  it("offsets a non-zero clip start by the arranged offset, not the recording time", () => {
    const clips = [clip(10, 20, 100)];
    const result = toSongsetLyricLines(clips, lines([12, 18]));

    expect(result.map((line) => line.localTimeSeconds)).toEqual([12, 18]);
    expect(result.map((line) => line.timelineSeconds)).toEqual([102, 108]);
  });

  it("clamps a pickup that precedes its Step to the clip start", () => {
    const clips = [clip(0, 10, 0), clip(10, 20, 10)];
    // 9.8 is a pickup sung into the second Step (the first gives up its tail).
    const result = toSongsetLyricLines(clips, lines([9.8]));

    expect(result).toHaveLength(1);
    expect(result[0].text).toBe("line-9.8");
    expect(result[0].localTimeSeconds).toBe(9.8);
    expect(result[0].timelineSeconds).toBe(10);
  });

  it("maps each Item's lines against that Item's own start, even when Items overlap", () => {
    // Two Items whose timeline ranges overlap (crossfade): item two starts at
    // 50 while item one still runs to 60.
    const secondItemClips = [clip(0, 20, 50)];
    const result = toSongsetLyricLines(secondItemClips, lines([5, 15]));

    expect(result.map((line) => line.timelineSeconds)).toEqual([55, 65]);
    result.forEach((line) => {
      expect(line.timelineSeconds).toBeGreaterThanOrEqual(50);
    });
  });

  it("returns nothing for an Item without clips", () => {
    expect(toSongsetLyricLines([], lines([1, 2, 3]))).toEqual([]);
  });
});
