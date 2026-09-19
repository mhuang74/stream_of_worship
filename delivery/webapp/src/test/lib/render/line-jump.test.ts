import { describe, it, expect } from "vitest";
import {
  findLineJumpTarget,
  CURSOR_LEAD_SECONDS,
  LINE_RESTART_THRESHOLD_SECONDS,
} from "@/lib/render/line-jump";
import type { Chapter, ChapterLine } from "@/lib/render/chapters";

function makeChapter(
  index: number,
  startSeconds: number,
  endSeconds: number,
  lineStarts: number[] = [],
  songTitle = `Song ${index + 1}`
): Chapter {
  const lines: ChapterLine[] = lineStarts.map((startSecondsOfLine) => ({
    text: `line @ ${startSecondsOfLine}`,
    startSeconds: startSecondsOfLine,
  }));
  return {
    position: index + 1,
    songTitle,
    startSeconds,
    endSeconds,
    lines,
  };
}

// Two songs: song 0 spans 0–100 with lines at 10/20/30/40, song 1 spans
// 100–200 with lines at 110/120/130.
const twoSongs = [
  makeChapter(0, 0, 100, [10, 20, 30, 40]),
  makeChapter(1, 100, 200, [110, 120, 130]),
];

describe("findLineJumpTarget", () => {
  describe("next direction (within a song)", () => {
    it("jumps to the first line when before any line", () => {
      expect(findLineJumpTarget(twoSongs, 0, 5, "next")).toBe(10);
    });

    it("jumps to the next line mid-song", () => {
      expect(findLineJumpTarget(twoSongs, 0, 21, "next")).toBe(30);
    });

    it("applies the same cursor lead as the lyric sheet highlight", () => {
      // Just before line @20 minus the 0.3s lead → still on line @10 → next
      // is line @20.
      expect(findLineJumpTarget(twoSongs, 0, 20 - CURSOR_LEAD_SECONDS - 0.01, "next")).toBe(20);
      // Within the lead window, line @20 is already "current" → next is @30.
      expect(findLineJumpTarget(twoSongs, 0, 20 - CURSOR_LEAD_SECONDS + 0.01, "next")).toBe(30);
    });

    it("crosses into the next song on the last line", () => {
      expect(findLineJumpTarget(twoSongs, 0, 45, "next")).toBe(110);
    });

    it("returns null at the last line of the last song", () => {
      expect(findLineJumpTarget(twoSongs, 1, 131, "next")).toBeNull();
    });

    it("skips songs without synced lines when crossing forward", () => {
      const chapters = [
        makeChapter(0, 0, 100, [10, 20]),
        makeChapter(1, 100, 150, []), // no synced lines
        makeChapter(2, 150, 250, [160, 170]),
      ];
      expect(findLineJumpTarget(chapters, 0, 25, "next")).toBe(160);
    });
  });

  describe("previous direction (within a song)", () => {
    it("restarts the current line when past the restart threshold", () => {
      const deep = 20 + LINE_RESTART_THRESHOLD_SECONDS + 0.5;
      expect(findLineJumpTarget(twoSongs, 0, deep, "previous")).toBe(20);
    });

    it("goes to the previous line just after the current line started", () => {
      expect(findLineJumpTarget(twoSongs, 0, 20 + 1, "previous")).toBe(10);
    });

    it("goes to the previous line when exactly at the current line's start", () => {
      expect(findLineJumpTarget(twoSongs, 0, 20, "previous")).toBe(10);
    });

    it("crosses into the previous song at the first line", () => {
      // Line @110 started 1s ago → previous line = last line of song 0.
      expect(findLineJumpTarget(twoSongs, 1, 111, "previous")).toBe(40);
    });

    it("returns null before the first line of the first song", () => {
      expect(findLineJumpTarget(twoSongs, 0, 5, "previous")).toBeNull();
    });

    it("skips songs without synced lines when crossing backward", () => {
      const chapters = [
        makeChapter(0, 0, 100, [10, 20]),
        makeChapter(1, 100, 150, []), // no synced lines
        makeChapter(2, 150, 250, [160, 170]),
      ];
      // First line of song 2, just started → previous line = last of song 0.
      expect(findLineJumpTarget(chapters, 2, 160.5, "previous")).toBe(20);
    });
  });

  describe("songs without synced lines", () => {
    it("returns null in both directions so the caller can fall back", () => {
      const chapters = [makeChapter(0, 0, 100, []), makeChapter(1, 100, 200, [110])];
      expect(findLineJumpTarget(chapters, 0, 50, "next")).toBeNull();
      expect(findLineJumpTarget(chapters, 0, 50, "previous")).toBeNull();
    });
  });

  describe("overlapping chapters (crossfade)", () => {
    // Crossfade (gap 0) makes chapters overlap: song A spans 0–100 with its
    // last line at 98 (after song B's start), song B starts at 95 with its
    // first line at 97. The controller resolves the overlap to the later
    // chapter (B), so a naive "previous song's last line" would seek forward.
    const overlapping = [
      makeChapter(0, 0, 100, [90, 98]),
      makeChapter(1, 95, 195, [97, 120]),
    ];

    it("does not seek forward on ArrowLeft from early in the next song", () => {
      expect(findLineJumpTarget(overlapping, 1, 96, "previous")).toBe(90);
    });

    it("does not seek forward on ArrowLeft from the next song's first line", () => {
      expect(findLineJumpTarget(overlapping, 1, 97, "previous")).toBe(90);
    });

    it("keeps ArrowRight forward in the run-up to the overlap", () => {
      // At 94 the controller still resolves chapter A; the jump must land ahead.
      expect(findLineJumpTarget(overlapping, 0, 94, "next")).toBe(98);
    });

    it("picks the previous chapter's last line before the current chapter start", () => {
      const chapters = [
        makeChapter(0, 0, 100, [10, 80]),
        makeChapter(1, 50, 150, [60]),
      ];
      // 80 is after chapter 1's start (50), so 10 is the bounded target.
      expect(findLineJumpTarget(chapters, 1, 60, "previous")).toBe(10);
    });

    it("falls through to an earlier chapter when the neighbour has no line before the bound", () => {
      const chapters = [
        makeChapter(0, 0, 100, [10, 20]),
        makeChapter(1, 100, 150, [110]), // line falls after chapter 2 starts
        makeChapter(2, 105, 205, [120]),
      ];
      expect(findLineJumpTarget(chapters, 2, 120, "previous")).toBe(20);
    });
  });

  describe("edge cases", () => {
    it("returns null for an empty chapter list", () => {
      expect(findLineJumpTarget([], 0, 0, "next")).toBeNull();
      expect(findLineJumpTarget([], 0, 0, "previous")).toBeNull();
    });

    it("returns null for an out-of-range song index", () => {
      expect(findLineJumpTarget(twoSongs, 99, 10, "next")).toBeNull();
      expect(findLineJumpTarget(twoSongs, -1, 10, "previous")).toBeNull();
    });
  });
});
