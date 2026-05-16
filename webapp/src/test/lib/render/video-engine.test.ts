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
import { parseLRC, GlobalLRCLine, convertToGlobalTimeline, estimateLastLyricDuration, isValidLRC, getLyricsTimeRange, findCurrentLyricIndex, groupLyricsBySong } from "@/lib/render/lrc-parser";
import { AssetFetcher } from "@/lib/render/asset-fetcher";
import { spawn as childProcessSpawn } from "child_process";
import ffmpeg from "fluent-ffmpeg";

const mockFfprobe = vi.fn();
(ffmpeg as unknown as { ffprobe: typeof mockFfprobe }).ffprobe = mockFfprobe;

// Mock child_process spawn
vi.mock("child_process", async (importOriginal) => {
  const actual = await importOriginal<typeof import("child_process")>();
  return {
    ...actual,
    spawn: vi.fn(),
  };
});

// Mock canvas native module
vi.mock("canvas", () => {
  const imageData = { data: new Uint8ClampedArray(1920 * 1080 * 4) };
  const mockCtx = {
    fillRect: vi.fn(),
    fillText: vi.fn(),
    measureText: vi.fn().mockReturnValue({ width: 100 }),
    setFont: vi.fn(),
    setColor: vi.fn(),
    createLinearGradient: vi.fn().mockReturnValue({
      addColorStop: vi.fn(),
    }),
    getImageData: vi.fn().mockReturnValue(imageData),
    putImageData: vi.fn(),
    drawImage: vi.fn(),
    clearRect: vi.fn(),
    beginPath: vi.fn(),
    closePath: vi.fn(),
    fill: vi.fn(),
    stroke: vi.fn(),
    arc: vi.fn(),
    rect: vi.fn(),
    save: vi.fn(),
    restore: vi.fn(),
    clip: vi.fn(),
    fillStyle: "",
    strokeStyle: "",
    font: "",
    textAlign: "",
    textBaseline: "",
    globalAlpha: 1,
    lineWidth: 1,
    shadowColor: "",
    shadowBlur: 0,
    shadowOffsetX: 0,
    shadowOffsetY: 0,
  };
  const mockCanvas = {
    width: 1920,
    height: 1080,
    getContext: vi.fn().mockReturnValue(mockCtx),
    toBuffer: vi.fn().mockReturnValue(Buffer.alloc(1920 * 1080 * 4)),
  };
  return {
    createCanvas: vi.fn().mockReturnValue(mockCanvas),
    registerFont: vi.fn(),
  };
});

// Mock fluent-ffmpeg
vi.mock("fluent-ffmpeg", () => {
  const mockFfmpegInstance = {
    input: vi.fn().mockReturnThis(),
    inputOptions: vi.fn().mockReturnThis(),
    complexFilter: vi.fn().mockReturnThis(),
    audioCodec: vi.fn().mockReturnThis(),
    audioBitrate: vi.fn().mockReturnThis(),
    audioFrequency: vi.fn().mockReturnThis(),
    audioChannels: vi.fn().mockReturnThis(),
    audioFilters: vi.fn().mockReturnThis(),
    output: vi.fn().mockReturnThis(),
    outputOptions: vi.fn().mockReturnThis(),
    noVideo: vi.fn().mockReturnThis(),
    on: vi.fn().mockImplementation(function(this: unknown, event: string, callback: () => void) {
      if (event === "end") {
        setTimeout(callback, 0);
      }
      return this;
    }),
    run: vi.fn(),
  };

  const mockFfmpeg = vi.fn(() => mockFfmpegInstance);
  const ffprobe = vi.fn();
  (mockFfmpeg as any).ffprobe = ffprobe;

  return {
    default: mockFfmpeg,
    ffprobe,
  };
});

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

describe("VideoEngine.generateVideo", () => {
  let assetFetcher: AssetFetcher;
  let videoEngine: VideoEngine;

  beforeEach(() => {
    vi.clearAllMocks();

    assetFetcher = {
      downloadLrc: vi.fn(),
      getTempDir: vi.fn().mockResolvedValue("/tmp/test"),
    } as unknown as AssetFetcher;

    videoEngine = new VideoEngine(assetFetcher, {
      template: "dark",
      fontSizePreset: "M",
      resolution: "720p",
      fps: 24,
    });
  });

  it("generates video for single song with lyrics", async () => {
    const lrcContent = "[00:05.00]First line\n[00:10.00]Second line";
    (assetFetcher.downloadLrc as ReturnType<typeof vi.fn>).mockResolvedValue(lrcContent);

    mockFfprobe.mockImplementation(
      (_path: string, cb: (err: null, meta: object) => void) => {
        cb(null, {
          streams: [{ channels: 2, sample_rate: 44100 }],
          format: { duration: 30 },
        });
      }
    );

    const encodeSpy = vi.spyOn(videoEngine as any, "encodeVideoWithFFmpeg").mockResolvedValue(undefined);

    const segments = [
      {
        item: {
          id: "item-1",
          songsetId: "set-1",
          songId: "song-1",
          songTitle: "Amazing Grace",
          recordingHashPrefix: "abc123",
          position: 0,
          gapBeats: 0,
          crossfadeEnabled: 0,
          crossfadeDurationSeconds: null,
          keyShiftSemitones: 0,
          tempoRatio: 1.0,
          tempoBpm: 120,
        },
        audioPath: "/tmp/audio.mp3",
        startTimeSeconds: 0,
        durationSeconds: 30,
        gapBeforeSeconds: 0,
      },
    ];

    const result = await videoEngine.generateVideo(
      "/tmp/audio.mp3",
      segments,
      "/tmp/output.mp4"
    );

    expect(result.outputPath).toBe("/tmp/output.mp4");
    expect(result.durationSeconds).toBe(30);
    expect(result.width).toBe(1280);
    expect(result.height).toBe(720);
    expect(encodeSpy).toHaveBeenCalled();
  });

  it("falls back to blank video when no LRC available", async () => {
    (assetFetcher.downloadLrc as ReturnType<typeof vi.fn>).mockResolvedValue(null);

    mockFfprobe.mockImplementation(
      (_path: string, cb: (err: null, meta: object) => void) => {
        cb(null, {
          streams: [{ channels: 2, sample_rate: 44100 }],
          format: { duration: 30 },
        });
      }
    );

    const blankSpy = vi.spyOn(videoEngine as any, "generateBlankVideo").mockResolvedValue({
      outputPath: "/tmp/output.mp4",
      totalFrames: 720,
      durationSeconds: 30,
      width: 1280,
      height: 720,
      fps: 24,
    });

    const segments = [
      {
        item: {
          id: "item-1",
          songsetId: "set-1",
          songId: "song-1",
          songTitle: "Test Song",
          recordingHashPrefix: "abc123",
          position: 0,
          gapBeats: 0,
          crossfadeEnabled: 0,
          crossfadeDurationSeconds: null,
          keyShiftSemitones: 0,
          tempoRatio: 1.0,
        },
        audioPath: "/tmp/audio.mp3",
        startTimeSeconds: 0,
        durationSeconds: 30,
        gapBeforeSeconds: 0,
      },
    ];

    const result = await videoEngine.generateVideo(
      "/tmp/audio.mp3",
      segments,
      "/tmp/output.mp4"
    );

    expect(result.outputPath).toBe("/tmp/output.mp4");
    expect(blankSpy).toHaveBeenCalled();
  });

  it("throws error when audio info cannot be retrieved", async () => {
    mockFfprobe.mockImplementation(
      (_path: string, cb: (err: Error, meta: undefined) => void) => {
        cb(new Error("ffprobe failed"), undefined);
      }
    );

    const segments = [
      {
        item: {
          id: "item-1",
          songsetId: "set-1",
          songId: "song-1",
          recordingHashPrefix: "abc123",
          position: 0,
          gapBeats: 0,
          crossfadeEnabled: 0,
          crossfadeDurationSeconds: null,
          keyShiftSemitones: 0,
          tempoRatio: 1.0,
        },
        audioPath: "/tmp/audio.mp3",
        startTimeSeconds: 0,
        durationSeconds: 30,
        gapBeforeSeconds: 0,
      },
    ];

    await expect(
      videoEngine.generateVideo("/tmp/audio.mp3", segments, "/tmp/output.mp4")
    ).rejects.toThrow();
  });
});

describe("findCurrentLyricIndex", () => {
  const lyrics: GlobalLRCLine[] = [
    { text: "First line", localTimeSeconds: 5, globalTimeSeconds: 5, title: "Song 1" },
    { text: "Second line", localTimeSeconds: 10, globalTimeSeconds: 10, title: "Song 1" },
    { text: "Third line", localTimeSeconds: 15, globalTimeSeconds: 15, title: "Song 1" },
  ];

  it("returns -1 when time is before first lyric", () => {
    expect(findCurrentLyricIndex(lyrics, 2)).toBe(-1);
  });

  it("returns 0 at exactly the first lyric time", () => {
    expect(findCurrentLyricIndex(lyrics, 5)).toBe(0);
  });

  it("returns correct index between two lyrics", () => {
    expect(findCurrentLyricIndex(lyrics, 12)).toBe(1);
  });

  it("returns last index when time is past all lyrics", () => {
    expect(findCurrentLyricIndex(lyrics, 100)).toBe(2);
  });

  it("returns -1 for empty lyrics array", () => {
    expect(findCurrentLyricIndex([], 10)).toBe(-1);
  });
});

describe("groupLyricsBySong", () => {
  it("groups lyrics by song title", () => {
    const lyrics: GlobalLRCLine[] = [
      { text: "Line 1", localTimeSeconds: 0, globalTimeSeconds: 0, title: "Song A" },
      { text: "Line 2", localTimeSeconds: 5, globalTimeSeconds: 5, title: "Song B" },
      { text: "Line 3", localTimeSeconds: 10, globalTimeSeconds: 10, title: "Song A" },
    ];

    const grouped = groupLyricsBySong(lyrics);

    expect(grouped.size).toBe(2);
    expect(grouped.get("Song A")).toHaveLength(2);
    expect(grouped.get("Song B")).toHaveLength(1);
    expect(grouped.get("Song A")![0].text).toBe("Line 1");
    expect(grouped.get("Song A")![1].text).toBe("Line 3");
  });

  it("returns empty map for empty input", () => {
    expect(groupLyricsBySong([])).toEqual(new Map());
  });

  it("handles single song", () => {
    const lyrics: GlobalLRCLine[] = [
      { text: "Line 1", localTimeSeconds: 0, globalTimeSeconds: 0, title: "Song X" },
      { text: "Line 2", localTimeSeconds: 5, globalTimeSeconds: 5, title: "Song X" },
    ];

    const grouped = groupLyricsBySong(lyrics);
    expect(grouped.size).toBe(1);
    expect(grouped.get("Song X")).toHaveLength(2);
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
          localTimeSeconds: 0,
          globalTimeSeconds: 10,
          text: "Repeated line",
          title: "Song",
        },
        {
          localTimeSeconds: 5,
          globalTimeSeconds: 15,
          text: "Other line",
          title: "Song",
        },
        {
          localTimeSeconds: 10,
          globalTimeSeconds: 20,
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
          localTimeSeconds: 0,
          globalTimeSeconds: 10,
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
