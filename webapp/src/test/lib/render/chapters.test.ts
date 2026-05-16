import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  generateChaptersManifest,
  generateChaptersManifestFromLyrics,
  chaptersToFFmpegMetadata,
  findChapterAtTime,
  getSongTitleAtTime,
  getLyricAtTime,
  parseChaptersManifest,
  getChapterDurations,
  getChapterProgress,
} from "@/lib/render/chapters";
import { AudioSegmentInfo } from "@/lib/render/audio-engine";
import { AssetFetcher } from "@/lib/render/asset-fetcher";
import { LRCLine } from "@/lib/render/lrc-parser";

describe("chapters", () => {
  let mockAssetFetcher: AssetFetcher;

  beforeEach(() => {
    mockAssetFetcher = {
      downloadLrc: vi.fn(),
    } as unknown as AssetFetcher;
  });

  describe("generateChaptersManifest", () => {
    it("generates manifest with chapters from segments", async () => {
      const segments: AudioSegmentInfo[] = [
        {
          item: {
            id: "item-1",
            songsetId: "set-1",
            songId: "Amazing Grace",
            recordingHashPrefix: "abc123",
            position: 0,
            gapBeats: 2,
            crossfadeEnabled: 0,
            crossfadeDurationSeconds: null,
            keyShiftSemitones: 0,
            tempoRatio: 1,
          },
          audioPath: "/tmp/audio1.mp3",
          startTimeSeconds: 0,
          durationSeconds: 180,
          gapBeforeSeconds: 0,
        },
        {
          item: {
            id: "item-2",
            songsetId: "set-1",
            songId: "How Great Thou Art",
            recordingHashPrefix: "def456",
            position: 1,
            gapBeats: 2,
            crossfadeEnabled: 0,
            crossfadeDurationSeconds: null,
            keyShiftSemitones: 0,
            tempoRatio: 1,
          },
          audioPath: "/tmp/audio2.mp3",
          startTimeSeconds: 180,
          durationSeconds: 240,
          gapBeforeSeconds: 2,
        },
      ];

      vi.mocked(mockAssetFetcher.downloadLrc).mockResolvedValue(null);

      const manifest = await generateChaptersManifest(
        segments,
        mockAssetFetcher,
        420
      );

      expect(manifest.chapters).toHaveLength(2);
      expect(manifest.totalDurationSeconds).toBe(420);
      expect(manifest.generatedAt).toBeDefined();

      // First chapter
      expect(manifest.chapters[0].position).toBe(1);
      expect(manifest.chapters[0].songTitle).toBe("Amazing Grace");
      expect(manifest.chapters[0].startSeconds).toBe(0);
      expect(manifest.chapters[0].endSeconds).toBe(180);
      expect(manifest.chapters[0].lines).toEqual([]);

      // Second chapter
      expect(manifest.chapters[1].position).toBe(2);
      expect(manifest.chapters[1].songTitle).toBe("How Great Thou Art");
      expect(manifest.chapters[1].startSeconds).toBe(180);
      expect(manifest.chapters[1].endSeconds).toBe(420);
    });

    it("includes lyrics when LRC files are available", async () => {
      const lrcContent = `[00:00.00] Line one
[00:05.00] Line two
[00:10.00] Line three`;

      const segments: AudioSegmentInfo[] = [
        {
          item: {
            id: "item-1",
            songsetId: "set-1",
            songId: "Test Song",
            recordingHashPrefix: "abc123",
            position: 0,
            gapBeats: 2,
            crossfadeEnabled: 0,
            crossfadeDurationSeconds: null,
            keyShiftSemitones: 0,
            tempoRatio: 1,
          },
          audioPath: "/tmp/audio1.mp3",
          startTimeSeconds: 0,
          durationSeconds: 60,
          gapBeforeSeconds: 0,
        },
      ];

      vi.mocked(mockAssetFetcher.downloadLrc).mockResolvedValue(lrcContent);

      const manifest = await generateChaptersManifest(
        segments,
        mockAssetFetcher,
        60
      );

      expect(manifest.chapters[0].lines).toHaveLength(3);
      expect(manifest.chapters[0].lines[0]).toEqual({
        text: "Line one",
        startSeconds: 0,
      });
      expect(manifest.chapters[0].lines[1]).toEqual({
        text: "Line two",
        startSeconds: 5,
      });
      expect(manifest.chapters[0].lines[2]).toEqual({
        text: "Line three",
        startSeconds: 10,
      });
    });

    it("handles missing recording hash prefix gracefully", async () => {
      const segments: AudioSegmentInfo[] = [
        {
          item: {
            id: "item-1",
            songsetId: "set-1",
            songId: "Test Song",
            recordingHashPrefix: null,
            position: 0,
            gapBeats: 2,
            crossfadeEnabled: 0,
            crossfadeDurationSeconds: null,
            keyShiftSemitones: 0,
            tempoRatio: 1,
          },
          audioPath: "/tmp/audio1.mp3",
          startTimeSeconds: 0,
          durationSeconds: 60,
          gapBeforeSeconds: 0,
        },
      ];

      const manifest = await generateChaptersManifest(
        segments,
        mockAssetFetcher,
        60
      );

      expect(manifest.chapters[0].lines).toEqual([]);
      expect(mockAssetFetcher.downloadLrc).not.toHaveBeenCalled();
    });

    it("handles LRC download errors gracefully", async () => {
      const segments: AudioSegmentInfo[] = [
        {
          item: {
            id: "item-1",
            songsetId: "set-1",
            songId: "Test Song",
            recordingHashPrefix: "abc123",
            position: 0,
            gapBeats: 2,
            crossfadeEnabled: 0,
            crossfadeDurationSeconds: null,
            keyShiftSemitones: 0,
            tempoRatio: 1,
          },
          audioPath: "/tmp/audio1.mp3",
          startTimeSeconds: 0,
          durationSeconds: 60,
          gapBeforeSeconds: 0,
        },
      ];

      vi.mocked(mockAssetFetcher.downloadLrc).mockRejectedValue(
        new Error("Download failed")
      );

      const manifest = await generateChaptersManifest(
        segments,
        mockAssetFetcher,
        60
      );

      expect(manifest.chapters[0].lines).toEqual([]);
    });
  });

  describe("generateChaptersManifestFromLyrics", () => {
    it("generates manifest from pre-loaded lyrics", () => {
      const segments: AudioSegmentInfo[] = [
        {
          item: {
            id: "item-1",
            songsetId: "set-1",
            songId: "Song One",
            recordingHashPrefix: "abc123",
            position: 0,
            gapBeats: 2,
            crossfadeEnabled: 0,
            crossfadeDurationSeconds: null,
            keyShiftSemitones: 0,
            tempoRatio: 1,
          },
          audioPath: "/tmp/audio1.mp3",
          startTimeSeconds: 0,
          durationSeconds: 60,
          gapBeforeSeconds: 0,
        },
        {
          item: {
            id: "item-2",
            songsetId: "set-1",
            songId: "Song Two",
            recordingHashPrefix: "def456",
            position: 1,
            gapBeats: 2,
            crossfadeEnabled: 0,
            crossfadeDurationSeconds: null,
            keyShiftSemitones: 0,
            tempoRatio: 1,
          },
          audioPath: "/tmp/audio2.mp3",
          startTimeSeconds: 60,
          durationSeconds: 90,
          gapBeforeSeconds: 2,
        },
      ];

      const lyricsMap = new Map<string, LRCLine[]>([
        [
          "abc123",
          [
            { timeSeconds: 0, text: "Line 1" },
            { timeSeconds: 5, text: "Line 2" },
          ],
        ],
        [
          "def456",
          [
            { timeSeconds: 0, text: "Verse 1" },
            { timeSeconds: 10, text: "Verse 2" },
          ],
        ],
      ]);

      const manifest = generateChaptersManifestFromLyrics(
        segments,
        lyricsMap,
        150
      );

      expect(manifest.chapters).toHaveLength(2);
      expect(manifest.chapters[0].lines).toHaveLength(2);
      expect(manifest.chapters[0].lines[0].text).toBe("Line 1");
      expect(manifest.chapters[0].lines[0].startSeconds).toBe(0);
      expect(manifest.chapters[1].lines).toHaveLength(2);
      expect(manifest.chapters[1].lines[0].text).toBe("Verse 1");
      expect(manifest.chapters[1].lines[0].startSeconds).toBe(60);
    });
  });

  describe("chaptersToFFmpegMetadata", () => {
    it("converts manifest to FFmpeg metadata format", () => {
      const manifest = {
        chapters: [
          {
            position: 1,
            songTitle: "Song One",
            startSeconds: 0,
            endSeconds: 180,
            lines: [],
          },
          {
            position: 2,
            songTitle: "Song Two",
            startSeconds: 180,
            endSeconds: 420,
            lines: [],
          },
        ],
        totalDurationSeconds: 420,
        generatedAt: "2024-01-01T00:00:00Z",
      };

      const metadata = chaptersToFFmpegMetadata(manifest);

      expect(metadata).toContain(";FFMETADATA1");
      expect(metadata).toContain("[CHAPTER]");
      expect(metadata).toContain("TIMEBASE=1/1000");
      expect(metadata).toContain("START=0");
      expect(metadata).toContain("END=180000");
      expect(metadata).toContain("title=Song One");
      expect(metadata).toContain("START=180000");
      expect(metadata).toContain("END=420000");
      expect(metadata).toContain("title=Song Two");
    });
  });

  describe("findChapterAtTime", () => {
    const manifest = {
      chapters: [
        {
          position: 1,
          songTitle: "Song One",
          startSeconds: 0,
          endSeconds: 60,
          lines: [],
        },
        {
          position: 2,
          songTitle: "Song Two",
          startSeconds: 60,
          endSeconds: 150,
          lines: [],
        },
        {
          position: 3,
          songTitle: "Song Three",
          startSeconds: 150,
          endSeconds: 200,
          lines: [],
        },
      ],
      totalDurationSeconds: 200,
      generatedAt: "2024-01-01T00:00:00Z",
    };

    it("finds chapter at start time", () => {
      expect(findChapterAtTime(manifest, 0)).toBe(0);
    });

    it("finds chapter in middle", () => {
      expect(findChapterAtTime(manifest, 30)).toBe(0);
      expect(findChapterAtTime(manifest, 90)).toBe(1);
      expect(findChapterAtTime(manifest, 175)).toBe(2);
    });

    it("finds chapter at exact end time", () => {
      expect(findChapterAtTime(manifest, 200)).toBe(2);
    });

    it("returns -1 for time outside chapters", () => {
      expect(findChapterAtTime(manifest, -1)).toBe(-1);
      expect(findChapterAtTime(manifest, 201)).toBe(-1);
    });
  });

  describe("getSongTitleAtTime", () => {
    const manifest = {
      chapters: [
        {
          position: 1,
          songTitle: "Amazing Grace",
          startSeconds: 0,
          endSeconds: 60,
          lines: [],
        },
        {
          position: 2,
          songTitle: "How Great Thou Art",
          startSeconds: 60,
          endSeconds: 120,
          lines: [],
        },
      ],
      totalDurationSeconds: 120,
      generatedAt: "2024-01-01T00:00:00Z",
    };

    it("returns song title at given time", () => {
      expect(getSongTitleAtTime(manifest, 30)).toBe("Amazing Grace");
      expect(getSongTitleAtTime(manifest, 90)).toBe("How Great Thou Art");
    });

    it("returns null for time outside chapters", () => {
      expect(getSongTitleAtTime(manifest, -1)).toBeNull();
      expect(getSongTitleAtTime(manifest, 121)).toBeNull();
    });
  });

  describe("getLyricAtTime", () => {
    const manifest = {
      chapters: [
        {
          position: 1,
          songTitle: "Song One",
          startSeconds: 0,
          endSeconds: 30,
          lines: [
            { text: "Line one", startSeconds: 0 },
            { text: "Line two", startSeconds: 5 },
            { text: "Line three", startSeconds: 10 },
          ],
        },
      ],
      totalDurationSeconds: 30,
      generatedAt: "2024-01-01T00:00:00Z",
    };

    it("returns current lyric at given time", () => {
      expect(getLyricAtTime(manifest, 0)).toBe("Line one");
      expect(getLyricAtTime(manifest, 4)).toBe("Line one");
      expect(getLyricAtTime(manifest, 5)).toBe("Line two");
      expect(getLyricAtTime(manifest, 10)).toBe("Line three");
      expect(getLyricAtTime(manifest, 20)).toBe("Line three");
    });

    it("returns null for time outside chapters", () => {
      expect(getLyricAtTime(manifest, -1)).toBeNull();
      expect(getLyricAtTime(manifest, 31)).toBeNull();
    });
  });

  describe("parseChaptersManifest", () => {
    it("parses valid JSON manifest", () => {
      const json = JSON.stringify({
        chapters: [
          {
            position: 1,
            songTitle: "Test Song",
            startSeconds: 0,
            endSeconds: 60,
            lines: [{ text: "Line one", startSeconds: 0 }],
          },
        ],
        totalDurationSeconds: 60,
        generatedAt: "2024-01-01T00:00:00Z",
      });

      const manifest = parseChaptersManifest(json);

      expect(manifest.chapters).toHaveLength(1);
      expect(manifest.chapters[0].position).toBe(1);
      expect(manifest.chapters[0].songTitle).toBe("Test Song");
      expect(manifest.chapters[0].lines[0].text).toBe("Line one");
    });

    it("throws on invalid manifest structure", () => {
      expect(() => parseChaptersManifest('{"invalid": true}')).toThrow(
        "Invalid chapters manifest"
      );
    });

    it("throws on invalid chapter structure", () => {
      const json = JSON.stringify({
        chapters: [{ invalid: true }],
      });
      expect(() => parseChaptersManifest(json)).toThrow("Invalid chapter");
    });
  });

  describe("getChapterDurations", () => {
    it("calculates chapter durations", () => {
      const manifest = {
        chapters: [
          {
            position: 1,
            songTitle: "Song One",
            startSeconds: 0,
            endSeconds: 60,
            lines: [],
          },
          {
            position: 2,
            songTitle: "Song Two",
            startSeconds: 60,
            endSeconds: 150,
            lines: [],
          },
        ],
        totalDurationSeconds: 150,
        generatedAt: "2024-01-01T00:00:00Z",
      };

      const durations = getChapterDurations(manifest);

      expect(durations).toEqual([60, 90]);
    });
  });

  describe("getChapterProgress", () => {
    const manifest = {
      chapters: [
        {
          position: 1,
          songTitle: "Song One",
          startSeconds: 0,
          endSeconds: 100,
          lines: [],
        },
      ],
      totalDurationSeconds: 100,
      generatedAt: "2024-01-01T00:00:00Z",
    };

    it("calculates progress within chapter", () => {
      const progress = getChapterProgress(manifest, 50);

      expect(progress).toEqual({
        chapterIndex: 0,
        progressPercent: 50,
      });
    });

    it("returns 0 progress at chapter start", () => {
      const progress = getChapterProgress(manifest, 0);

      expect(progress).toEqual({
        chapterIndex: 0,
        progressPercent: 0,
      });
    });

    it("returns 100 progress at chapter end", () => {
      const progress = getChapterProgress(manifest, 100);

      expect(progress).toEqual({
        chapterIndex: 0,
        progressPercent: 100,
      });
    });

    it("returns null for time outside chapters", () => {
      expect(getChapterProgress(manifest, -1)).toBeNull();
      expect(getChapterProgress(manifest, 101)).toBeNull();
    });
  });
});
