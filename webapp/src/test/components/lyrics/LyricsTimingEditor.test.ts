import { describe, it, expect } from "vitest";
import {
  secondsToLrcTimestamp,
  lrcTimestampToSeconds,
  buildLrc,
} from "@/components/lyrics/LyricsTimingEditor";
import { LRCLine } from "@/lib/render/lrc-parser";

describe("secondsToLrcTimestamp", () => {
  it("formats 0 seconds as 00:00.00", () => {
    expect(secondsToLrcTimestamp(0)).toBe("00:00.00");
  });

  it("formats seconds with minutes", () => {
    expect(secondsToLrcTimestamp(90)).toBe("01:30.00");
  });

  it("formats fractional seconds", () => {
    expect(secondsToLrcTimestamp(10.5)).toBe("00:10.50");
  });

  it("formats longer durations", () => {
    expect(secondsToLrcTimestamp(125.75)).toBe("02:05.75");
  });
});

describe("lrcTimestampToSeconds", () => {
  it("parses valid mm:ss.xx format", () => {
    expect(lrcTimestampToSeconds("00:10.50")).toBeCloseTo(10.5);
  });

  it("parses minutes correctly", () => {
    expect(lrcTimestampToSeconds("01:30.00")).toBeCloseTo(90.0);
  });

  it("returns null for invalid format", () => {
    expect(lrcTimestampToSeconds("invalid")).toBeNull();
  });

  it("returns null for empty string", () => {
    expect(lrcTimestampToSeconds("")).toBeNull();
  });

  it("trims whitespace before parsing", () => {
    expect(lrcTimestampToSeconds(" 00:10.50 ")).toBeCloseTo(10.5);
  });

  it("parses mm:ss.xxx (3-digit ms) format", () => {
    expect(lrcTimestampToSeconds("00:10.500")).toBeCloseTo(10.5);
  });
});

describe("buildLrc", () => {
  it("builds LRC string from lines", () => {
    const lines: LRCLine[] = [
      { timeSeconds: 10.5, text: "Hello world" },
      { timeSeconds: 25.0, text: "Second line" },
    ];
    const lrc = buildLrc(lines);
    expect(lrc).toContain("[00:10.50]Hello world");
    expect(lrc).toContain("[00:25.00]Second line");
  });

  it("sorts lines by timestamp", () => {
    const lines: LRCLine[] = [
      { timeSeconds: 25.0, text: "Second" },
      { timeSeconds: 10.5, text: "First" },
    ];
    const lrc = buildLrc(lines);
    const firstIdx = lrc.indexOf("[00:10.50]");
    const secondIdx = lrc.indexOf("[00:25.00]");
    expect(firstIdx).toBeLessThan(secondIdx);
  });

  it("returns empty string for empty lines", () => {
    expect(buildLrc([])).toBe("");
  });

  it("does not mutate original lines array", () => {
    const lines: LRCLine[] = [
      { timeSeconds: 25.0, text: "Second" },
      { timeSeconds: 10.5, text: "First" },
    ];
    const original = [...lines];
    buildLrc(lines);
    expect(lines[0].timeSeconds).toBe(original[0].timeSeconds);
    expect(lines[1].timeSeconds).toBe(original[1].timeSeconds);
  });
});
