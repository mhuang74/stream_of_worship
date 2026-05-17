/**
 * Audio Engine for rendering songset audio with gap transitions.
 * 
 * Ports the Python AudioEngine functionality to Node.js using fluent-ffmpeg.
 * Handles audio concatenation, gap calculation (beat-based), and loudness normalization.
 */

import ffmpeg from "fluent-ffmpeg";
import ffmpegStatic from "ffmpeg-static";
import * as fs from "fs/promises";
import * as path from "path";
import { AssetFetcher } from "./asset-fetcher";

if (ffmpegStatic) {
  ffmpeg.setFfmpegPath(ffmpegStatic);
}

export interface SongsetItem {
  id: string;
  songsetId: string;
  songId: string;
  songTitle?: string | null;
  recordingHashPrefix: string | null;
  position: number;
  gapBeats: number | null;
  crossfadeEnabled: number | null;
  crossfadeDurationSeconds: number | null;
  keyShiftSemitones: number | null;
  tempoRatio: number | null;
  tempoBpm?: number | null;
  durationSeconds?: number | null;
}

export interface AudioSegmentInfo {
  item: SongsetItem;
  audioPath: string;
  startTimeSeconds: number;
  durationSeconds: number;
  gapBeforeSeconds: number;
}

export interface ExportResult {
  outputPath: string;
  totalDurationSeconds: number;
  segments: AudioSegmentInfo[];
  sampleRate: number;
  channels: number;
}

export interface AudioEngineOptions {
  targetLufs?: number;
  outputBitrate?: string;
  sampleRate?: number;
  channels?: number;
}

export type ProgressCallback = (currentStep: number, totalSteps: number) => void;

/**
 * AudioEngine handles audio processing for songset rendering.
 * 
 * Features:
 * - Gap transition calculation (beat-based)
 * - Audio concatenation with fluent-ffmpeg
 * - Loudness normalization (RMS-based approximation)
 * - Transition preview generation
 */
export class AudioEngine {
  private assetFetcher: AssetFetcher;
  private targetLufs: number;
  private outputBitrate: string;
  private sampleRate: number;
  private channels: number;
  private previewTempFiles: string[] = [];

  constructor(
    assetFetcher: AssetFetcher,
    options: AudioEngineOptions = {}
  ) {
    this.assetFetcher = assetFetcher;
    this.targetLufs = options.targetLufs ?? -14.0;
    this.outputBitrate = options.outputBitrate ?? "320k";
    this.sampleRate = options.sampleRate ?? 44100;
    this.channels = options.channels ?? 2;
  }

  /**
   * Calculate crossfade overlap in milliseconds.
   */
  private getCrossfadeMs(item: SongsetItem): number {
    if (!item.crossfadeEnabled || !item.crossfadeDurationSeconds) {
      return 0;
    }

    return Math.max(0, Math.round(item.crossfadeDurationSeconds * 1000));
  }

  /**
   * Calculate gap duration in milliseconds based on beats and tempo.
   * 
   * @param item - Songset item with gap configuration
   * @param tempoBpm - Tempo for beat-based gap calculation
   * @returns Gap duration in milliseconds
   */
  calculateGapMs(item: SongsetItem, tempoBpm?: number | null): number {
    // Crossfade - no gap, just the crossfade duration
    if (item.crossfadeEnabled && item.crossfadeDurationSeconds) {
      return 0;
    }

    // Calculate gap from beats (default 2 beats)
    const gapBeats = item.gapBeats ?? 2.0;

    if (tempoBpm && tempoBpm > 0) {
      // Convert beats to milliseconds: 60000ms / BPM = ms per beat
      const beatDurationMs = 60000.0 / tempoBpm;
      return Math.round(gapBeats * beatDurationMs);
    } else {
      // Default: 2 seconds per beat estimate
      return Math.round(gapBeats * 1000);
    }
  }

  /**
   * Get audio file information using ffprobe.
   * 
   * @param filePath - Path to audio file
   * @returns Audio metadata or null if failed
   */
  async getAudioInfo(filePath: string): Promise<{
    durationSeconds: number;
    durationMs: number;
    channels: number;
    sampleRate: number;
    bitrate: number;
    fileSizeBytes: number;
  } | null> {
    try {
      // Check file exists
      const stats = await fs.stat(filePath);
      
      return await new Promise((resolve, reject) => {
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
          const bitrate = parseInt(String(metadata.format.bit_rate ?? "0"), 10);

          resolve({
            durationSeconds,
            durationMs: Math.round(durationSeconds * 1000),
            channels: stream.channels ?? 2,
            sampleRate: stream.sample_rate ?? 44100,
            bitrate: Math.round(bitrate / 1000), // Convert to kbps
            fileSizeBytes: stats.size,
          });
        });
      });
    } catch {
      return null;
    }
  }

  /**
   * Generate combined audio for a songset with gap transitions.
   * 
   * @param items - List of songset items in order
   * @param outputPath - Path for the output audio file
   * @param progressCallback - Called with (currentStep, totalSteps) during processing
   * @param normalize - Whether to normalize loudness
   * @returns ExportResult with output information
   */
  async generateSongsetAudio(
    items: SongsetItem[],
    outputPath: string,
    progressCallback?: ProgressCallback,
    normalize: boolean = true
  ): Promise<ExportResult> {
    if (items.length === 0) {
      throw new Error("Cannot generate audio for empty songset");
    }

    const segments: AudioSegmentInfo[] = [];
    let currentTimeMs = 0;

    const totalSteps = items.length * 2; // Load + process for each item
    let currentStep = 0;

    // Download all audio files first
    const audioFiles: {
      path: string;
      item: SongsetItem;
      gapMs: number;
      crossfadeMs: number;
      durationMs: number;
      startMs: number;
    }[] = [];
    
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      
      // Update progress
      if (progressCallback) {
        progressCallback(currentStep, totalSteps);
      }
      currentStep++;

      // Download/get cached audio
      if (!item.recordingHashPrefix) {
        throw new Error(`Item ${item.id} has no recording`);
      }

      const audioPath = await this.assetFetcher.downloadAudio(item.recordingHashPrefix);
      if (!audioPath) {
        throw new Error(
          `Could not get audio for recording ${item.recordingHashPrefix}`
        );
      }

      // Calculate gap before this song
      const info = await this.getAudioInfo(audioPath);
      const durationMs = info?.durationMs ?? 0;
      let gapMs = 0;
      let crossfadeMs = 0;

      if (i > 0) {
        gapMs = this.calculateGapMs(item, item.tempoBpm);
        crossfadeMs = Math.min(this.getCrossfadeMs(item), durationMs);
      }

      const startTimeMs = i === 0 ? 0 : Math.max(0, currentTimeMs + gapMs - crossfadeMs);

      // Record segment info
      const segmentInfo: AudioSegmentInfo = {
        item,
        audioPath,
        startTimeSeconds: startTimeMs / 1000.0,
        durationSeconds: durationMs / 1000.0,
        gapBeforeSeconds: gapMs / 1000.0,
      };
      segments.push(segmentInfo);

      currentTimeMs = startTimeMs + durationMs;
      audioFiles.push({
        path: audioPath,
        item,
        gapMs,
        crossfadeMs,
        durationMs,
        startMs: startTimeMs,
      });

      // Update progress
      if (progressCallback) {
        progressCallback(currentStep, totalSteps);
      }
      currentStep++;
    }

    // Ensure output directory exists
    await fs.mkdir(path.dirname(outputPath), { recursive: true });

    // Build FFmpeg command for concatenation
    await this.concatenateAudioFiles(audioFiles, outputPath, normalize);

    // Final progress update
    if (progressCallback) {
      progressCallback(totalSteps, totalSteps);
    }

    return {
      outputPath,
      totalDurationSeconds: currentTimeMs / 1000.0,
      segments,
      sampleRate: this.sampleRate,
      channels: this.channels,
    };
  }

  /**
   * Concatenate audio files with gaps using FFmpeg.
   * 
   * @param audioFiles - Array of audio file paths with gaps
   * @param outputPath - Output file path
   * @param normalize - Whether to apply loudness normalization
   */
  private async concatenateAudioFiles(
    audioFiles: {
      path: string;
      item: SongsetItem;
      gapMs: number;
      crossfadeMs: number;
      durationMs: number;
      startMs: number;
    }[],
    outputPath: string,
    normalize: boolean
  ): Promise<void> {
    return new Promise((resolve, reject) => {
      const command = ffmpeg();
      const filterParts: string[] = [];
      const outputLabels: string[] = [];

      for (const audioFile of audioFiles) {
        command.input(audioFile.path);
      }

      for (let i = 0; i < audioFiles.length; i++) {
        const audioFile = audioFiles[i];
        const nextCrossfadeMs = audioFiles[i + 1]?.crossfadeMs ?? 0;
        const filters = [`[${i}:a]asetpts=PTS-STARTPTS`];

        if (audioFile.crossfadeMs > 0) {
          filters.push(
            `afade=t=in:st=0:d=${(audioFile.crossfadeMs / 1000).toFixed(3)}`
          );
        }

        if (nextCrossfadeMs > 0) {
          const fadeOutStartSeconds = Math.max(
            0,
            (audioFile.durationMs - nextCrossfadeMs) / 1000
          );
          filters.push(
            `afade=t=out:st=${fadeOutStartSeconds.toFixed(3)}:d=${(
              nextCrossfadeMs / 1000
            ).toFixed(3)}`
          );
        }

        if (audioFile.startMs > 0) {
          const delay = Math.round(audioFile.startMs);
          filters.push(`adelay=${delay}|${delay}`);
        }

        const outputLabel = `a${i}`;
        filterParts.push(`${filters.join(",")}[${outputLabel}]`);
        outputLabels.push(`[${outputLabel}]`);
      }

      filterParts.push(
        `${outputLabels.join("")}amix=inputs=${outputLabels.length}:normalize=0:dropout_transition=0[outa]`
      );

      command.complexFilter(filterParts, "[outa]");

      // Apply loudness normalization if requested
      if (normalize) {
        // Use loudnorm filter for EBU R128 loudness normalization
        // target_offset is the target integrated loudness (-14 LUFS is common for streaming)
        command.audioFilters([
          `loudnorm=I=${this.targetLufs}:TP=-1.5:LRA=11`,
        ]);
      }

      command
        .audioCodec("libmp3lame")
        .audioBitrate(this.outputBitrate)
        .audioFrequency(this.sampleRate)
        .audioChannels(this.channels)
        .output(outputPath)
        .on("end", () => resolve())
        .on("error", (err) => reject(err))
        .run();
    });
  }

  /**
   * Generate a preview of a transition between two songs.
   * 
   * @param fromItem - First song item
   * @param toItem - Second song item
   * @param previewDurationSeconds - Duration of the preview clip
   * @returns Path to preview audio file or null if generation failed
   */
  async previewTransition(
    fromItem: SongsetItem,
    toItem: SongsetItem,
    previewDurationSeconds: number = 15.0
  ): Promise<string | null> {
    if (!fromItem.recordingHashPrefix || !toItem.recordingHashPrefix) {
      return null;
    }

    // Clean up previous preview temp files
    await this.cleanupPreviewFiles();

    try {
      // Download audio files
      const fromPath = await this.assetFetcher.downloadAudio(
        fromItem.recordingHashPrefix
      );
      const toPath = await this.assetFetcher.downloadAudio(
        toItem.recordingHashPrefix
      );

      if (!fromPath || !toPath) {
        return null;
      }

      // Get audio info
      const fromInfo = await this.getAudioInfo(fromPath);
      const toInfo = await this.getAudioInfo(toPath);

      if (!fromInfo || !toInfo) {
        return null;
      }

      // Calculate clip durations
      const halfDurationMs = (previewDurationSeconds * 1000) / 2;
      
      // Extract end of first song
      const fromDurationMs = Math.min(halfDurationMs, fromInfo.durationMs);
      const fromStartMs = Math.max(0, fromInfo.durationMs - fromDurationMs);

      // Extract start of second song
      const toDurationMs = Math.min(halfDurationMs, toInfo.durationMs);

      // Calculate gap / overlap
      const gapMs = this.calculateGapMs(toItem, toItem.tempoBpm);
      const crossfadeMs = Math.min(this.getCrossfadeMs(toItem), fromDurationMs, toDurationMs);

      // Create temp file for preview
      const tempFile = path.join(
        await this.assetFetcher.getTempDir(),
        `preview-${Date.now()}.mp3`
      );
      this.previewTempFiles.push(tempFile);

      // Build FFmpeg command for preview
      await new Promise<void>((resolve, reject) => {
        const command = ffmpeg();

        // Add inputs
        command.input(fromPath);
        command.input(toPath);

        // Build filter complex
        const filters: string[] = [];

        // Trim end of first song
        const fromStartSeconds = fromStartMs / 1000;
        const fromDurationSeconds = fromDurationMs / 1000;
        filters.push(
          `[0:a]atrim=start=${fromStartSeconds}:duration=${fromDurationSeconds},asetpts=PTS-STARTPTS[from]`
        );

        // Trim start of second song
        const toDurationSec = toDurationMs / 1000;
        filters.push(
          `[1:a]atrim=start=0:duration=${toDurationSec},asetpts=PTS-STARTPTS[to]`
        );

        if (crossfadeMs > 0) {
          const overlapStartSeconds = Math.max(
            0,
            (fromDurationMs - crossfadeMs) / 1000
          );
          filters.push(
            `[from]afade=t=out:st=${overlapStartSeconds.toFixed(3)}:d=${(
              crossfadeMs / 1000
            ).toFixed(3)}[fromf]`
          );
          filters.push(
            `[to]afade=t=in:st=0:d=${(crossfadeMs / 1000).toFixed(3)},adelay=${Math.max(
              0,
              Math.round(fromDurationMs - crossfadeMs)
            )}|${Math.max(0, Math.round(fromDurationMs - crossfadeMs))}[tof]`
          );
          filters.push("[fromf][tof]amix=inputs=2:normalize=0:dropout_transition=0[outa]");
        } else if (gapMs > 0) {
          const gapSeconds = gapMs / 1000;
          filters.push(
            `aevalsrc=0:d=${gapSeconds}[gap]`
          );
          filters.push("[from][gap][to]concat=n=3:v=0:a=1[outa]");
        } else {
          filters.push("[from][to]concat=n=2:v=0:a=1[outa]");
        }

        command.complexFilter(filters, "[outa]");

        command
          .audioCodec("libmp3lame")
          .audioBitrate("192k")
          .audioFrequency(this.sampleRate)
          .audioChannels(this.channels)
          .output(tempFile)
          .on("end", () => resolve())
          .on("error", (err) => reject(err))
          .run();
      });

      return tempFile;
    } catch {
      return null;
    }
  }

  /**
   * Clean up temporary preview files.
   */
  async cleanupPreviewFiles(): Promise<void> {
    for (const file of this.previewTempFiles) {
      try {
        await fs.unlink(file);
      } catch {
        // Ignore errors
      }
    }
    this.previewTempFiles = [];
  }

  /**
   * Calculate the total duration of a songset including gaps.
   * 
   * @param items - List of songset items
   * @returns Total duration in seconds
   */
  async calculateTotalDuration(items: SongsetItem[]): Promise<number> {
    let totalMs = 0;

    for (let i = 0; i < items.length; i++) {
      const item = items[i];

      // Add gap (except for first song)
      if (i > 0) {
        const gapMs = this.calculateGapMs(item, item.tempoBpm);
        totalMs += gapMs;
        totalMs -= this.getCrossfadeMs(item);
      }

      // Add song duration if available
      if (item.durationSeconds) {
        totalMs += item.durationSeconds * 1000;
      } else if (item.recordingHashPrefix) {
        // Try to get from audio file
        const audioPath = await this.assetFetcher.downloadAudio(
          item.recordingHashPrefix
        );
        if (audioPath) {
          const info = await this.getAudioInfo(audioPath);
          if (info) {
            totalMs += info.durationMs;
          }
        }
      }
    }

    return totalMs / 1000.0;
  }
}
