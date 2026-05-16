/**
 * Asset Fetcher for downloading audio files from R2 storage.
 * 
 * Handles caching of downloaded files to avoid repeated downloads
 * during the render process.
 */

import * as fs from "fs/promises";
import * as path from "path";
import { createR2ClientFromEnv, R2Client } from "@/lib/r2/client";

export interface AssetFetcherOptions {
  cacheDir?: string;
  tempDir?: string;
  r2Client?: R2Client;
}

export interface DownloadedAsset {
  hashPrefix: string;
  localPath: string;
  contentType: string;
  sizeBytes: number;
  downloadedAt: Date;
}

/**
 * AssetFetcher downloads and caches audio files from R2 storage.
 * 
 * Features:
 * - Downloads audio files from R2 using signed URLs
 * - Caches files locally to avoid repeated downloads
 * - Manages temporary directory for render outputs
 */
export class AssetFetcher {
  private cacheDir: string;
  private tempDir: string;
  private r2Client: R2Client;
  private downloadedAssets: Map<string, DownloadedAsset> = new Map();

  constructor(options: AssetFetcherOptions = {}) {
    this.cacheDir = options.cacheDir ?? "/tmp/sow-assets/cache";
    this.tempDir = options.tempDir ?? "/tmp/sow-assets/temp";
    this.r2Client = options.r2Client ?? createR2ClientFromEnv();
  }

  /**
   * Initialize directories for caching and temp files.
   */
  async initialize(): Promise<void> {
    await fs.mkdir(this.cacheDir, { recursive: true });
    await fs.mkdir(this.tempDir, { recursive: true });
  }

  /**
   * Get the temp directory path.
   */
  async getTempDir(): Promise<string> {
    await fs.mkdir(this.tempDir, { recursive: true });
    return this.tempDir;
  }

  /**
   * Get the cache directory path.
   */
  getCacheDir(): string {
    return this.cacheDir;
  }

  /**
   * Download audio file from R2 and cache locally.
   * 
   * @param hashPrefix - Recording hash prefix
   * @returns Local path to downloaded file or null if failed
   */
  async downloadAudio(hashPrefix: string): Promise<string | null> {
    // Check if already cached
    const cached = this.downloadedAssets.get(hashPrefix);
    if (cached) {
      // Verify file still exists
      try {
        await fs.access(cached.localPath);
        return cached.localPath;
      } catch {
        // File was deleted, remove from cache
        this.downloadedAssets.delete(hashPrefix);
      }
    }

    // Check local cache directory
    const cachePath = path.join(this.cacheDir, `${hashPrefix}.mp3`);
    try {
      await fs.access(cachePath);
      // File exists in cache
      const stats = await fs.stat(cachePath);
      this.downloadedAssets.set(hashPrefix, {
        hashPrefix,
        localPath: cachePath,
        contentType: "audio/mpeg",
        sizeBytes: stats.size,
        downloadedAt: stats.mtime,
      });
      return cachePath;
    } catch {
      // File not in cache, need to download
    }

    try {
      // Generate signed URL
      const signedUrlResult = await this.r2Client.getAudioSignedUrl(hashPrefix, {
        expiresInSeconds: 3600,
      });

      // Download file
      const response = await fetch(signedUrlResult.url);
      if (!response.ok) {
        throw new Error(`Failed to download: ${response.status} ${response.statusText}`);
      }

      // Get file data
      const arrayBuffer = await response.arrayBuffer();
      const buffer = Buffer.from(arrayBuffer);

      // Ensure cache directory exists
      await fs.mkdir(this.cacheDir, { recursive: true });

      // Write to cache
      await fs.writeFile(cachePath, buffer);

      // Record in cache map
      const stats = await fs.stat(cachePath);
      this.downloadedAssets.set(hashPrefix, {
        hashPrefix,
        localPath: cachePath,
        contentType: "audio/mpeg",
        sizeBytes: stats.size,
        downloadedAt: new Date(),
      });

      return cachePath;
    } catch (error) {
      console.error(`Failed to download audio for ${hashPrefix}:`, error);
      return null;
    }
  }

  /**
   * Download LRC file from R2.
   * 
   * @param hashPrefix - Recording hash prefix
   * @returns LRC file content as string or null if failed
   */
  async downloadLrc(hashPrefix: string): Promise<string | null> {
    try {
      // Generate signed URL
      const signedUrlResult = await this.r2Client.getLrcSignedUrl(hashPrefix, {
        expiresInSeconds: 3600,
      });

      // Download file
      const response = await fetch(signedUrlResult.url);
      if (!response.ok) {
        throw new Error(`Failed to download LRC: ${response.status} ${response.statusText}`);
      }

      return await response.text();
    } catch (error) {
      console.error(`Failed to download LRC for ${hashPrefix}:`, error);
      return null;
    }
  }

  /**
   * Get information about a cached asset.
   * 
   * @param hashPrefix - Recording hash prefix
   * @returns Asset info or null if not cached
   */
  getCachedAsset(hashPrefix: string): DownloadedAsset | null {
    return this.downloadedAssets.get(hashPrefix) ?? null;
  }

  /**
   * Check if an asset is cached locally.
   * 
   * @param hashPrefix - Recording hash prefix
   * @returns True if cached
   */
  async isCached(hashPrefix: string): Promise<boolean> {
    // Check memory cache
    if (this.downloadedAssets.has(hashPrefix)) {
      return true;
    }

    // Check filesystem
    const cachePath = path.join(this.cacheDir, `${hashPrefix}.mp3`);
    try {
      await fs.access(cachePath);
      return true;
    } catch {
      return false;
    }
  }

  /**
   * Clear the memory cache (does not delete files).
   */
  clearMemoryCache(): void {
    this.downloadedAssets.clear();
  }

  /**
   * Clear the filesystem cache (deletes cached files).
   */
  async clearFileCache(): Promise<void> {
    try {
      const files = await fs.readdir(this.cacheDir);
      for (const file of files) {
        await fs.unlink(path.join(this.cacheDir, file));
      }
    } catch {
      // Ignore errors
    }
    this.downloadedAssets.clear();
  }

  /**
   * Get cache statistics.
   */
  async getCacheStats(): Promise<{
    fileCount: number;
    totalSizeBytes: number;
  }> {
    try {
      const files = await fs.readdir(this.cacheDir);
      let totalSize = 0;
      for (const file of files) {
        const stats = await fs.stat(path.join(this.cacheDir, file));
        totalSize += stats.size;
      }
      return {
        fileCount: files.length,
        totalSizeBytes: totalSize,
      };
    } catch {
      return {
        fileCount: 0,
        totalSizeBytes: 0,
      };
    }
  }

  /**
   * Clean up temporary files.
   */
  async cleanupTemp(): Promise<void> {
    try {
      const files = await fs.readdir(this.tempDir);
      for (const file of files) {
        await fs.unlink(path.join(this.tempDir, file));
      }
    } catch {
      // Ignore errors
    }
  }
}
