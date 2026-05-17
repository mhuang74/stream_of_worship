import { describe, it, expect, vi, beforeEach } from "vitest";
import { AudioEngine, SongsetItem } from "@/lib/render/audio-engine";
import { AssetFetcher } from "@/lib/render/asset-fetcher";

/* eslint-disable @typescript-eslint/no-explicit-any */

vi.mock("fluent-ffmpeg", () => {
  const mockFfprobe = vi.fn();
  const mockFfmpeg = vi.fn(() => ({
    input: vi.fn().mockReturnThis(),
    audioFilters: vi.fn().mockReturnThis(),
    audioBitrate: vi.fn().mockReturnThis(),
    outputOptions: vi.fn().mockReturnThis(),
    output: vi.fn().mockReturnThis(),
    on: vi.fn().mockReturnThis(),
    run: vi.fn(),
  }));
  (mockFfmpeg as any).setFfmpegPath = vi.fn();
  (mockFfmpeg as any).ffprobe = mockFfprobe;
  return { default: mockFfmpeg };
});

vi.mock("ffmpeg-static", () => ({
  default: "/usr/bin/ffmpeg",
}));

vi.mock("fs/promises", () => ({
  default: {
    mkdir: vi.fn().mockResolvedValue(undefined),
    stat: vi.fn().mockResolvedValue({ size: 1000 }),
    unlink: vi.fn().mockResolvedValue(undefined),
    writeFile: vi.fn().mockResolvedValue(undefined),
    readFile: vi.fn().mockResolvedValue(Buffer.alloc(100)),
  },
}));

function createTestItem(overrides: Partial<SongsetItem> = {}): SongsetItem {
  return {
    id: "item-1",
    songsetId: "set-1",
    songId: "song-1",
    songTitle: "Test Song",
    recordingHashPrefix: "rec-1",
    position: 0,
    gapBeats: 2,
    crossfadeEnabled: 0,
    crossfadeDurationSeconds: null,
    keyShiftSemitones: 0,
    tempoRatio: 1,
    tempoBpm: 120,
    durationSeconds: 180,
    ...overrides,
  };
}

describe("AudioEngine", () => {
  let engine: AudioEngine;
  let mockAssetFetcher: AssetFetcher;

  beforeEach(() => {
    vi.clearAllMocks();
    mockAssetFetcher = {
      downloadAudio: vi.fn().mockResolvedValue("/tmp/audio.mp3"),
      downloadLrc: vi.fn().mockResolvedValue(null),
      getTempDir: vi.fn().mockResolvedValue("/tmp/test"),
      cleanupTemp: vi.fn().mockResolvedValue(undefined),
    } as unknown as AssetFetcher;
    engine = new AudioEngine(mockAssetFetcher);
  });

  describe("getCrossfadeMs", () => {
    it("returns 0 when crossfade is disabled", () => {
      const item = createTestItem({ crossfadeEnabled: 0 });
      expect((engine as any).getCrossfadeMs(item)).toBe(0);
    });

    it("returns crossfade duration in ms when enabled", () => {
      const item = createTestItem({
        crossfadeEnabled: 1,
        crossfadeDurationSeconds: 2.5,
      });
      expect((engine as any).getCrossfadeMs(item)).toBe(2500);
    });

    it("returns 0 when crossfadeEnabled but no duration", () => {
      const item = createTestItem({
        crossfadeEnabled: 1,
        crossfadeDurationSeconds: null,
      });
      expect((engine as any).getCrossfadeMs(item)).toBe(0);
    });
  });

  describe("calculateGapMs", () => {
    it("calculates gap from beats and tempo", () => {
      const item = createTestItem({ gapBeats: 2 });
      expect(engine.calculateGapMs(item, 120)).toBe(1000);
    });

    it("uses default 2 beats when gapBeats is null", () => {
      const item = createTestItem({ gapBeats: null });
      expect(engine.calculateGapMs(item, 120)).toBe(1000);
    });

    it("falls back to 1 second per beat when no tempo", () => {
      const item = createTestItem({ gapBeats: 3 });
      expect(engine.calculateGapMs(item, null)).toBe(3000);
    });

    it("returns 0 when crossfade is enabled", () => {
      const item = createTestItem({
        crossfadeEnabled: 1,
        crossfadeDurationSeconds: 2,
      });
      expect(engine.calculateGapMs(item, 120)).toBe(0);
    });
  });

  describe("calculateTotalDuration", () => {
    it("returns 0 for empty items", async () => {
      expect(await engine.calculateTotalDuration([])).toBe(0);
    });

    it("returns duration for single item", async () => {
      const items = [createTestItem({ durationSeconds: 180 })];
      expect(await engine.calculateTotalDuration(items)).toBe(180);
    });

    it("adds gap duration between items", async () => {
      const items = [
        createTestItem({ durationSeconds: 180, gapBeats: 2, tempoBpm: 120 }),
        createTestItem({ durationSeconds: 200, gapBeats: 2, tempoBpm: 120 }),
      ];
      const total = await engine.calculateTotalDuration(items);
      expect(total).toBeGreaterThan(380);
    });
  });

  describe("generateSongsetAudio", () => {
    it("throws error for empty items", async () => {
      await expect(
        engine.generateSongsetAudio([], "/tmp/output.mp3")
      ).rejects.toThrow();
    });
  });
});
