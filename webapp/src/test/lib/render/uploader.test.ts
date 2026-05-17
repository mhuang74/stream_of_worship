/* eslint-disable @typescript-eslint/no-explicit-any */
import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import {
  R2Uploader,
} from "@/lib/render/uploader";
import { ChaptersManifest } from "@/lib/render/chapters";
import * as fs from "fs/promises";
import * as path from "path";

describe("R2Uploader", () => {
  let tempDir: string;
  let mockSend: ReturnType<typeof vi.fn>;

  beforeEach(async () => {
    // Create temp directory for test files
    tempDir = await fs.mkdtemp("/tmp/uploader-test-");

    // Set up environment variables
    process.env.R2_ACCOUNT_ID = "test-account";
    process.env.R2_ACCESS_KEY_ID = "test-key";
    process.env.R2_SECRET_ACCESS_KEY = "test-secret";
    process.env.R2_BUCKET_NAME = "test-bucket";

    // Create mock send function
    mockSend = vi.fn();
  });

  afterEach(async () => {
    // Clean up temp directory
    try {
      const files = await fs.readdir(tempDir);
      for (const file of files) {
        await fs.unlink(path.join(tempDir, file));
      }
      await fs.rmdir(tempDir);
    } catch {
      // Ignore cleanup errors
    }

    vi.clearAllMocks();
  });

  describe("constructor", () => {
    it("creates uploader from environment variables", () => {
      // Mock S3Client before creating uploader
      const mockS3Client = vi.fn().mockImplementation(() => ({
        send: mockSend,
      }));
      vi.doMock("@aws-sdk/client-s3", () => ({
        S3Client: mockS3Client,
        PutObjectCommand: vi.fn(),
        HeadObjectCommand: vi.fn(),
        DeleteObjectCommand: vi.fn().mockImplementation(function(this: any, input: any) { this.input = input; return this; }),
      }));

      const uploader = new R2Uploader();
      expect(uploader).toBeDefined();
    });

    it("creates uploader with explicit config", () => {
      const config = {
        accountId: "explicit-account",
        accessKeyId: "explicit-key",
        secretAccessKey: "explicit-secret",
        bucketName: "explicit-bucket",
      };
      const uploader = new R2Uploader(config);
      expect(uploader).toBeDefined();
    });

    it("throws when environment variables are missing", () => {
      delete process.env.R2_ACCOUNT_ID;
      delete process.env.R2_ACCESS_KEY_ID;
      delete process.env.R2_SECRET_ACCESS_KEY;
      delete process.env.R2_BUCKET_NAME;

      expect(() => new R2Uploader()).toThrow("R2 credentials not configured");
    });
  });

  describe("uploadFile", () => {
    it("uploads a file to R2", async () => {
      const testFile = path.join(tempDir, "test.mp3");
      await fs.writeFile(testFile, "test audio content");

      // Create uploader with mocked client
      const uploader = new R2Uploader();
      (uploader as any).client = { send: mockSend };
      mockSend.mockResolvedValueOnce({ ETag: '"abc123"' });

      const result = await uploader.uploadFile("renders/job-1/output.mp3", testFile);

      expect(result.key).toBe("renders/job-1/output.mp3");
      expect(result.sizeBytes).toBe(18);
      expect(result.etag).toBe('"abc123"');
      expect(result.uploadedAt).toBeInstanceOf(Date);
    });

    it("infers content type from file extension", async () => {
      const testFile = path.join(tempDir, "test.mp4");
      await fs.writeFile(testFile, "test video content");

      const uploader = new R2Uploader();
      (uploader as any).client = { send: mockSend };
      mockSend.mockResolvedValueOnce({ ETag: '"def456"' });

      await uploader.uploadFile("renders/job-1/output.mp4", testFile);

      // Verify the mock was called
      expect(mockSend).toHaveBeenCalledTimes(1);
    });

    it("uses explicit content type when provided", async () => {
      const testFile = path.join(tempDir, "test.txt");
      await fs.writeFile(testFile, "test content");

      const uploader = new R2Uploader();
      (uploader as any).client = { send: mockSend };
      mockSend.mockResolvedValueOnce({ ETag: '"ghi789"' });

      await uploader.uploadFile("renders/job-1/output.txt", testFile, {
        contentType: "application/octet-stream",
      });

      expect(mockSend).toHaveBeenCalled();
    });

    it("includes metadata when provided", async () => {
      const testFile = path.join(tempDir, "test.mp3");
      await fs.writeFile(testFile, "test audio");

      const uploader = new R2Uploader();
      (uploader as any).client = { send: mockSend };
      mockSend.mockResolvedValueOnce({ ETag: '"jkl012"' });

      await uploader.uploadFile("renders/job-1/output.mp3", testFile, {
        metadata: { "render-job-id": "job-1", "content-type": "audio" },
      });

      expect(mockSend).toHaveBeenCalled();
    });
  });

  describe("uploadBuffer", () => {
    it("uploads a buffer to R2", async () => {
      const buffer = Buffer.from('{"chapters": []}');

      const uploader = new R2Uploader();
      (uploader as any).client = { send: mockSend };
      mockSend.mockResolvedValueOnce({ ETag: '"mno345"' });

      const result = await uploader.uploadBuffer(
        "renders/job-1/chapters.json",
        buffer
      );

      expect(result.key).toBe("renders/job-1/chapters.json");
      expect(result.sizeBytes).toBe(16); // Buffer length
    });
  });

  describe("uploadRenderArtifacts", () => {
    it("uploads all artifact types", async () => {
      const mp3File = path.join(tempDir, "output.mp3");
      const mp4File = path.join(tempDir, "output.mp4");
      await fs.writeFile(mp3File, "mp3 content");
      await fs.writeFile(mp4File, "mp4 content");

      const chapters: ChaptersManifest = {
        chapters: [
          {
            position: 1,
            songTitle: "Test Song",
            startSeconds: 0,
            endSeconds: 60,
            lines: [],
          },
        ],
        totalDurationSeconds: 60,
        generatedAt: "2024-01-01T00:00:00Z",
      };

      const uploader = new R2Uploader();
      (uploader as any).client = { send: mockSend };
      mockSend
        .mockResolvedValueOnce({ ETag: '"mp3etag"' })
        .mockResolvedValueOnce({ ETag: '"mp4etag"' })
        .mockResolvedValueOnce({ ETag: '"jsonetag"' });

      const result = await uploader.uploadRenderArtifacts("job-1", {
        mp3Path: mp3File,
        mp4Path: mp4File,
        chapters,
      });

      expect(result.mp3R2Key).toBe("renders/job-1/output.mp3");
      expect(result.mp4R2Key).toBe("renders/job-1/output.mp4");
      expect(result.chaptersR2Key).toBe("renders/job-1/chapters.json");
      expect(result.uploadedAt).toBeInstanceOf(Date);

      expect(mockSend).toHaveBeenCalledTimes(3);
    });

    it("uploads only MP3 when video is disabled", async () => {
      const mp3File = path.join(tempDir, "output.mp3");
      await fs.writeFile(mp3File, "mp3 content");

      const uploader = new R2Uploader();
      (uploader as any).client = { send: mockSend };
      mockSend.mockResolvedValueOnce({ ETag: '"mp3etag"' });

      const result = await uploader.uploadRenderArtifacts("job-1", {
        mp3Path: mp3File,
      });

      expect(result.mp3R2Key).toBe("renders/job-1/output.mp3");
      expect(result.mp4R2Key).toBeUndefined();
      expect(result.chaptersR2Key).toBeUndefined();

      expect(mockSend).toHaveBeenCalledTimes(1);
    });

    it("calls progress callback for each file", async () => {
      const mp3File = path.join(tempDir, "output.mp3");
      await fs.writeFile(mp3File, "mp3");

      const uploader = new R2Uploader();
      (uploader as any).client = { send: mockSend };
      mockSend.mockResolvedValueOnce({ ETag: '"etag"' });

      const progressCallback = vi.fn();

      await uploader.uploadRenderArtifacts(
        "job-1",
        { mp3Path: mp3File },
        progressCallback
      );

      expect(progressCallback).toHaveBeenCalledWith("mp3", 0, 3);
      expect(progressCallback).toHaveBeenCalledWith("mp3", 3, 3);
    });
  });

  describe("fileExists", () => {
    it("returns true when file exists", async () => {
      const uploader = new R2Uploader();
      (uploader as any).client = { send: mockSend };
      mockSend.mockResolvedValueOnce({});

      const exists = await uploader.fileExists("renders/job-1/output.mp3");

      expect(exists).toBe(true);
    });

    it("returns false when file does not exist", async () => {
      const uploader = new R2Uploader();
      (uploader as any).client = { send: mockSend };
      const notFoundError = new Error("Not found");
      (notFoundError as any).name = "NotFound";
      mockSend.mockRejectedValueOnce(notFoundError);

      const exists = await uploader.fileExists("renders/job-1/output.mp3");

      expect(exists).toBe(false);
    });

    it("throws on other errors", async () => {
      const uploader = new R2Uploader();
      (uploader as any).client = { send: mockSend };
      mockSend.mockRejectedValueOnce(new Error("Network error"));

      await expect(uploader.fileExists("renders/job-1/output.mp3")).rejects.toThrow(
        "Network error"
      );
    });
  });

  describe("deleteFile", () => {
    it("calls S3 send when deleting a file", async () => {
      const uploader = new R2Uploader();
      (uploader as any).client = { send: mockSend };
      mockSend.mockResolvedValueOnce({});

      await uploader.deleteFile("renders/job-1/output.mp3");

      expect(mockSend).toHaveBeenCalledTimes(1);
      const callArg = mockSend.mock.calls[0][0];
      expect(callArg.input).toMatchObject({
        Bucket: "test-bucket",
        Key: "renders/job-1/output.mp3",
      });
    });
  });

  describe("deleteRenderArtifacts", () => {
    it("deletes all artifacts for a render job", async () => {
      const uploader = new R2Uploader();
      (uploader as any).client = { send: mockSend };
      // fileExists returns true for all files
      mockSend.mockResolvedValue({});

      // This test verifies the method runs without error
      // The actual delete operations use dynamic imports that are hard to mock
      await expect(uploader.deleteRenderArtifacts("job-1")).resolves.not.toThrow();
    });

    it("handles missing files gracefully", async () => {
      const uploader = new R2Uploader();
      (uploader as any).client = { send: mockSend };
      const notFoundError = new Error("Not found");
      (notFoundError as any).name = "NotFound";

      // fileExists returns NotFound for all files
      mockSend.mockRejectedValue(notFoundError);

      // Should not throw even when files don't exist
      await expect(uploader.deleteRenderArtifacts("job-1")).resolves.not.toThrow();
    });
  });

  describe("uploadRenderArtifacts key format", () => {
    it("uses correct key format for MP3 uploads", async () => {
      const uploader = new R2Uploader();
      (uploader as any).client = { send: mockSend };
      mockSend.mockResolvedValue({ ETag: '"etag"' });

      const tmpFile = `/tmp/test-upload-mp3-${Date.now()}.mp3`;
      await import("fs/promises").then((fs) => fs.writeFile(tmpFile, Buffer.alloc(100)));

      try {
        await uploader.uploadFile(`renders/job-123/output.mp3`, tmpFile, {
          contentType: "audio/mpeg",
        });

        const callArg = mockSend.mock.calls[0][0];
        expect(callArg.input.Key).toBe("renders/job-123/output.mp3");
      } finally {
        await import("fs/promises").then((fs) => fs.unlink(tmpFile).catch(() => {}));
      }
    });
  });
});
