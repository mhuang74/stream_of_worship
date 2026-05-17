/**
 * Uploader module for uploading render artifacts to R2 storage.
 *
 * Handles uploading MP3, MP4, and chapters.json files to Cloudflare R2
 * after rendering is complete.
 */

import * as fs from "fs/promises";
import * as path from "path";
import {
  S3Client,
  PutObjectCommand,
  HeadObjectCommand,
} from "@aws-sdk/client-s3";
import { R2Client, R2Config, createR2ClientFromEnv } from "@/lib/r2/client";
import { ChaptersManifest } from "./chapters";

export interface UploadOptions {
  contentType?: string;
  cacheControl?: string;
  metadata?: Record<string, string>;
}

export interface UploadResult {
  key: string;
  sizeBytes: number;
  etag: string | undefined;
  uploadedAt: Date;
}

export interface RenderArtifacts {
  mp3Path?: string;
  mp4Path?: string;
  chapters?: ChaptersManifest;
}

export interface UploadArtifactsResult {
  mp3R2Key?: string;
  mp4R2Key?: string;
  chaptersR2Key?: string;
  uploadedAt: Date;
}

export type UploadProgressCallback = (
  fileType: "mp3" | "mp4" | "chapters",
  bytesUploaded: number,
  totalBytes: number
) => void;

/**
 * R2Uploader handles uploading render artifacts to Cloudflare R2 storage.
 */
export class R2Uploader {
  private client: S3Client;
  private bucketName: string;

  constructor(config?: R2Config) {
    const r2Client = config ? new R2Client(config) : createR2ClientFromEnv();
    this.client = (r2Client as unknown as { client: S3Client }).client;
    this.bucketName = (r2Client as unknown as { bucketName: string }).bucketName;
  }

  /**
   * Upload a file to R2.
   *
   * @param key - R2 object key
   * @param filePath - Local file path
   * @param options - Upload options
   * @returns Upload result
   */
  async uploadFile(
    key: string,
    filePath: string,
    options: UploadOptions = {}
  ): Promise<UploadResult> {
    const fileBuffer = await fs.readFile(filePath);
    const stats = await fs.stat(filePath);
    const result = await this.putObject(key, fileBuffer, stats.size, options);
    return { ...result, sizeBytes: stats.size };
  }

  async uploadBuffer(
    key: string,
    buffer: Buffer,
    options: UploadOptions = {}
  ): Promise<UploadResult> {
    return this.putObject(key, buffer, buffer.length, options);
  }

  private async putObject(
    key: string,
    body: Buffer,
    sizeBytes: number,
    options: UploadOptions
  ): Promise<UploadResult> {
    const contentType =
      options.contentType ?? this.inferContentType(key);
    const cacheControl =
      options.cacheControl ?? "public, max-age=3600";

    const command = new PutObjectCommand({
      Bucket: this.bucketName,
      Key: key,
      Body: body,
      ContentType: contentType,
      CacheControl: cacheControl,
      Metadata: options.metadata,
    });

    const result = await this.client.send(command);

    return {
      key,
      sizeBytes,
      etag: result.ETag,
      uploadedAt: new Date(),
    };
  }

  /**
   * Upload render artifacts for a job.
   *
   * @param renderJobId - Render job ID
   * @param artifacts - Render artifacts to upload
   * @param progressCallback - Called with upload progress
   * @returns Upload result with R2 keys
   */
  async uploadRenderArtifacts(
    renderJobId: string,
    artifacts: RenderArtifacts,
    progressCallback?: UploadProgressCallback
  ): Promise<UploadArtifactsResult> {
    const result: UploadArtifactsResult = {
      uploadedAt: new Date(),
    };

    // Upload MP3 if present
    if (artifacts.mp3Path) {
      const key = `renders/${renderJobId}/output.mp3`;
      const stats = await fs.stat(artifacts.mp3Path);

      if (progressCallback) {
        progressCallback("mp3", 0, stats.size);
      }

      const uploadResult = await this.uploadFile(key, artifacts.mp3Path, {
        contentType: "audio/mpeg",
        cacheControl: "public, max-age=3600",
        metadata: {
          "render-job-id": renderJobId,
          "content-type": "audio",
        },
      });

      if (progressCallback) {
        progressCallback("mp3", uploadResult.sizeBytes, uploadResult.sizeBytes);
      }

      result.mp3R2Key = key;
    }

    // Upload MP4 if present
    if (artifacts.mp4Path) {
      const key = `renders/${renderJobId}/output.mp4`;
      const stats = await fs.stat(artifacts.mp4Path);

      if (progressCallback) {
        progressCallback("mp4", 0, stats.size);
      }

      const uploadResult = await this.uploadFile(key, artifacts.mp4Path, {
        contentType: "video/mp4",
        cacheControl: "public, max-age=3600",
        metadata: {
          "render-job-id": renderJobId,
          "content-type": "video",
        },
      });

      if (progressCallback) {
        progressCallback("mp4", uploadResult.sizeBytes, uploadResult.sizeBytes);
      }

      result.mp4R2Key = key;
    }

    // Upload chapters if present
    if (artifacts.chapters) {
      const key = `renders/${renderJobId}/chapters.json`;
      const jsonContent = JSON.stringify(artifacts.chapters, null, 2);
      const buffer = Buffer.from(jsonContent, "utf-8");

      if (progressCallback) {
        progressCallback("chapters", 0, buffer.length);
      }

      const uploadResult = await this.uploadBuffer(key, buffer, {
        contentType: "application/json",
        cacheControl: "public, max-age=3600",
        metadata: {
          "render-job-id": renderJobId,
          "content-type": "chapters",
        },
      });

      if (progressCallback) {
        progressCallback("chapters", uploadResult.sizeBytes, uploadResult.sizeBytes);
      }

      result.chaptersR2Key = key;
    }

    return result;
  }

  async fileExists(key: string): Promise<boolean> {
    try {
      const command = new HeadObjectCommand({
        Bucket: this.bucketName,
        Key: key,
      });
      await this.client.send(command);
      return true;
    } catch (error) {
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

  async deleteFile(key: string): Promise<void> {
    const { DeleteObjectCommand } = await import("@aws-sdk/client-s3");
    const command = new DeleteObjectCommand({
      Bucket: this.bucketName,
      Key: key,
    });
    await this.client.send(command);
  }

  async deleteRenderArtifacts(renderJobId: string): Promise<void> {
    const keys = [
      `renders/${renderJobId}/output.mp3`,
      `renders/${renderJobId}/output.mp4`,
      `renders/${renderJobId}/chapters.json`,
    ];

    for (const key of keys) {
      try {
        if (await this.fileExists(key)) {
          await this.deleteFile(key);
        }
      } catch (error) {
        console.warn(`Failed to delete ${key}:`, error);
      }
    }
  }

  private inferContentType(key: string): string {
    const ext = path.extname(key).toLowerCase();
    const contentTypes: Record<string, string> = {
      ".mp3": "audio/mpeg",
      ".mp4": "video/mp4",
      ".json": "application/json",
      ".lrc": "text/plain; charset=utf-8",
      ".txt": "text/plain",
      ".jpg": "image/jpeg",
      ".jpeg": "image/jpeg",
      ".png": "image/png",
      ".gif": "image/gif",
      ".webp": "image/webp",
    };
    return contentTypes[ext] ?? "application/octet-stream";
  }
}
