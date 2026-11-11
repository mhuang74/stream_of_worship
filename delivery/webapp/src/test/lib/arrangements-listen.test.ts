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
    // The heading is looked up at the clamped position, so it names a section
    // this cut actually plays (10–20), not the 0–10 one it faded out of.
    expect(result[0].localTimeSeconds).toBe(10);
    expect(result[0].timelineSeconds).toBe(10);
  });

  it("keeps a late line inside its own Step's window instead of the next one", () => {
    // Source section 10–20 s was dropped: the 20–30 s clip sits directly after
    // the 0–10 s clip on the timeline, so an unclamped 10.3 s line would
    // highlight 0.3 s inside the next Step.
    const clips = [clip(0, 10, 0), clip(20, 30, 10)];
    const result = toSongsetLyricLines(clips, lines([10.3]));

    expect(result).toHaveLength(1);
    // Heading lookup follows the highlight: it stays just inside the played
    // range (the 1 ms inset keeps it off the next section's boundary) instead
    // of jumping to 20 s.
    expect(result[0].localTimeSeconds).toBeCloseTo(9.999, 6);
    expect(result[0].timelineSeconds).toBeCloseTo(9.999, 6);
    expect(result[0].timelineSeconds).toBeLessThan(10);
  });

  it("keeps a pickup whose preceding section was dropped, at the Step's start", () => {
    const clips = [clip(0, 10, 0), clip(20, 30, 10)];
    const result = toSongsetLyricLines(clips, lines([19.8]));

    expect(result).toHaveLength(1);
    expect(result[0].localTimeSeconds).toBe(20);
    expect(result[0].timelineSeconds).toBe(10);
  });

  it("shows the same line under two identical repeated Steps", () => {
    const clips = [clip(10, 20, 0), clip(10, 20, 20)];
    const result = toSongsetLyricLines(clips, lines([15]));

    expect(result.map((line) => line.timelineSeconds)).toEqual([5, 25]);
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

  it("heads a within-tolerance late line with a section the cut plays, agreeing with the controller sheet", () => {
    // The real shape that disagreed: chorus 34.86–59.13 is the last step, the
    // Recording's next section starts exactly at 59.13, and a line 0.2 s past
    // the clip end is still kept by the ownership tolerance. Its heading must
    // be the chorus this cut plays, which is also what the controller sheet's
    // chapterLineHeadings derives from the same clamp.
    const clips = [clip(34.86, 59.13, 0)];
    const result = toSongsetLyricLines(clips, lines([59.3]));

    expect(result).toHaveLength(1);
    // 1 ms inside the clip end, so the half-open section lookup stays in the
    // chorus rather than landing on the following interlude's start.
    expect(result[0].localTimeSeconds).toBeCloseTo(59.129, 6);
    expect(result[0].localTimeSeconds).toBeLessThan(59.13);
  });
});
