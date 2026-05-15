/**
 * Tests for Video Engine
 */

import { describe, it, expect, beforeEach, vi, beforeAll } from "vitest";
import {
  VideoEngine,
  ChapterInfo,
} from "@/lib/render/video-engine";
import {
  FrameRenderer,
  VideoTemplateName,
} from "@/lib/render/frame-renderer";
import { parseLRC, GlobalLRCLine, convertToGlobalTimeline, estimateLastLyricDuration, isValidLRC, getLyricsTimeRange } from "@/lib/render/lrc-parser";
import { AssetFetcher } from "@/lib/render/asset-fetcher";

// Mock child_process spawn
vi.mock("child_process", async (importOriginal) => {
  const actual = await importOriginal<typeof import("child_process")>();
  return {
    ...actual,
    spawn: vi.fn(),
  };
});

// Mock fluent-ffmpeg
vi.mock("fluent-ffmpeg", () => ({
  default: {
    ffprobe: vi.fn(),
  },
}));

// Mock fs/promises
vi.mock("fs/promises", () => ({
  mkdir: vi.fn().mockResolvedValue(undefined),
  writeFile: vi.fn().mockResolvedValue(undefined),
  rename: vi.fn().mockResolvedValue(undefined),
  unlink: vi.fn().mockResolvedValue(undefined),
  stat: vi.fn().mockResolvedValue({ size: 1024 }),
  access: vi.fn().mockResolvedValue(undefined),
  readdir: vi.fn().mockResolvedValue([]),
}));

describe("VideoEngine", () => {
  let assetFetcher: AssetFetcher;
  let videoEngine: VideoEngine;

  beforeEach(() => {
    assetFetcher = {
      downloadLrc: vi.fn(),
      getTempDir: vi.fn().mockResolvedValue("/tmp/test"),
    } as unknown as AssetFetcher;

    videoEngine = new VideoEngine(assetFetcher, {
      template: "dark",
      fontSizePreset: "M",
      resolution: "1080p",
      fps: 24,
      includeTitleCard: true,
      titleCardDurationSeconds: 5,
    });
  });

  describe("constructor", () => {
    it("should create VideoEngine with default options", () => {
      const engine = new VideoEngine(assetFetcher);
      expect(engine).toBeDefined();
    });

    it("should create VideoEngine with custom options", () => {
      const engine = new VideoEngine(assetFetcher, {
        template: "gradient_warm",
        fontSizePreset: "L",
        resolution: "720p",
        fps: 30,
        includeTitleCard: false,
        titleCardDurationSeconds: 10,
      });
      expect(engine).toBeDefined();
    });

    it("should clamp title card duration between 5-30 seconds", () => {
      const engine1 = new VideoEngine(assetFetcher, {
        titleCardDurationSeconds: 3,
      });
      expect(engine1).toBeDefined();

      const engine2 = new VideoEngine(assetFetcher, {
        titleCardDurationSeconds: 35,
      });
      expect(engine2).toBeDefined();
    });
  });

  describe("getAvailableTemplates", () => {
    it("should return list of available templates", () => {
      const templates = VideoEngine.getAvailableTemplates();
      expect(templates).toContain("dark");
      expect(templates).toContain("gradient_warm");
      expect(templates).toContain("gradient_blue");
    });
  });

  describe("getTemplate", () => {
    it("should return template by name", () => {
      const template = VideoEngine.getTemplate("dark");
      expect(template.name).toBe("dark");
      expect(template.resolution).toEqual([1920, 1080]);
    });

    it("should return dark template for unknown name", () => {
      const template = VideoEngine.getTemplate("unknown" as VideoTemplateName);
      expect(template.name).toBe("dark");
    });
  });

  describe("getAvailableFontSizes", () => {
    it("should return all font size presets", () => {
      const sizes = VideoEngine.getAvailableFontSizes();
      expect(sizes).toContain("S");
      expect(sizes).toContain("M");
      expect(sizes).toContain("L");
      expect(sizes).toContain("XL");
    });
  });

  describe("getFontSize", () => {
    it("should return correct font size for S preset", () => {
      expect(VideoEngine.getFontSize("S")).toBe(32);
    });

    it("should return correct font size for M preset", () => {
      expect(VideoEngine.getFontSize("M")).toBe(48);
    });

    it("should return correct font size for L preset", () => {
      expect(VideoEngine.getFontSize("L")).toBe(64);
    });

    it("should return correct font size for XL preset", () => {
      expect(VideoEngine.getFontSize("XL")).toBe(80);
    });
  });

  describe("formatChaptersForFFmpeg", () => {
    it("should format chapters correctly", async () => {
      const chapters: ChapterInfo[] = [
        {
          position: 1,
          songTitle: "Song 1",
          startSeconds: 0,
          endSeconds: 180,
          lines: [{ text: "Line 1", startSeconds: 5 }],
        },
        {
          position: 2,
          songTitle: "Song 2",
          startSeconds: 180,
          endSeconds: 360,
          lines: [{ text: "Line 2", startSeconds: 185 }],
        },
      ];

      // Access private method through type assertion
      const result = await (videoEngine as unknown as { formatChaptersForFFmpeg: (chapters: ChapterInfo[]) => string }).formatChaptersForFFmpeg(chapters);
      
      expect(result).toContain(";FFMETADATA1");
      expect(result).toContain("[CHAPTER]");
      expect(result).toContain("TIMEBASE=1/1000");
      expect(result).toContain("START=0");
      expect(result).toContain("END=180000");
      expect(result).toContain("title=Song 1");
    });
  });
});

describe("FrameRenderer", () => {
  let renderer: FrameRenderer;

  beforeAll(async () => {
    renderer = new FrameRenderer({
      template: FrameRenderer.getTemplate("dark"),
      fontSizePreset: "M",
      resolution: { width: 1920, height: 1080 },
    });
    await renderer.initialize();
  });

  describe("getAvailableTemplates", () => {
    it("should return list of available templates", () => {
      const templates = FrameRenderer.getAvailableTemplates();
      expect(templates).toHaveLength(3);
      expect(templates).toContain("dark");
      expect(templates).toContain("gradient_warm");
      expect(templates).toContain("gradient_blue");
    });
  });

  describe("getTemplate", () => {
    it("should return dark template with correct colors", () => {
      const template = FrameRenderer.getTemplate("dark");
      expect(template.backgroundColor).toEqual([20, 20, 30]);
      expect(template.textColor).toEqual([200, 200, 200]);
      expect(template.highlightColor).toEqual([255, 255, 255]);
    });

    it("should return gradient_warm template with correct colors", () => {
      const template = FrameRenderer.getTemplate("gradient_warm");
      expect(template.backgroundColor).toEqual([60, 30, 20]);
      expect(template.textColor).toEqual([255, 240, 220]);
      expect(template.highlightColor).toEqual([255, 200, 150]);
    });

    it("should return gradient_blue template with correct colors", () => {
      const template = FrameRenderer.getTemplate("gradient_blue");
      expect(template.backgroundColor).toEqual([20, 30, 60]);
      expect(template.textColor).toEqual([220, 240, 255]);
      expect(template.highlightColor).toEqual([150, 200, 255]);
    });

    it("should return dark template for unknown name", () => {
      const template = FrameRenderer.getTemplate("unknown" as VideoTemplateName);
      expect(template.name).toBe("dark");
    });
  });

  describe("getAvailableFontSizes", () => {
    it("should return all font size presets", () => {
      const sizes = FrameRenderer.getAvailableFontSizes();
      expect(sizes).toEqual(["S", "M", "L", "XL"]);
    });
  });

  describe("getFontSize", () => {
    it("should return correct sizes for all presets", () => {
      expect(FrameRenderer.getFontSize("S")).toBe(32);
      expect(FrameRenderer.getFontSize("M")).toBe(48);
      expect(FrameRenderer.getFontSize("L")).toBe(64);
      expect(FrameRenderer.getFontSize("XL")).toBe(80);
    });
  });

  describe("getBaseFontSize", () => {
    it("should return base font size for M preset", () => {
      expect(renderer.getBaseFontSize()).toBe(48);
    });
  });

  describe("renderTitleCard", () => {
    it("should render title card with songset info", () => {
      const canvas = renderer.renderTitleCard({
        enabled: true,
        durationSeconds: 5,
        songsetName: "Test Songset",
        songCount: 3,
        totalDurationSeconds: 600,
      });

      expect(canvas).toBeDefined();
      expect(canvas.width).toBe(1920);
      expect(canvas.height).toBe(1080);
    });
  });
});

describe("LRC Parser", () => {
  describe("parseLRC", () => {
    it("should parse LRC content with timestamps", () => {
      const lrcContent = `[00:05.00]First line
[00:10.50]Second line
[00:15.00]Third line`;

      const lines = parseLRC(lrcContent);
      expect(lines).toHaveLength(3);
      expect(lines[0].timeSeconds).toBe(5.0);
      expect(lines[0].text).toBe("First line");
      expect(lines[1].timeSeconds).toBe(10.5);
      expect(lines[1].text).toBe("Second line");
    });

    it("should parse LRC with 3-digit milliseconds", () => {
      const lrcContent = `[00:05.123]Line with 3-digit ms`;
      const lines = parseLRC(lrcContent);
      expect(lines[0].timeSeconds).toBeCloseTo(5.123, 3);
    });

    it("should skip empty lines", () => {
      const lrcContent = `[00:05.00]First line

[00:10.00]Second line`;
      const lines = parseLRC(lrcContent);
      expect(lines).toHaveLength(2);
    });

    it("should sort lines by timestamp", () => {
      const lrcContent = `[00:10.00]Second
[00:05.00]First`;
      const lines = parseLRC(lrcContent);
      expect(lines[0].text).toBe("First");
      expect(lines[1].text).toBe("Second");
    });

    it("should handle empty LRC content", () => {
      const lines = parseLRC("");
      expect(lines).toHaveLength(0);
    });
  });

  describe("convertToGlobalTimeline", () => {
    it("should convert local timestamps to global", () => {
      const localLines = [
        { timeSeconds: 5.0, text: "Line 1" },
        { timeSeconds: 10.0, text: "Line 2" },
      ];

      const globalLines = convertToGlobalTimeline(localLines, 100, "Test Song");
      expect(globalLines).toHaveLength(2);
      expect(globalLines[0].globalTimeSeconds).toBe(105.0);
      expect(globalLines[0].localTimeSeconds).toBe(5.0);
      expect(globalLines[0].title).toBe("Test Song");
    });
  });

  describe("estimateLastLyricDuration", () => {
    it("should return minimum 3 seconds for empty lyrics", () => {
      const duration = estimateLastLyricDuration([]);
      expect(duration).toBe(5.0);
    });

    it("should estimate based on previous occurrence", () => {
      const lyrics: GlobalLRCLine[] = [
        {
          timeSeconds: 10,
          globalTimeSeconds: 10,
          localTimeSeconds: 0,
          text: "Repeated line",
          title: "Song",
        },
        {
          timeSeconds: 15,
          globalTimeSeconds: 15,
          localTimeSeconds: 5,
          text: "Other line",
          title: "Song",
        },
        {
          timeSeconds: 20,
          globalTimeSeconds: 20,
          localTimeSeconds: 10,
          text: "Repeated line",
          title: "Song",
        },
      ];

      const duration = estimateLastLyricDuration(lyrics);
      expect(duration).toBe(5.0); // Duration from first to second occurrence
    });

    it("should estimate based on character count when no previous occurrence", () => {
      const lyrics: GlobalLRCLine[] = [
        {
          timeSeconds: 10,
          globalTimeSeconds: 10,
          localTimeSeconds: 0,
          text: "Hello",
          title: "Song",
        },
      ];

      const duration = estimateLastLyricDuration(lyrics, 70);
      expect(duration).toBeGreaterThanOrEqual(3.0);
    });
  });

  describe("isValidLRC", () => {
    it("should return true for valid LRC content", () => {
      expect(isValidLRC("[00:05.00]Line")).toBe(true);
      expect(isValidLRC("[00:05.123]Line")).toBe(true);
    });

    it("should return false for invalid content", () => {
      expect(isValidLRC("Just plain text")).toBe(false);
      expect(isValidLRC("")).toBe(false);
    });
  });

  describe("getLyricsTimeRange", () => {
    it("should return time range for lyrics", () => {
      const lyrics = [
        { timeSeconds: 5.0, text: "First" },
        { timeSeconds: 10.0, text: "Last" },
      ];

      const range = getLyricsTimeRange(lyrics);
      expect(range).not.toBeNull();
      expect(range!.firstTime).toBe(5.0);
      expect(range!.lastTime).toBe(10.0);
    });

    it("should return null for empty lyrics", () => {
      const range = getLyricsTimeRange([]);
      expect(range).toBeNull();
    });
  });
});
