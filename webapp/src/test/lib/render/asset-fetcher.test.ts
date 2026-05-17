import { describe, it, expect, beforeEach, vi } from "vitest";
import { AssetFetcher } from "@/lib/render/asset-fetcher";

/* eslint-disable @typescript-eslint/no-explicit-any */

const mockGetAudioSignedUrl = vi.fn();
const mockGetLrcSignedUrl = vi.fn();

vi.mock("@/lib/r2/client", () => ({
  createR2ClientFromEnv: vi.fn(() => ({
    getAudioSignedUrl: mockGetAudioSignedUrl,
    getLrcSignedUrl: mockGetLrcSignedUrl,
  })),
  R2Client: class {},
}));

vi.mock("fs/promises", () => ({
  access: vi.fn(),
  mkdir: vi.fn(),
  writeFile: vi.fn(),
  readdir: vi.fn(),
  stat: vi.fn(),
  unlink: vi.fn(),
}));

import * as fs from "fs/promises";

const mockFetch = vi.fn();
global.fetch = mockFetch;

describe("AssetFetcher", () => {
  let fetcher: AssetFetcher;

  beforeEach(() => {
    vi.clearAllMocks();
    fetcher = new AssetFetcher({
      cacheDir: "/tmp/sow-test/cache",
      tempDir: "/tmp/sow-test/temp",
    });
  });

  describe("constructor", () => {
    it("uses configured paths", () => {
      expect(fetcher.getCacheDir()).toBe("/tmp/sow-test/cache");
    });
  });

  describe("initialize", () => {
    it("creates cache and temp directories", async () => {
      vi.mocked(fs.mkdir).mockResolvedValue(undefined);
      await fetcher.initialize();
      expect(fs.mkdir).toHaveBeenCalledWith("/tmp/sow-test/cache", { recursive: true });
      expect(fs.mkdir).toHaveBeenCalledWith("/tmp/sow-test/temp", { recursive: true });
    });
  });

  describe("getTempDir", () => {
    it("creates temp directory and returns path", async () => {
      vi.mocked(fs.mkdir).mockResolvedValue(undefined);
      const dir = await fetcher.getTempDir();
      expect(dir).toBe("/tmp/sow-test/temp");
      expect(fs.mkdir).toHaveBeenCalledWith("/tmp/sow-test/temp", { recursive: true });
    });
  });

  describe("downloadAudio", () => {
    it("returns cached file path when already present", async () => {
      vi.mocked(fs.access).mockResolvedValue(undefined);
      const result = await fetcher.downloadAudio("abc123");
      expect(result).toBe("/tmp/sow-test/cache/abc123.mp3");
      expect(mockGetAudioSignedUrl).not.toHaveBeenCalled();
    });

    it("downloads file from R2 when not cached", async () => {
      vi.mocked(fs.access).mockRejectedValue(new Error("Not found"));
      mockGetAudioSignedUrl.mockResolvedValue({
        url: "https://r2.example.com/audio.mp3",
      });
      mockFetch.mockResolvedValue({
        ok: true,
        arrayBuffer: async () => new ArrayBuffer(4),
      });
      vi.mocked(fs.mkdir).mockResolvedValue(undefined);
      vi.mocked(fs.writeFile).mockResolvedValue(undefined);

      const result = await fetcher.downloadAudio("abc123");
      expect(result).toBe("/tmp/sow-test/cache/abc123.mp3");
      expect(mockGetAudioSignedUrl).toHaveBeenCalledWith("abc123", expect.objectContaining({ expiresInSeconds: 3600 }));
      expect(fs.writeFile).toHaveBeenCalled();
    });

    it("returns null when download fails", async () => {
      vi.mocked(fs.access).mockRejectedValue(new Error("Not found"));
      mockGetAudioSignedUrl.mockResolvedValue({
        url: "https://r2.example.com/audio.mp3",
      });
      mockFetch.mockResolvedValue({
        ok: false,
        status: 404,
        statusText: "Not Found",
      });

      const result = await fetcher.downloadAudio("abc123");
      expect(result).toBeNull();
    });

    it("returns null on fetch error", async () => {
      vi.mocked(fs.access).mockRejectedValue(new Error("Not found"));
      mockGetAudioSignedUrl.mockResolvedValue({
        url: "https://r2.example.com/audio.mp3",
      });
      mockFetch.mockRejectedValue(new Error("Network error"));

      const result = await fetcher.downloadAudio("abc123");
      expect(result).toBeNull();
    });
  });

  describe("downloadLrc", () => {
    it("downloads LRC file from R2", async () => {
      mockGetLrcSignedUrl.mockResolvedValue({
        url: "https://r2.example.com/lyrics.lrc",
      });
      mockFetch.mockResolvedValue({
        ok: true,
        text: async () => "[00:12.34]Test lyric",
      });

      const result = await fetcher.downloadLrc("abc123");
      expect(result).toBe("[00:12.34]Test lyric");
      expect(mockGetLrcSignedUrl).toHaveBeenCalledWith("abc123", expect.objectContaining({ expiresInSeconds: 3600 }));
    });

    it("returns null on fetch error", async () => {
      mockGetLrcSignedUrl.mockResolvedValue({
        url: "https://r2.example.com/lyrics.lrc",
      });
      mockFetch.mockRejectedValue(new Error("Network error"));

      const result = await fetcher.downloadLrc("abc123");
      expect(result).toBeNull();
    });

    it("returns null when response is not ok", async () => {
      mockGetLrcSignedUrl.mockResolvedValue({
        url: "https://r2.example.com/lyrics.lrc",
      });
      mockFetch.mockResolvedValue({
        ok: false,
        status: 500,
        statusText: "Internal Server Error",
      });

      const result = await fetcher.downloadLrc("abc123");
      expect(result).toBeNull();
    });
  });

  describe("isCached", () => {
    it("returns true when cached file exists", async () => {
      vi.mocked(fs.access).mockResolvedValue(undefined);
      const result = await fetcher.isCached("abc123");
      expect(result).toBe(true);
    });

    it("returns false when file does not exist", async () => {
      vi.mocked(fs.access).mockRejectedValue(new Error("Not found"));
      const result = await fetcher.isCached("abc123");
      expect(result).toBe(false);
    });
  });

  describe("clearFileCache", () => {
    it("deletes all cached files", async () => {
      vi.mocked(fs.readdir).mockResolvedValue(["file1.mp3", "file2.mp3"] as any);
      vi.mocked(fs.unlink).mockResolvedValue(undefined);

      await fetcher.clearFileCache();
      expect(fs.unlink).toHaveBeenCalledTimes(2);
    });

    it("handles errors gracefully", async () => {
      vi.mocked(fs.readdir).mockRejectedValue(new Error("Dir not found"));
      await expect(fetcher.clearFileCache()).resolves.not.toThrow();
    });
  });

  describe("getCacheStats", () => {
    it("returns file count and total size", async () => {
      vi.mocked(fs.readdir).mockResolvedValue(["file1.mp3", "file2.mp3"] as any);
      vi.mocked(fs.stat).mockResolvedValueOnce({ size: 100 } as any);
      vi.mocked(fs.stat).mockResolvedValueOnce({ size: 200 } as any);

      const stats = await fetcher.getCacheStats();
      expect(stats.fileCount).toBe(2);
      expect(stats.totalSizeBytes).toBe(300);
    });

    it("returns zeros when directory does not exist", async () => {
      vi.mocked(fs.readdir).mockRejectedValue(new Error("Dir not found"));
      const stats = await fetcher.getCacheStats();
      expect(stats.fileCount).toBe(0);
      expect(stats.totalSizeBytes).toBe(0);
    });
  });

  describe("cleanupTemp", () => {
    it("deletes all temp files", async () => {
      vi.mocked(fs.readdir).mockResolvedValue(["temp1.mp3", "temp2.mp3"] as any);
      vi.mocked(fs.unlink).mockResolvedValue(undefined);

      await fetcher.cleanupTemp();
      expect(fs.unlink).toHaveBeenCalledTimes(2);
    });

    it("handles errors gracefully", async () => {
      vi.mocked(fs.readdir).mockRejectedValue(new Error("Dir not found"));
      await expect(fetcher.cleanupTemp()).resolves.not.toThrow();
    });
  });
});
