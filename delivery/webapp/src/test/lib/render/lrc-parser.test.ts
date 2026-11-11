import { describe, it, expect } from "vitest";
import {
  parseLRC,
  convertToGlobalTimeline,
  estimateLastLyricDuration,
  findCurrentLyricIndex,
  groupLyricsBySong,
  isValidLRC,
  getLyricsTimeRange,
  linesForSourceRange,
} from "@/lib/render/lrc-parser";
import { LRCLine, GlobalLRCLine } from "@/lib/render/lrc-parser";

describe("parseLRC", () => {
  it("parses basic mm:ss.xx format", () => {
    const lrc = "[00:05.50]Hello world";
    const result = parseLRC(lrc);
    expect(result).toHaveLength(1);
    expect(result[0].timeSeconds).toBeCloseTo(5.5);
    expect(result[0].text).toBe("Hello world");
  });

  it("parses mm:ss.xxx with 3-digit ms", () => {
    const lrc = "[00:05.500]Hello world";
    const result = parseLRC(lrc);
    expect(result).toHaveLength(1);
    expect(result[0].timeSeconds).toBeCloseTo(5.5);
  });

  it("handles multiple lines", () => {
    const lrc = "[00:00.00]Start\n[00:10.50]Middle\n[00:30.00]End";
    const result = parseLRC(lrc);
    expect(result).toHaveLength(3);
    expect(result[0].text).toBe("Start");
    expect(result[1].text).toBe("Middle");
    expect(result[2].text).toBe("End");
  });

  it("keeps empty-text lines as gap placeholders", () => {
    const lrc = "[00:00.00]Start\n[00:05.50]\n[00:10.00]End";
    const result = parseLRC(lrc);
    expect(result).toHaveLength(3);
    expect(result[0].text).toBe("Start");
    expect(result[1].text).toBe("");
    expect(result[1].timeSeconds).toBeCloseTo(5.5);
    expect(result[2].text).toBe("End");
  });

  it("sorts by timestamp", () => {
    const lrc = "[00:30.00]Late\n[00:10.50]Early\n[00:05.00]First";
    const result = parseLRC(lrc);
    expect(result[0].text).toBe("First");
    expect(result[1].text).toBe("Early");
    expect(result[2].text).toBe("Late");
  });

  it("handles empty input", () => {
    const result = parseLRC("");
    expect(result).toHaveLength(0);
  });

  it("handles whitespace-only input", () => {
    const result = parseLRC(" \t\n  ");
    expect(result).toHaveLength(0);
  });

  it("handles special characters and Chinese text", () => {
    const lrc = "[00:10.50]Start\n[00:20.00]中文文本\n[00:30.50]English & 特殊字符";
    const result = parseLRC(lrc);
    expect(result).toHaveLength(3);
    expect(result[1].text).toBe("中文文本");
    expect(result[2].text).toBe("English & 特殊字符");
  });

  it("handles duplicate timestamps", () => {
    const lrc = "[00:10.50]First\n[00:10.50]Second";
    const result = parseLRC(lrc);
    expect(result).toHaveLength(2);
    expect(result[0].timeSeconds).toBeCloseTo(10.5);
    expect(result[1].timeSeconds).toBeCloseTo(10.5);
  });

  it("ignores malformed brackets", () => {
    const lrc = "[00:10.50Hello\n[Invalid format]\n[00:20.00]Valid";
    const result = parseLRC(lrc);
    expect(result).toHaveLength(1);
    expect(result[0].text).toBe("Valid");
  });

  it("keeps zero-length text after bracket as placeholder", () => {
    const lrc = "[00:00.00]\n[00:10.50]Text";
    const result = parseLRC(lrc);
    expect(result).toHaveLength(2);
    expect(result[0].text).toBe("");
  });

  it("keeps whitespace-only text as placeholder after trim", () => {
    const lrc = "[00:00.00]   \n[00:10.50]Text";
    const result = parseLRC(lrc);
    expect(result).toHaveLength(2);
    expect(result[0].text).toBe("");
  });

  it("handles multiple consecutive malformed lines", () => {
    const lrc = "[00:00.00]Start\n[invalid]\n[bad]\n[00:05.00]End";
    const result = parseLRC(lrc);
    expect(result).toHaveLength(2);
  });
});

describe("convertToGlobalTimeline", () => {
  it("converts local lines to global timeline", () => {
    const localLines: LRCLine[] = [
      { timeSeconds: 0, text: "Start" },
      { timeSeconds: 10, text: "Middle" },
    ];
    const result = convertToGlobalTimeline(localLines, 5, " Song ");
    expect(result).toHaveLength(2);
    expect(result[0].globalTimeSeconds).toBe(5);
    expect(result[1].globalTimeSeconds).toBe(15);
  });

  it("handles zero segment start offset", () => {
    const localLines: LRCLine[] = [
      { timeSeconds: 5, text: "Line 1" },
      { timeSeconds: 15, text: "Line 2" },
    ];
    const result = convertToGlobalTimeline(localLines, 0, "Test Song");
    expect(result[0].globalTimeSeconds).toBe(5);
    expect(result[1].globalTimeSeconds).toBe(15);
  });

  it("handles multiple lines correctly", () => {
    const localLines: LRCLine[] = [
      { timeSeconds: 0, text: "First" },
      { timeSeconds: 5, text: "Second" },
      { timeSeconds: 10, text: "Third" },
    ];
    const result = convertToGlobalTimeline(localLines, 7, "Title");
    expect(result[0].globalTimeSeconds).toBe(7);
    expect(result[1].globalTimeSeconds).toBe(12);
    expect(result[2].globalTimeSeconds).toBe(17);
  });

  it("preserves original line data", () => {
    const localLines: LRCLine[] = [
      { timeSeconds: 5, text: "Text" },
    ];
    const result = convertToGlobalTimeline(localLines, 10, "Song");
    expect(result[0].localTimeSeconds).toBe(5);
    expect(result[0].globalTimeSeconds).toBe(15);
    expect(result[0].text).toBe("Text");
    expect(result[0].title).toBe("Song");
  });

  it("handles empty local lines array", () => {
    const result = convertToGlobalTimeline([], 5, "Song");
    expect(result).toHaveLength(0);
  });

  it("handles empty title string", () => {
    const localLines: LRCLine[] = [
      { timeSeconds: 10, text: "Text" },
    ];
    const result = convertToGlobalTimeline(localLines, 0, "");
    expect(result).toHaveLength(1);
    expect(result[0].title).toBe("");
  });
});

describe("estimateLastLyricDuration", () => {
  it("returns 5 seconds for empty array", () => {
    const result = estimateLastLyricDuration([]);
    expect(result).toBe(5.0);
  });

  it("uses previous occurrence duration for repeated text", () => {
    const lyrics: GlobalLRCLine[] = [
      { text: "Repeat", localTimeSeconds: 10, globalTimeSeconds: 10, title: "Song" },
      { text: "Next", localTimeSeconds: 20, globalTimeSeconds: 20, title: "Song" },
      { text: "Repeat", localTimeSeconds: 30, globalTimeSeconds: 30, title: "Song" },
    ];
    const result = estimateLastLyricDuration(lyrics);
    expect(result).toBe(10.0);
  });

  it("uses minimum of 3 seconds for repeated text", () => {
    const lyrics: GlobalLRCLine[] = [
      { text: "Repeat", localTimeSeconds: 10, globalTimeSeconds: 10, title: "Song" },
      { text: "Repeat", localTimeSeconds: 11, globalTimeSeconds: 11, title: "Song" },
    ];
    const result = estimateLastLyricDuration(lyrics);
    expect(result).toBe(3.0);
  });

  it("uses fallback char count for unique text", () => {
    const lyrics: GlobalLRCLine[] = [
      { text: "Hello", localTimeSeconds: 10, globalTimeSeconds: 10, title: "Song" },
    ];
    const result = estimateLastLyricDuration(lyrics);
    expect(result).toBeGreaterThan(0);
  });

  it("estimates duration using BPM", () => {
    const lyrics: GlobalLRCLine[] = [
      { text: "Test", localTimeSeconds: 10, globalTimeSeconds: 10, title: "Song" },
    ];
    const result = estimateLastLyricDuration(lyrics, 120);
    expect(result).toBeGreaterThan(0);
  });

  it("uses default BPM of 70 when none provided", () => {
    const lyrics: GlobalLRCLine[] = [
      { text: "Test", localTimeSeconds: 10, globalTimeSeconds: 10, title: "Song" },
    ];
    const result = estimateLastLyricDuration(lyrics);
    expect(result).toBeGreaterThan(0);
  });

  it("maintains minimum 3 seconds regardless of calculation", () => {
    const shortTextRepeated: GlobalLRCLine[] = [
      { text: "Repeat", localTimeSeconds: 10, globalTimeSeconds: 10, title: "Song" },
      { text: "Repeat", localTimeSeconds: 11, globalTimeSeconds: 11, title: "Song" },
    ];
    const calculatedLongText: GlobalLRCLine[] = [
      { text: "12345678901234567890", localTimeSeconds: 0, globalTimeSeconds: 0, title: "Song" },
    ];
    expect(estimateLastLyricDuration(shortTextRepeated)).toBe(3.0);
    expect(estimateLastLyricDuration(calculatedLongText, 200)).toBeGreaterThanOrEqual(3.0);
  });

  it("handles single line array", () => {
    const lyrics: GlobalLRCLine[] = [
      { text: "Single", localTimeSeconds: 0, globalTimeSeconds: 0, title: "Song" },
    ];
    const result = estimateLastLyricDuration(lyrics);
    expect(result).toBeGreaterThan(0);
  });

  it("rounds properly for character counts", () => {
    const lyrics: GlobalLRCLine[] = [
      { text: "ABC", localTimeSeconds: 0, globalTimeSeconds: 0, title: "Song" },
    ];
    const result = estimateLastLyricDuration(lyrics);
    expect(result).toBeGreaterThan(0);
  });
});

describe("findCurrentLyricIndex", () => {
  it("returns -1 for time before first lyric", () => {
    const lyrics: GlobalLRCLine[] = [
      { text: "First", globalTimeSeconds: 10, localTimeSeconds: 10, title: "Song" },
      { text: "Second", globalTimeSeconds: 20, localTimeSeconds: 20, title: "Song" },
    ];
    expect(findCurrentLyricIndex(lyrics, 5)).toBe(-1);
  });

  it("returns 0 for time at first lyric", () => {
    const lyrics: GlobalLRCLine[] = [
      { text: "First", globalTimeSeconds: 10, localTimeSeconds: 10, title: "Song" },
      { text: "Second", globalTimeSeconds: 20, localTimeSeconds: 20, title: "Song" },
    ];
    expect(findCurrentLyricIndex(lyrics, 10)).toBe(0);
  });

  it("returns correct index for time between lyrics", () => {
    const lyrics: GlobalLRCLine[] = [
      { text: "First", globalTimeSeconds: 10, localTimeSeconds: 10, title: "Song" },
      { text: "Second", globalTimeSeconds: 20, localTimeSeconds: 20, title: "Song" },
    ];
    expect(findCurrentLyricIndex(lyrics, 15)).toBe(0);
    expect(findCurrentLyricIndex(lyrics, 18)).toBe(0);
  });

  it("returns index of last lyric for time at last lyric", () => {
    const lyrics: GlobalLRCLine[] = [
      { text: "First", globalTimeSeconds: 10, localTimeSeconds: 10, title: "Song" },
      { text: "Second", globalTimeSeconds: 20, localTimeSeconds: 20, title: "Song" },
    ];
    expect(findCurrentLyricIndex(lyrics, 20)).toBe(1);
  });

  it("returns last index for time after last lyric", () => {
    const lyrics: GlobalLRCLine[] = [
      { text: "First", globalTimeSeconds: 10, localTimeSeconds: 10, title: "Song" },
      { text: "Second", globalTimeSeconds: 20, localTimeSeconds: 20, title: "Song" },
    ];
    expect(findCurrentLyricIndex(lyrics, 25)).toBe(1);
    expect(findCurrentLyricIndex(lyrics, 100)).toBe(1);
  });

  it("returns -1 for empty lyrics array", () => {
    expect(findCurrentLyricIndex([], 10)).toBe(-1);
  });

  it("returns maximum index when time matches last lyric exactly", () => {
    const lyrics: GlobalLRCLine[] = [
      { text: "First", globalTimeSeconds: 10, localTimeSeconds: 10, title: "Song" },
      { text: "Second", globalTimeSeconds: 20, localTimeSeconds: 20, title: "Song" },
      { text: "Last", globalTimeSeconds: 30, localTimeSeconds: 30, title: "Song" },
    ];
    expect(findCurrentLyricIndex(lyrics, 30)).toBe(2);
  });
});

describe("groupLyricsBySong", () => {
  it("groups single song lyrics", () => {
    const lyrics: GlobalLRCLine[] = [
      { text: "First", globalTimeSeconds: 10, localTimeSeconds: 10, title: "Song 1" },
      { text: "Second", globalTimeSeconds: 20, localTimeSeconds: 20, title: "Song 1" },
      { text: "Third", globalTimeSeconds: 30, localTimeSeconds: 30, title: "Song 1" },
    ];
    const result = groupLyricsBySong(lyrics);
    expect(result.size).toBe(1);
    expect(result.get("Song 1")).toHaveLength(3);
  });

  it("groups multiple song lyrics separately", () => {
    const lyrics: GlobalLRCLine[] = [
      { text: "First", globalTimeSeconds: 10, localTimeSeconds: 10, title: "Song 1" },
      { text: "Second", globalTimeSeconds: 20, localTimeSeconds: 20, title: "Song 1" },
      { text: "First", globalTimeSeconds: 10, localTimeSeconds: 10, title: "Song 2" },
      { text: "Second", globalTimeSeconds: 20, localTimeSeconds: 20, title: "Song 2" },
    ];
    const result = groupLyricsBySong(lyrics);
    expect(result.size).toBe(2);
    expect(result.get("Song 1")).toHaveLength(2);
    expect(result.get("Song 2")).toHaveLength(2);
  });

  it("handles empty lyrics array", () => {
    const result = groupLyricsBySong([]);
    expect(result.size).toBe(0);
  });

  it("handles songs with single line", () => {
    const lyrics: GlobalLRCLine[] = [
      { text: "Only one", globalTimeSeconds: 10, localTimeSeconds: 10, title: "Solo Song" },
    ];
    const result = groupLyricsBySong(lyrics);
    expect(result.size).toBe(1);
    expect(result.get("Solo Song")).toHaveLength(1);
  });

  it("handles titles with special characters", () => {
    const lyrics: GlobalLRCLine[] = [
      { text: "Line 1", globalTimeSeconds: 10, localTimeSeconds: 10, title: "Song & Special!" },
      { text: "Line 2", globalTimeSeconds: 20, localTimeSeconds: 20, title: "Song & Special!" },
    ];
    const result = groupLyricsBySong(lyrics);
    expect(result.get("Song & Special!")).toHaveLength(2);
  });

  it("handles multiple songs with various line counts", () => {
    const lyrics: GlobalLRCLine[] = [
      { text: "1", globalTimeSeconds: 0, localTimeSeconds: 0, title: "A" },
      { text: "1", globalTimeSeconds: 0, localTimeSeconds: 0, title: "B" },
      { text: "1", globalTimeSeconds: 0, localTimeSeconds: 0, title: "B" },
      { text: "1", globalTimeSeconds: 0, localTimeSeconds: 0, title: "C" },
      { text: "1", globalTimeSeconds: 0, localTimeSeconds: 0, title: "A" },
    ];
    const result = groupLyricsBySong(lyrics);
    expect(result.size).toBe(3);
    expect(result.get("A")).toHaveLength(2);
    expect(result.get("B")).toHaveLength(2);
    expect(result.get("C")).toHaveLength(1);
  });
});

describe("isValidLRC", () => {
  it("returns true for valid LRC content with 2+ timestamped lines", () => {
    expect(isValidLRC("[00:00.00]Text\n[00:10.50]Valid")).toBe(true);
    expect(isValidLRC("[00:10.50]Valid\n[01:05.234]Content")).toBe(true);
  });

  it("returns false for invalid content without brackets", () => {
    expect(isValidLRC("No brackets here")).toBe(false);
    expect(isValidLRC("Just text")).toBe(false);
    expect(isValidLRC("")).toBe(false);
    expect(isValidLRC("   ")).toBe(false);
  });

  it("returns false for malformed timestamps", () => {
    expect(isValidLRC("[00:00]Invalid seconds")).toBe(false);
    expect(isValidLRC("[:05.50]Invalid minutes")).toBe(false);
  });

  it("returns true for valid Chinese characters with 2+ lines", () => {
    expect(isValidLRC("[00:00.00]中文歌词\n[00:10.50]第二行")).toBe(true);
    expect(isValidLRC("[01:05.50]测试文本\n[02:00.00]另一行")).toBe(true);
  });

  it("returns true for valid with extra content", () => {
    expect(isValidLRC("[00:00.00]Lyrics \n [01:00.00]More")).toBe(true);
    expect(isValidLRC("Artist: Name\n[00:00.00]Start\n[00:10.00]Second")).toBe(true);
  });

  it("returns true for combined with other text", () => {
    expect(isValidLRC("[00:00.00]Test\n[00:10.50]Another")).toBe(true);
  });

  it("returns false for empty string", () => {
    expect(isValidLRC("")).toBe(false);
  });

  it("returns false for text without timestamp bracket", () => {
    expect(isValidLRC("Some text with [00.10.50] but text only")).toBe(false);
  });

  it("v6: returns false for content with only 1 timestamped line", () => {
    expect(isValidLRC("[00:00.00]Text")).toBe(false);
    expect(isValidLRC("[00:10.50]Valid")).toBe(false);
    expect(isValidLRC("[01:05.234]Content")).toBe(false);
  });

  it("v6: returns false for plain text with a single timestamp reference", () => {
    expect(isValidLRC("bridge at [01:00.00] and then more text")).toBe(false);
    expect(isValidLRC("Some text with [00:10.50] reference")).toBe(false);
  });

  it("v6: returns true for content with exactly 2 timestamped lines", () => {
    expect(isValidLRC("[00:00.00]First\n[00:10.50]Second")).toBe(true);
  });

  it("v6: returns false for content with 0 timestamped lines", () => {
    expect(isValidLRC("Just plain text\nNo timestamps here")).toBe(false);
  });
});

describe("getLyricsTimeRange", () => {
  it("returns correct range for multiple lyrics", () => {
    const lyrics: LRCLine[] = [
      { timeSeconds: 10, text: "First" },
      { timeSeconds: 20, text: "Second" },
      { timeSeconds: 30, text: "Third" },
    ];
    const result = getLyricsTimeRange(lyrics);
    expect(result).not.toBeNull();
    expect(result!.firstTime).toBe(10);
    expect(result!.lastTime).toBe(30);
  });

  it("returns array with same time for single line", () => {
    const lyrics: LRCLine[] = [
      { timeSeconds: 15.75, text: "Solo" },
    ];
    const result = getLyricsTimeRange(lyrics);
    expect(result).not.toBeNull();
    expect(result!.firstTime).toBe(15.75);
    expect(result!.lastTime).toBe(15.75);
  });

  it("returns null for empty array", () => {
    const result = getLyricsTimeRange([]);
    expect(result).toBeNull();
  });

  it("handles fractional seconds correctly", () => {
    const lyrics: LRCLine[] = [
      { timeSeconds: 1.5, text: "First" },
      { timeSeconds: 5.75, text: "Second" },
      { timeSeconds: 10.25, text: "Third" },
    ];
    const result = getLyricsTimeRange(lyrics);
    expect(result).not.toBeNull();
    expect(result!.firstTime).toBe(1.5);
    expect(result!.lastTime).toBe(10.25);
  });

  it("handles zero times", () => {
    const lyrics: LRCLine[] = [
      { timeSeconds: 0, text: "Start" },
      { timeSeconds: 5, text: "Middle" },
    ];
    const result = getLyricsTimeRange(lyrics);
    expect(result).not.toBeNull();
    expect(result!.firstTime).toBe(0);
    expect(result!.lastTime).toBe(5);
  });

  it("handles large time values", () => {
    const lyrics: LRCLine[] = [
      { timeSeconds: 100, text: "Long song" },
      { timeSeconds: 200, text: "Even longer" },
    ];
    const result = getLyricsTimeRange(lyrics);
    expect(result).not.toBeNull();
    expect(result!.firstTime).toBe(100);
    expect(result!.lastTime).toBe(200);
  });
});

describe("linesForSourceRange", () => {
  const REAL = [
    { startSeconds: 27.37, endSeconds: 53.33 },
    { startSeconds: 53.33, endSeconds: 80.8 },
  ];
  const lines: LRCLine[] = [
    { timeSeconds: 27.77, text: "a" },
    { timeSeconds: 40.9, text: "b" },
    { timeSeconds: 53.3, text: "c" },
    { timeSeconds: 66.8, text: "d" },
  ];

  it("assigns a pickup line to the section that starts at the boundary", () => {
    // Real numbers from the 讚美之泉 recording: the verse is 27.37–53.33 while
    // the line after it starts at 53.30, 30 ms before the boundary.
    expect(
      linesForSourceRange(lines, 27.37, 53.33, REAL).map((line) => line.text)
    ).toEqual(["a", "b"]);
    expect(
      linesForSourceRange(lines, 53.33, 80.8, REAL).map((line) => line.text)
    ).toEqual(["c", "d"]);
  });

  it("tiles consecutive ranges, showing the pickup exactly once", () => {
    // A line in the handing-over window appears in the accepting range only.
    const boundary: LRCLine[] = [{ timeSeconds: 53.3, text: "c" }];
    const spans = [
      linesForSourceRange(boundary, 27.37, 53.33, REAL),
      linesForSourceRange(boundary, 53.33, 80.8, REAL),
    ];
    expect(spans.flat().map((line) => line.text)).toEqual(["c"]);
  });

  it("tiles an adjacent short range instead of duplicating into both", () => {
    // Real shape: a 0–0.21 s "start" section followed by "intro" at 0.21.
    // The stub is shorter than the tolerance, so it keeps its own line rather
    // than handing it to a neighbour that would then show it a second time.
    const stubs = [
      { startSeconds: 0, endSeconds: 0.21 },
      { startSeconds: 0.21, endSeconds: 15.91 },
    ];
    const stubLines: LRCLine[] = [
      { timeSeconds: 0.05, text: "opening" },
      { timeSeconds: 0.4, text: "intro line" },
    ];
    const spans = [
      linesForSourceRange(stubLines, 0, 0.21, stubs).map((line) => line.text),
      linesForSourceRange(stubLines, 0.21, 15.91, stubs).map((line) => line.text),
    ];
    expect(spans).toEqual([["opening"], ["intro line"]]);
  });

  it("keeps its tail when no component starts at the range end", () => {
    // A full-file clip (an unarranged render) ends at the recording duration,
    // so nothing can inherit its final half second — the line must stay.
    const tail: LRCLine[] = [{ timeSeconds: 29.8, text: "last line" }];
    expect(
      linesForSourceRange(tail, 0, 30, [{ startSeconds: 0, endSeconds: 30 }]).map(
        (line) => line.text
      )
    ).toEqual(["last line"]);
    expect(
      linesForSourceRange(tail, 0, 30, [
        { startSeconds: 0, endSeconds: 30 },
        { startSeconds: 20, endSeconds: 25 },
      ]).map((line) => line.text)
    ).toEqual(["last line"]);
  });

  it("hands over a line exactly at the tolerance before a followed range end", () => {
    const neighbours = [
      { startSeconds: 0, endSeconds: 5.5 },
      { startSeconds: 5.5, endSeconds: 10 },
    ];
    expect(
      linesForSourceRange([{ timeSeconds: 5.0, text: "x" }], 0, 5.5, neighbours)
    ).toEqual([]);
    expect(
      linesForSourceRange([{ timeSeconds: 5.0, text: "x" }], 5.5, 10, neighbours).map(
        (line) => line.text
      )
    ).toEqual(["x"]);
  });

  it("keeps a pickup when the section before it is absent from the arrangement", () => {
    // The source section 10–20 s was dropped, so no Step ends at the 20 s
    // boundary the pickup is sung into. Ownership tolerates a line within 0.5 s
    // of a Step's bounds, so the pickup still lands on the Step starting at 20.
    const steps = [
      { startSeconds: 0, endSeconds: 10 },
      { startSeconds: 20, endSeconds: 30 },
    ];
    const pickup: LRCLine[] = [{ timeSeconds: 19.8, text: "pickup" }];
    expect(linesForSourceRange(pickup, 20, 30, steps)).toEqual(pickup);
    expect(linesForSourceRange(pickup, 0, 10, steps)).toEqual([]);
  });

  it("keeps a late line with the Step it was sung into and not the next one", () => {
    const steps = [
      { startSeconds: 0, endSeconds: 10 },
      { startSeconds: 20, endSeconds: 30 },
    ];
    const late: LRCLine[] = [{ timeSeconds: 10.3, text: "late" }];
    expect(linesForSourceRange(late, 0, 10, steps)).toEqual(late);
    expect(linesForSourceRange(late, 20, 30, steps)).toEqual([]);
  });

  it("shows a line exactly on a shared boundary once, under the later Step", () => {
    const steps = [
      { startSeconds: 0, endSeconds: 10 },
      { startSeconds: 10, endSeconds: 20 },
    ];
    const boundary: LRCLine[] = [{ timeSeconds: 10, text: "boundary" }];
    expect(linesForSourceRange(boundary, 0, 10, steps)).toEqual([]);
    expect(linesForSourceRange(boundary, 10, 20, steps)).toEqual(boundary);
  });

  it("shows the same line under two identical repeated Steps", () => {
    const steps = [
      { startSeconds: 10, endSeconds: 20 },
      { startSeconds: 10, endSeconds: 20 },
    ];
    const repeated: LRCLine[] = [{ timeSeconds: 15, text: "chorus" }];
    expect(linesForSourceRange(repeated, 10, 20, steps)).toEqual(repeated);
  });

  it("drops a line more than the tolerance outside every Step", () => {
    const steps = [
      { startSeconds: 0, endSeconds: 10 },
      { startSeconds: 20, endSeconds: 30 },
    ];
    const gone: LRCLine[] = [{ timeSeconds: 10.6, text: "omitted" }];
    expect(linesForSourceRange(gone, 0, 10, steps)).toEqual([]);
    expect(linesForSourceRange(gone, 20, 30, steps)).toEqual([]);
  });

  it("returns no lines for an empty or inverted range", () => {
    const single: LRCLine[] = [{ timeSeconds: 5.0, text: "x" }];
    expect(linesForSourceRange(single, 5, 5, [])).toEqual([]);
    expect(linesForSourceRange(single, 5, 4, [])).toEqual([]);
  });
});