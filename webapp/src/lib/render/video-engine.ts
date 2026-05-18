/**
 * Video Engine for generating lyrics videos.
 *
 * Generates MP4 videos with synchronized lyrics using frame rendering
 * and FFmpeg encoding. Supports multiple templates and resolutions.
 */

import * as fs from "fs/promises";
import { existsSync } from "fs";
import * as path from "path";
import ffmpeg from "fluent-ffmpeg";
import ffmpegStatic from "ffmpeg-static";
import { spawn } from "child_process";
import { AssetFetcher } from "./asset-fetcher";
import {
  FrameRenderer,
  VideoTemplate,
  VideoTemplateName,
  FontSizePreset,
  SegmentInfo,
} from "./frame-renderer";
import {
  parseLRC,
  GlobalLRCLine,
  convertToGlobalTimeline,
} from "./lrc-parser";
import { chaptersToFFmpegMetadata, ChaptersManifest } from "./chapters";
import { AudioSegmentInfo } from "./audio-engine";

export interface VideoEngineOptions {
  template?: VideoTemplateName;
  fontSizePreset?: FontSizePreset;
  resolution?: "720p" | "1080p";
  fps?: number;
  includeTitleCard?: boolean;
  titleCardDurationSeconds?: number;
  ffmpegPath?: string;
  ffprobePath?: string;
}

export interface VideoExportResult {
  outputPath: string;
  totalFrames: number;
  durationSeconds: number;
  width: number;
  height: number;
  fps: number;
}

export interface ChapterInfo {
  position: number;
  songTitle: string;
  startSeconds: number;
  endSeconds: number;
  lines: Array<{
    text: string;
    startSeconds: number;
  }>;
}

export type ProgressCallback = (currentFrame: number, totalFrames: number) => void;

/**
 * VideoEngine generates lyrics videos synchronized with audio.
 */
export class VideoEngine {
  private assetFetcher: AssetFetcher;
  private template: VideoTemplate;
  private fontSizePreset: FontSizePreset;
  private resolution: { width: number; height: number };
  private fps: number;
  private includeTitleCard: boolean;
  private titleCardDurationSeconds: number;
  private ffmpegPath: string;
  private ffprobePath: string;
  private frameRenderer: FrameRenderer;

  constructor(
    assetFetcher: AssetFetcher,
    options: VideoEngineOptions = {}
  ) {
    this.assetFetcher = assetFetcher;
    this.template = FrameRenderer.getTemplate(options.template ?? "dark");
    this.fontSizePreset = options.fontSizePreset ?? "M";
    this.resolution =
      options.resolution === "720p"
        ? { width: 1280, height: 720 }
        : { width: 1920, height: 1080 };
    this.fps = options.fps ?? 24;
    this.includeTitleCard = options.includeTitleCard ?? true;
    this.titleCardDurationSeconds = Math.min(
      Math.max(options.titleCardDurationSeconds ?? 5, 5),
      30
    );
    this.ffmpegPath = options.ffmpegPath ?? (ffmpegStatic && existsSync(ffmpegStatic) ? ffmpegStatic : "ffmpeg");
    this.ffprobePath = options.ffprobePath ?? "ffprobe";

    this.frameRenderer = new FrameRenderer({
      template: this.template,
      fontSizePreset: this.fontSizePreset,
      resolution: this.resolution,
    });
  }

  /**
   * Initialize the video engine.
   */
  async initialize(): Promise<void> {
    await this.frameRenderer.initialize();
  }

  /**
   * Generate a lyrics video synchronized with audio.
   *
   * @param audioPath - Path to the mixed audio file
   * @param segments - Audio segment information with song metadata
   * @param outputPath - Path for output video
   * @param progressCallback - Called with (currentFrame, totalFrames)
   * @returns VideoExportResult with output information
   */
  async generateVideo(
    audioPath: string,
    segments: AudioSegmentInfo[],
    outputPath: string,
    progressCallback?: ProgressCallback
  ): Promise<VideoExportResult> {
    // Ensure output directory exists
    await fs.mkdir(path.dirname(outputPath), { recursive: true });

    // Get audio duration
    const audioInfo = await this.getAudioInfo(audioPath);
    if (!audioInfo) {
      throw new Error("Could not get audio info");
    }

    const totalDurationSeconds = audioInfo.durationSeconds;
    const titleCardFrames = this.includeTitleCard
      ? Math.ceil(this.titleCardDurationSeconds * this.fps)
      : 0;
    const totalFrames = Math.ceil(totalDurationSeconds * this.fps) + titleCardFrames;

    // Collect all lyrics with global timing
    const allLyrics: GlobalLRCLine[] = [];
    const chapters: ChapterInfo[] = [];

    for (let i = 0; i < segments.length; i++) {
      const segment = segments[i];
      const hashPrefix = segment.item.recordingHashPrefix;

      if (!hashPrefix) {
        continue;
      }

      // Download and parse LRC
      const lrcContent = await this.assetFetcher.downloadLrc(hashPrefix);
      if (!lrcContent) {
        console.warn(`No LRC found for ${hashPrefix}`);
        continue;
      }

      const localLyrics = parseLRC(lrcContent);
      const globalLyrics = convertToGlobalTimeline(
        localLyrics,
        segment.startTimeSeconds,
        segment.item.songTitle ?? segment.item.songId?.toString() ?? `song-${i}`
      );

      allLyrics.push(...globalLyrics);

      // Build chapter info
      const segmentEnd = segment.startTimeSeconds + segment.durationSeconds;
      chapters.push({
        position: i + 1,
        songTitle: segment.item.songTitle ?? segment.item.songId?.toString() ?? `Song ${i + 1}`,
        startSeconds: segment.startTimeSeconds,
        endSeconds: segmentEnd,
        lines: localLyrics.map((line) => ({
          text: line.text,
          startSeconds: segment.startTimeSeconds + line.timeSeconds,
        })),
      });
    }

    if (allLyrics.length === 0) {
      // Generate blank video if no lyrics
      return this.generateBlankVideo(audioPath, outputPath, totalDurationSeconds);
    }

    // Convert segments to SegmentInfo format
    const segmentInfos: SegmentInfo[] = segments.map((seg, i) => ({
      id: seg.item.id,
      songId: seg.item.songId,
      position: seg.item.position,
      songTitle: seg.item.songTitle ?? seg.item.songId?.toString() ?? `Song ${i + 1}`,
      startTimeSeconds: seg.startTimeSeconds,
      durationSeconds: seg.durationSeconds,
      tempoBpm: seg.item.tempoBpm,
    }));

    const titleCardConfig = this.includeTitleCard
      ? {
          frames: titleCardFrames,
          songsetName: segments[0]?.item.songTitle ?? "Worship Set",
          songCount: segments.length,
          totalDurationSeconds,
        }
      : undefined;

    // Generate video with FFmpeg
    await this.encodeVideoWithFFmpeg(
      audioPath,
      outputPath,
      totalFrames,
      totalDurationSeconds,
      allLyrics,
      segmentInfos,
      progressCallback,
      titleCardConfig
    );

    return {
      outputPath,
      totalFrames,
      durationSeconds: totalDurationSeconds,
      width: this.resolution.width,
      height: this.resolution.height,
      fps: this.fps,
    };
  }

  /**
   * Encode video using FFmpeg with raw video input.
   */
  private async encodeVideoWithFFmpeg(
    audioPath: string,
    outputPath: string,
    totalFrames: number,
    totalDurationSeconds: number,
    lyrics: GlobalLRCLine[],
    segments: SegmentInfo[],
    progressCallback?: ProgressCallback,
    titleCardConfig?: {
      frames: number;
      songsetName: string;
      songCount: number;
      totalDurationSeconds: number;
    }
  ): Promise<void> {
    const { width, height } = this.resolution;

    // Build FFmpeg command
    const args = [
      "-y", // Overwrite output
      "-f",
      "rawvideo",
      "-vcodec",
      "rawvideo",
      "-s",
      `${width}x${height}`,
      "-pix_fmt",
      "rgba",
      "-r",
      String(this.fps),
      "-i",
      "-", // Read from stdin
      "-i",
      audioPath,
      ...this.getVideoCodecArgs(),
      "-c:a",
      "aac",
      "-b:a",
      "192k",
      "-shortest",
      outputPath,
    ];

    return new Promise((resolve, reject) => {
      const process = spawn(this.ffmpegPath, args, {
        stdio: ["pipe", "ignore", "ignore"],
      });

      let frameCount = 0;
      let isProcessing = true;

      const writeFrame = async () => {
        if (!isProcessing) return;

        try {
          let buffer: Buffer;

          if (titleCardConfig && frameCount < titleCardConfig.frames) {
            const canvas = this.frameRenderer.renderTitleCard({
              enabled: true,
              durationSeconds: titleCardConfig.totalDurationSeconds,
              songsetName: titleCardConfig.songsetName,
              songCount: titleCardConfig.songCount,
              totalDurationSeconds: titleCardConfig.totalDurationSeconds,
            });
            buffer = this.frameRenderer.canvasToBuffer(canvas);
          } else {
            const lyricsFrameIndex = titleCardConfig ? frameCount - titleCardConfig.frames : frameCount;
            const currentTime = lyricsFrameIndex / this.fps;

            const canvas = this.frameRenderer.renderFrame(
              lyrics,
              segments,
              currentTime
            );
            buffer = this.frameRenderer.canvasToBuffer(canvas);
          }

          if (process.stdin && process.stdin.writable) {
            const canContinue = process.stdin.write(buffer, (err) => {
              if (err) {
                isProcessing = false;
                reject(err);
                return;
              }

              frameCount++;

              if (progressCallback && frameCount % this.fps === 0) {
                progressCallback(frameCount, totalFrames);
              }

              if (frameCount >= totalFrames) {
                if (process.stdin) {
                  process.stdin.end();
                }
              }
            });

            if (!canContinue && isProcessing && frameCount < totalFrames) {
              await new Promise<void>((resolve) => {
                process.stdin!.once("drain", resolve);
              });
            }

            if (frameCount < totalFrames && isProcessing && canContinue) {
              setImmediate(writeFrame);
            } else if (frameCount < totalFrames && isProcessing) {
              setImmediate(writeFrame);
            }
          }
        } catch (error) {
          isProcessing = false;
          reject(error);
        }
      };

      process.on("error", (err) => {
        isProcessing = false;
        reject(err);
      });

      process.on("exit", (code) => {
        isProcessing = false;
        if (code === 0) {
          if (progressCallback) {
            progressCallback(totalFrames, totalFrames);
          }
          resolve();
        } else {
          reject(new Error(`FFmpeg exited with code ${code}`));
        }
      });

      // Start writing frames
      writeFrame();
    });
  }

  /**
   * Generate a blank video with just the background color.
   */
  private async generateBlankVideo(
    audioPath: string,
    outputPath: string,
    durationSeconds: number
  ): Promise<VideoExportResult> {
    const { width, height } = this.resolution;
    const [bgR, bgG, bgB] = this.template.backgroundColor;

    const args = [
      "-y",
      "-f",
      "lavfi",
      "-i",
      `color=c=#${bgR.toString(16).padStart(2, "0")}${bgG.toString(16).padStart(2, "0")}${bgB.toString(16).padStart(2, "0")}:s=${width}x${height}:d=${durationSeconds}`,
      "-i",
      audioPath,
      ...this.getVideoCodecArgs("5000k"),
      "-c:a",
      "aac",
      "-b:a",
      "192k",
      "-shortest",
      outputPath,
    ];

    await new Promise<void>((resolve, reject) => {
      const process = spawn(this.ffmpegPath, args);

      process.on("error", reject);
      process.on("exit", (code) => {
        if (code === 0) {
          resolve();
        } else {
          reject(new Error(`FFmpeg exited with code ${code}`));
        }
      });
    });

    return {
      outputPath,
      totalFrames: Math.ceil(durationSeconds * this.fps),
      durationSeconds,
      width,
      height,
      fps: this.fps,
    };
  }

  /**
   * Get platform-appropriate video codec arguments.
   */
  private getVideoCodecArgs(bitrate: string = "8000k"): string[] {
    // Use libx264 with ultrafast preset for best compatibility
    return [
      "-c:v",
      "libx264",
      "-preset",
      "ultrafast",
      "-crf",
      "23",
      "-b:v",
      bitrate,
    ];
  }

  /**
   * Get audio file information using ffprobe.
   */
  private async getAudioInfo(
    filePath: string
  ): Promise<{
    durationSeconds: number;
    durationMs: number;
    sampleRate: number;
    channels: number;
  } | null> {
    return new Promise((resolve, reject) => {
      ffmpeg.ffprobe(filePath, (err, metadata) => {
        if (err) {
          reject(err);
          return;
        }

        if (!metadata.streams || metadata.streams.length === 0) {
          reject(new Error("No audio streams found"));
          return;
        }

        const stream = metadata.streams[0];
        const durationSeconds = metadata.format.duration ?? 0;

        resolve({
          durationSeconds,
          durationMs: Math.round(durationSeconds * 1000),
          sampleRate: stream.sample_rate ?? 44100,
          channels: stream.channels ?? 2,
        });
      });
    });
  }

  /**
   * Inject chapter atoms into MP4 file.
   * Best-effort: proceeds on failure.
   *
   * @param videoPath - Path to MP4 file
   * @param chapters - Chapter information
   * @returns True if successful
   */
  async injectChapters(
    videoPath: string,
    chapters: ChapterInfo[]
  ): Promise<boolean> {
    try {
      // Create temporary file for chapters
      const tempDir = await this.assetFetcher.getTempDir();
      const chaptersPath = path.join(tempDir, `chapters-${Date.now()}.txt`);

      // Write chapters in FFmpeg metadata format
      const manifest: ChaptersManifest = {
        chapters,
        totalDurationSeconds: 0,
        generatedAt: new Date().toISOString(),
      };
      const chaptersContent = chaptersToFFmpegMetadata(manifest);
      await fs.writeFile(chaptersPath, chaptersContent, "utf-8");

      // Create output path
      const outputPath = `${videoPath}.chapters.mp4`;

      // Run FFmpeg to inject chapters
      const args = [
        "-y",
        "-i",
        videoPath,
        "-i",
        chaptersPath,
        "-map_metadata",
        "1",
        "-c",
        "copy",
        outputPath,
      ];

      await new Promise<void>((resolve, reject) => {
        const process = spawn(this.ffmpegPath, args);
        process.on("error", reject);
        process.on("exit", (code) => {
          if (code === 0) {
            resolve();
          } else {
            reject(new Error(`FFmpeg exited with code ${code}`));
          }
        });
      });

      // Replace original file with chapter-injected version
      await fs.rename(outputPath, videoPath);

      // Clean up temp file
      await fs.unlink(chaptersPath).catch(() => {});

      return true;
    } catch (error) {
      console.warn("Failed to inject chapters (proceeding anyway):", error);
      return false;
    }
  }

}
