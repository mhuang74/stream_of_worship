import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  R2Client,
  createR2ClientFromEnv,
} from "@/lib/r2/client";

// Create mock functions
const mockGetSignedUrl = vi.fn();
const mockSend = vi.fn();

// Mock AWS SDK
vi.mock("@aws-sdk/client-s3", () => ({
  S3Client: class MockS3Client {
    send = mockSend;
  },
  GetObjectCommand: class MockGetObjectCommand {
    constructor(public params: unknown) {}
  },
  HeadObjectCommand: class MockHeadObjectCommand {
    constructor(public params: unknown) {}
  },
}));

vi.mock("@aws-sdk/s3-request-presigner", () => ({
  getSignedUrl: (...args: unknown[]) => mockGetSignedUrl(...args),
}));

describe("R2Client", () => {
  const mockConfig = {
    endpointUrl: "https://test-account.r2.cloudflarestorage.com",
    accessKeyId: "test-access-key",
    secretAccessKey: "test-secret-key",
    bucketName: "test-bucket",
    region: "auto",
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("constructor", () => {
    it("creates client with provided config", () => {
      const client = new R2Client(mockConfig);
      expect(client).toBeInstanceOf(R2Client);
    });
  });

  describe("generateSignedUrl", () => {
    it("generates signed URL with default options", async () => {
      const mockUrl = "https://test.r2.cloudflarestorage.com/test-bucket/test-key?X-Amz-Algorithm=AWS4-HMAC-SHA256";
      mockGetSignedUrl.mockResolvedValue(mockUrl);

      const client = new R2Client(mockConfig);
      const result = await client.generateSignedUrl("test-key");

      expect(result.url).toBe(mockUrl);
      expect(result.cacheControl).toBe("public, max-age=3600");
      expect(result.expiresAt).toBeInstanceOf(Date);
      expect(result.expiresAt.getTime()).toBeGreaterThan(Date.now());
    });

    it("generates signed URL with custom expiration", async () => {
      const mockUrl = "https://test.r2.cloudflarestorage.com/test-bucket/test-key?X-Amz-Algorithm=AWS4-HMAC-SHA256";
      mockGetSignedUrl.mockResolvedValue(mockUrl);

      const client = new R2Client(mockConfig);
      const result = await client.generateSignedUrl("test-key", "audio", {
        expiresInSeconds: 7200,
      });

      expect(result.url).toBe(mockUrl);
      const expectedExpiry = Date.now() + 7200 * 1000;
      expect(result.expiresAt.getTime()).toBeCloseTo(expectedExpiry, -2);
    });

    it("generates signed URL for video files", async () => {
      const mockUrl = "https://test.r2.cloudflarestorage.com/test-bucket/test-key?X-Amz-Algorithm=AWS4-HMAC-SHA256";
      mockGetSignedUrl.mockResolvedValue(mockUrl);

      const client = new R2Client(mockConfig);
      const result = await client.generateSignedUrl("test-key", "video");

      expect(result.cacheControl).toBe("public, max-age=3600");
    });

    it("generates signed URL for LRC files", async () => {
      const mockUrl = "https://test.r2.cloudflarestorage.com/test-bucket/test-key?X-Amz-Algorithm=AWS4-SHA256";
      mockGetSignedUrl.mockResolvedValue(mockUrl);

      const client = new R2Client(mockConfig);
      const result = await client.generateSignedUrl("test-key", "lrc");

      expect(result.cacheControl).toBe("public, max-age=86400");
    });

    it("includes content disposition in signed URL", async () => {
      const mockUrl = "https://test.r2.cloudflarestorage.com/test-bucket/test-key?X-Amz-Algorithm=AWS4-HMAC-SHA256";
      mockGetSignedUrl.mockResolvedValue(mockUrl);

      const client = new R2Client(mockConfig);
      await client.generateSignedUrl("test-key", "audio", {
        contentDisposition: 'attachment; filename="test.mp3"',
      });

      const getObjectCall = mockGetSignedUrl.mock.calls[0];
      expect(getObjectCall[1].params).toMatchObject({
        ResponseContentDisposition: 'attachment; filename="test.mp3"',
      });
    });
  });

  describe("getAudioSignedUrl", () => {
    it("generates signed URL for audio file by hash prefix", async () => {
      const mockUrl = "https://test.r2.cloudflarestorage.com/test-bucket/abc123/audio.mp3?X-Amz-Algorithm=AWS4-HMAC-SHA256";
      mockGetSignedUrl.mockResolvedValue(mockUrl);

      const client = new R2Client(mockConfig);
      const result = await client.getAudioSignedUrl("abc123");

      expect(result.url).toBe(mockUrl);
      const getObjectCall = mockGetSignedUrl.mock.calls[0];
      expect(getObjectCall[1].params).toMatchObject({
        Key: "abc123/audio.mp3",
      });
    });
  });

  describe("getLrcSignedUrl", () => {
    it("generates signed URL for LRC file by hash prefix", async () => {
      const mockUrl = "https://test.r2.cloudflarestorage.com/test-bucket/abc123/lyrics.lrc?X-Amz-Algorithm=AWS4-HMAC-SHA256";
      mockGetSignedUrl.mockResolvedValue(mockUrl);

      const client = new R2Client(mockConfig);
      const result = await client.getLrcSignedUrl("abc123");

      expect(result.url).toBe(mockUrl);
      const getObjectCall = mockGetSignedUrl.mock.calls[0];
      expect(getObjectCall[1].params).toMatchObject({
        Key: "abc123/lyrics.lrc",
      });
    });
  });

  describe("getVideoSignedUrl", () => {
    it("generates signed URL for video file by render job ID", async () => {
      const mockUrl = "https://test.r2.cloudflarestorage.com/test-bucket/renders/job-123/output.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256";
      mockGetSignedUrl.mockResolvedValue(mockUrl);

      const client = new R2Client(mockConfig);
      const result = await client.getVideoSignedUrl("job-123");

      expect(result.url).toBe(mockUrl);
      const getObjectCall = mockGetSignedUrl.mock.calls[0];
      expect(getObjectCall[1].params).toMatchObject({
        Key: "renders/job-123/output.mp4",
      });
    });
  });

  describe("getRenderedAudioSignedUrl", () => {
    it("generates signed URL for rendered MP3 by render job ID", async () => {
      const mockUrl = "https://test.r2.cloudflarestorage.com/test-bucket/renders/job-123/output.mp3?X-Amz-Algorithm=AWS4-HMAC-SHA256";
      mockGetSignedUrl.mockResolvedValue(mockUrl);

      const client = new R2Client(mockConfig);
      const result = await client.getRenderedAudioSignedUrl("job-123");

      expect(result.url).toBe(mockUrl);
      const getObjectCall = mockGetSignedUrl.mock.calls[0];
      expect(getObjectCall[1].params).toMatchObject({
        Key: "renders/job-123/output.mp3",
      });
    });
  });

  describe("getChaptersSignedUrl", () => {
    it("generates signed URL for chapters JSON by render job ID", async () => {
      const mockUrl = "https://test.r2.cloudflarestorage.com/test-bucket/renders/job-123/chapters.json?X-Amz-Algorithm=AWS4-HMAC-SHA256";
      mockGetSignedUrl.mockResolvedValue(mockUrl);

      const client = new R2Client(mockConfig);
      const result = await client.getChaptersSignedUrl("job-123");

      expect(result.url).toBe(mockUrl);
      const getObjectCall = mockGetSignedUrl.mock.calls[0];
      expect(getObjectCall[1].params).toMatchObject({
        Key: "renders/job-123/chapters.json",
      });
    });
  });

  describe("fileExists", () => {
    it("returns true when file exists", async () => {
      mockSend.mockResolvedValue({});

      const client = new R2Client(mockConfig);
      const exists = await client.fileExists("test-key");

      expect(exists).toBe(true);
    });

    it("returns false when file does not exist", async () => {
      const notFoundError = { name: "NotFound" };
      mockSend.mockRejectedValue(notFoundError);

      const client = new R2Client(mockConfig);
      const exists = await client.fileExists("test-key");

      expect(exists).toBe(false);
    });

    it("throws on other errors", async () => {
      const otherError = new Error("Network error");
      mockSend.mockRejectedValue(otherError);

      const client = new R2Client(mockConfig);
      await expect(client.fileExists("test-key")).rejects.toThrow("Network error");
    });
  });

  describe("parseS3Url", () => {
    it("parses valid S3 URL", () => {
      const result = R2Client.parseS3Url("s3://my-bucket/path/to/file.mp3");
      expect(result).toEqual({
        bucket: "my-bucket",
        key: "path/to/file.mp3",
      });
    });

    it("parses S3 URL with nested paths", () => {
      const result = R2Client.parseS3Url("s3://test-bucket/renders/job-123/output.mp4");
      expect(result).toEqual({
        bucket: "test-bucket",
        key: "renders/job-123/output.mp4",
      });
    });

    it("throws on invalid S3 URL format", () => {
      expect(() => R2Client.parseS3Url("https://example.com/file.mp3")).toThrow(
        "Invalid S3 URL format"
      );
    });

    it("throws on S3 URL without key", () => {
      expect(() => R2Client.parseS3Url("s3://my-bucket")).toThrow(
        "Invalid S3 URL format"
      );
    });

    it("throws on empty S3 URL", () => {
      expect(() => R2Client.parseS3Url("s3://")).toThrow(
        "Invalid S3 URL format"
      );
    });
  });
});

describe("createR2ClientFromEnv", () => {
  const originalEnv = process.env;

  beforeEach(() => {
    process.env = { ...originalEnv };
  });

  afterEach(() => {
    process.env = originalEnv;
  });

  it("creates client from environment variables", () => {
    process.env.SOW_R2_ENDPOINT_URL = "https://test-account.r2.cloudflarestorage.com";
    process.env.SOW_R2_ACCESS_KEY_ID = "test-access-key";
    process.env.SOW_R2_SECRET_ACCESS_KEY = "test-secret-key";
    process.env.SOW_R2_BUCKET = "test-bucket";

    const client = createR2ClientFromEnv();
    expect(client).toBeInstanceOf(R2Client);
  });

  it("throws when SOW_R2_ENDPOINT_URL is missing", () => {
    delete process.env.SOW_R2_ENDPOINT_URL;
    process.env.SOW_R2_ACCESS_KEY_ID = "test-access-key";
    process.env.SOW_R2_SECRET_ACCESS_KEY = "test-secret-key";
    process.env.SOW_R2_BUCKET = "test-bucket";

    expect(() => createR2ClientFromEnv()).toThrow("R2 credentials not configured");
  });

  it("throws when SOW_R2_ACCESS_KEY_ID is missing", () => {
    process.env.SOW_R2_ENDPOINT_URL = "https://test-account.r2.cloudflarestorage.com";
    delete process.env.SOW_R2_ACCESS_KEY_ID;
    process.env.SOW_R2_SECRET_ACCESS_KEY = "test-secret-key";
    process.env.SOW_R2_BUCKET = "test-bucket";

    expect(() => createR2ClientFromEnv()).toThrow("R2 credentials not configured");
  });

  it("throws when SOW_R2_SECRET_ACCESS_KEY is missing", () => {
    process.env.SOW_R2_ENDPOINT_URL = "https://test-account.r2.cloudflarestorage.com";
    process.env.SOW_R2_ACCESS_KEY_ID = "test-access-key";
    delete process.env.SOW_R2_SECRET_ACCESS_KEY;
    process.env.SOW_R2_BUCKET = "test-bucket";

    expect(() => createR2ClientFromEnv()).toThrow("R2 credentials not configured");
  });

  it("throws when SOW_R2_BUCKET is missing", () => {
    process.env.SOW_R2_ENDPOINT_URL = "https://test-account.r2.cloudflarestorage.com";
    process.env.SOW_R2_ACCESS_KEY_ID = "test-access-key";
    process.env.SOW_R2_SECRET_ACCESS_KEY = "test-secret-key";
    delete process.env.SOW_R2_BUCKET;

    expect(() => createR2ClientFromEnv()).toThrow("R2 credentials not configured");
  });
});
