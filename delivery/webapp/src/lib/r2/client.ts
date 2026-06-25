import {
  S3Client,
  GetObjectCommand,
  HeadObjectCommand,
} from "@aws-sdk/client-s3";
import { getSignedUrl } from "@aws-sdk/s3-request-presigner";

/**
 * Default signed-URL expiry for non-cast artefacts (audio/lrc/json preview fetches).
 */
export const DEFAULT_EXPIRES_IN_SECONDS = 3600;

/**
 * Signed-URL expiry for Cast / TV-share playback. A 4-hour URL covers a full
 * worship set (~2-3h of songs + setup/transition slack). Services longer than
 * ~3h40m require a deliberate stop/re-cast from the phone, since the receiver
 * fetches the MP4 directly from R2 and never calls the webapp.
 */
export const CAST_PLAYBACK_EXPIRES_IN_SECONDS = 14400;

export interface R2Config {
  endpointUrl: string;
  accessKeyId: string;
  secretAccessKey: string;
  bucketName: string;
  region?: string;
}

export interface SignedUrlOptions {
  expiresInSeconds?: number;
  contentType?: string;
  contentDisposition?: string;
}

export interface SignedUrlResult {
  url: string;
  expiresAt: Date;
  cacheControl: string;
}

export interface FileTypeConfig {
  contentType: string;
  cacheControl: string;
}

const FILE_TYPE_CONFIGS: Record<string, FileTypeConfig> = {
  audio: {
    contentType: "audio/mpeg",
    cacheControl: "public, max-age=3600",
  },
  video: {
    contentType: "video/mp4",
    cacheControl: "public, max-age=3600",
  },
  lrc: {
    contentType: "text/plain; charset=utf-8",
    cacheControl: "public, max-age=86400",
  },
  json: {
    contentType: "application/json",
    cacheControl: "public, max-age=3600",
  },
};

export class R2Client {
  private client: S3Client;
  private bucketName: string;

  constructor(config: R2Config) {
    this.client = new S3Client({
      region: config.region || "auto",
      endpoint: config.endpointUrl,
      credentials: {
        accessKeyId: config.accessKeyId,
        secretAccessKey: config.secretAccessKey,
      },
    });

    this.bucketName = config.bucketName;
  }

  /**
   * Generate a signed URL for accessing a file in R2
   */
  async generateSignedUrl(
    key: string,
    fileType: keyof typeof FILE_TYPE_CONFIGS = "audio",
    options: SignedUrlOptions = {}
  ): Promise<SignedUrlResult> {
    const expiresInSeconds = options.expiresInSeconds || DEFAULT_EXPIRES_IN_SECONDS;
    const fileConfig = FILE_TYPE_CONFIGS[fileType];

    const command = new GetObjectCommand({
      Bucket: this.bucketName,
      Key: key,
      ResponseContentType: options.contentType || fileConfig.contentType,
      ResponseContentDisposition: options.contentDisposition,
    });

    const url = await getSignedUrl(this.client, command, {
      expiresIn: expiresInSeconds,
    });

    const expiresAt = new Date(Date.now() + expiresInSeconds * 1000);

    return {
      url,
      expiresAt,
      cacheControl: fileConfig.cacheControl,
    };
  }

  /**
   * Get the size in bytes of an object in R2.
   * Returns null if the object doesn't exist or size is unavailable.
   */
  async getObjectSize(key: string): Promise<number | null> {
    try {
      const command = new HeadObjectCommand({
        Bucket: this.bucketName,
        Key: key,
      });
      const result = await this.client.send(command);
      return result.ContentLength ?? null;
    } catch {
      return null;
    }
  }

  /**
   * Check if a file exists in R2
   */
  async fileExists(key: string): Promise<boolean> {
    try {
      const command = new HeadObjectCommand({
        Bucket: this.bucketName,
        Key: key,
      });
      await this.client.send(command);
      return true;
    } catch (error) {
      // Check if it's a 404 error
      if (
        error &&
        typeof error === "object" &&
        "name" in error &&
        error.name === "NotFound"
      ) {
        return false;
      }
      throw error;
    }
  }

  /**
   * Generate signed URL for audio file by hash prefix
   */
  async getAudioSignedUrl(
    hashPrefix: string,
    options?: SignedUrlOptions
  ): Promise<SignedUrlResult> {
    const key = `${hashPrefix}/audio.mp3`;
    return this.generateSignedUrl(key, "audio", options);
  }

  /**
   * Generate signed URL for LRC file by hash prefix
   */
  async getLrcSignedUrl(
    hashPrefix: string,
    options?: SignedUrlOptions
  ): Promise<SignedUrlResult> {
    const key = `${hashPrefix}/lyrics.lrc`;
    return this.generateSignedUrl(key, "lrc", options);
  }

  /**
   * Generate signed URL for video file by render job ID
   */
  async getVideoSignedUrl(
    renderJobId: string,
    options?: SignedUrlOptions
  ): Promise<SignedUrlResult> {
    const key = `renders/${renderJobId}/output.mp4`;
    return this.generateSignedUrl(key, "video", options);
  }

  /**
   * Generate signed URL for MP3 file by render job ID
   */
  async getRenderedAudioSignedUrl(
    renderJobId: string,
    options?: SignedUrlOptions
  ): Promise<SignedUrlResult> {
    const key = `renders/${renderJobId}/output.mp3`;
    return this.generateSignedUrl(key, "audio", options);
  }

  /**
   * Generate signed URL for chapters JSON by render job ID
   */
  async getChaptersSignedUrl(
    renderJobId: string,
    options?: SignedUrlOptions
  ): Promise<SignedUrlResult> {
    const key = `renders/${renderJobId}/chapters.json`;
    return this.generateSignedUrl(key, "json", options);
  }

  /**
   * Parse an S3-style URL into bucket and key
   */
  static parseS3Url(s3Url: string): { bucket: string; key: string } {
    if (!s3Url.startsWith("s3://")) {
      throw new Error(`Invalid S3 URL format: ${s3Url}`);
    }

    const parts = s3Url.slice(5).split("/", 1);
    if (parts.length !== 1 || !parts[0]) {
      throw new Error(`Invalid S3 URL format: ${s3Url}`);
    }

    const bucket = parts[0];
    const key = s3Url.slice(5 + bucket.length + 1);

    if (!key) {
      throw new Error(`Invalid S3 URL format: ${s3Url}`);
    }

    return { bucket, key };
  }
}

/**
 * Create R2 client from environment variables
 */
export function createR2ClientFromEnv(): R2Client {
  const endpointUrl = process.env.SOW_R2_ENDPOINT_URL;
  const accessKeyId = process.env.SOW_R2_ACCESS_KEY_ID;
  const secretAccessKey = process.env.SOW_R2_SECRET_ACCESS_KEY;
  const bucketName = process.env.SOW_R2_BUCKET;

  if (!endpointUrl || !accessKeyId || !secretAccessKey || !bucketName) {
    throw new Error(
      "R2 credentials not configured. " +
        "Set SOW_R2_ENDPOINT_URL, SOW_R2_ACCESS_KEY_ID, SOW_R2_SECRET_ACCESS_KEY, and SOW_R2_BUCKET environment variables."
    );
  }

  return new R2Client({
    endpointUrl,
    accessKeyId,
    secretAccessKey,
    bucketName,
  });
}
