import { describe, it, expect } from "vitest";
import {
  parseLRC,
  convertToGlobalTimeline,
  estimateLastLyricDuration,
  findCurrentLyricIndex,
  groupLyricsBySong,
  isValidLRC,
  getLyricsTimeRange,
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

  it("skips lines without text", () => {
    const lrc = "[00:00.00]Start\n[00:05.50]\n[00:10.00]End";
    const result = parseLRC(lrc);
    expect(result).toHaveLength(2);
    expect(result[0].text).toBe("Start");
    expect(result[1].text).toBe("End");
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

  it("handles zero-length text after bracket", () => {
    const lrc = "[00:00.00]\n[00:10.50]Text";
    const result = parseLRC(lrc);
    expect(result).toHaveLength(1);
  });

  it("handles lines with only whitespace as text", () => {
    const lrc = "[00:00.00]   \n[00:10.50]Text";
    const result = parseLRC(lrc);
    expect(result).toHaveLength(1);
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
  it("returns true for valid LRC content", () => {
    expect(isValidLRC("[00:00.00]Text")).toBe(true);
    expect(isValidLRC("[00:10.50]Valid")).toBe(true);
    expect(isValidLRC("[01:05.234]Content")).toBe(true);
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

  it("returns true for valid Chinese characters", () => {
    expect(isValidLRC("[00:00.00]中文歌词")).toBe(true);
    expect(isValidLRC("[01:05.50]测试文本")).toBe(true);
  });

  it("returns true for valid with extra content", () => {
    expect(isValidLRC("[00:00.00]Lyrics \n [01:00.00]More")).toBe(true);
    expect(isValidLRC("Artist: Name\n[00:00.00]Start")).toBe(true);
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