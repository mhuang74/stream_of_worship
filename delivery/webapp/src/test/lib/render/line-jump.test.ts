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
