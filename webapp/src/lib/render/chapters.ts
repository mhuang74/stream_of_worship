/**
 * Chapters module for generating chapter manifests.
 *
 * Creates chapters.json files that map song positions, titles, timing,
 * and lyrics for navigation during playback.
 */

import { AudioSegmentInfo } from "./audio-engine";
import { AssetFetcher } from "./asset-fetcher";
import { parseLRC, LRCLine } from "./lrc-parser";

export interface ChapterLine {
  text: string;
  startSeconds: number;
}

export interface Chapter {
  position: number;
  songTitle: string;
  startSeconds: number;
  endSeconds: number;
  lines: ChapterLine[];
}

export interface ChaptersManifest {
  chapters: Chapter[];
  totalDurationSeconds: number;
  generatedAt: string;
}

export interface ChapterGenerationOptions {
  includeEmptyChapters?: boolean;
}

function buildChaptersFromSegments(
  segments: AudioSegmentInfo[],
  getLyrics: (hashPrefix: string, startSeconds: number) => ChapterLine[] | Promise<ChapterLine[]>
): Promise<Chapter[]> {
  const promises = segments.map(async (segment, i) => {
    const startSeconds = segment.startTimeSeconds;
    const endSeconds = startSeconds + segment.durationSeconds;
    const songTitle = segment.item.songTitle ?? segment.item.songId ?? `Song ${i + 1}`;

    const hashPrefix = segment.item.recordingHashPrefix;
    const lines = hashPrefix
      ? await getLyrics(hashPrefix, startSeconds)
      : [];

    return {
      position: i + 1,
      songTitle,
      startSeconds,
      endSeconds,
      lines,
    };
  });
  return Promise.all(promises);
}

export async function generateChaptersManifest(
  segments: AudioSegmentInfo[],
  assetFetcher: AssetFetcher,
  totalDurationSeconds: number
): Promise<ChaptersManifest> {
  const chapters = await buildChaptersFromSegments(
    segments,
    async (hashPrefix, startSeconds) => {
      try {
        const lrcContent = await assetFetcher.downloadLrc(hashPrefix);
        if (lrcContent) {
          const localLyrics = parseLRC(lrcContent);
          return localLyrics.map((line) => ({
            text: line.text,
            startSeconds: startSeconds + line.timeSeconds,
          }));
        }
      } catch (error) {
        console.warn(`Failed to load LRC for chapter:`, error);
      }
      return [];
    }
  );

  return {
    chapters,
    totalDurationSeconds,
    generatedAt: new Date().toISOString(),
  };
}

export function generateChaptersManifestFromLyrics(
  segments: AudioSegmentInfo[],
  lyricsMap: Map<string, LRCLine[]>,
  totalDurationSeconds: number
): ChaptersManifest {
  const chapters = segments.map((segment, i) => {
    const startSeconds = segment.startTimeSeconds;
    const endSeconds = startSeconds + segment.durationSeconds;
    const songTitle = segment.item.songTitle ?? segment.item.songId ?? `Song ${i + 1}`;

    const hashPrefix = segment.item.recordingHashPrefix;
    let lines: ChapterLine[] = [];

    if (hashPrefix && lyricsMap.has(hashPrefix)) {
      const localLyrics = lyricsMap.get(hashPrefix)!;
      lines = localLyrics.map((line) => ({
        text: line.text,
        startSeconds: startSeconds + line.timeSeconds,
      }));
    }

    return {
      position: i + 1,
      songTitle,
      startSeconds,
      endSeconds,
      lines,
    };
  });

  return {
    chapters,
    totalDurationSeconds,
    generatedAt: new Date().toISOString(),
  };
}

/**
 * Convert chapters manifest to FFmpeg metadata format.
 * Used for injecting chapters into MP4 files.
 *
 * @param manifest - Chapters manifest
 * @returns FFmpeg metadata string
 */
export function chaptersToFFmpegMetadata(manifest: ChaptersManifest): string {
  const lines: string[] = [";FFMETADATA1"];

  for (const chapter of manifest.chapters) {
    lines.push("[CHAPTER]");
    lines.push("TIMEBASE=1/1000");
    lines.push(`START=${Math.floor(chapter.startSeconds * 1000)}`);
    lines.push(`END=${Math.floor(chapter.endSeconds * 1000)}`);
    lines.push(`title=${chapter.songTitle}`);
  }

  return lines.join("\n");
}

/**
 * Find the chapter containing a specific time position.
 *
 * @param manifest - Chapters manifest
 * @param positionSeconds - Time position in seconds
 * @returns Chapter index or -1 if not found
 */
export function findChapterAtTime(
  manifest: ChaptersManifest,
  positionSeconds: number
): number {
  for (let i = 0; i < manifest.chapters.length; i++) {
    const chapter = manifest.chapters[i];
    if (
      positionSeconds >= chapter.startSeconds &&
      positionSeconds < chapter.endSeconds
    ) {
      return i;
    }
  }

  // Check if at the exact end of the last chapter
  if (manifest.chapters.length > 0) {
    const lastChapter = manifest.chapters[manifest.chapters.length - 1];
    if (positionSeconds === lastChapter.endSeconds) {
      return manifest.chapters.length - 1;
    }
  }

  return -1;
}

/**
 * Get the current song title at a specific time position.
 *
 * @param manifest - Chapters manifest
 * @param positionSeconds - Time position in seconds
 * @returns Song title or null if between chapters
 */
export function getSongTitleAtTime(
  manifest: ChaptersManifest,
  positionSeconds: number
): string | null {
  const chapterIndex = findChapterAtTime(manifest, positionSeconds);
  if (chapterIndex >= 0) {
    return manifest.chapters[chapterIndex].songTitle;
  }
  return null;
}

/**
 * Get the current lyric line at a specific time position.
 *
 * @param manifest - Chapters manifest
 * @param positionSeconds - Time position in seconds
 * @returns Lyric line text or null if not found
 */
export function getLyricAtTime(
  manifest: ChaptersManifest,
  positionSeconds: number
): string | null {
  const chapterIndex = findChapterAtTime(manifest, positionSeconds);
  if (chapterIndex < 0) {
    return null;
  }

  const chapter = manifest.chapters[chapterIndex];

  // Find the current line within the chapter
  for (let i = chapter.lines.length - 1; i >= 0; i--) {
    const line = chapter.lines[i];
    if (positionSeconds >= line.startSeconds) {
      return line.text;
    }
  }

  return null;
}

export function parseChaptersManifest(json: string): ChaptersManifest {
  const parsed = JSON.parse(json) as ChaptersManifest;

  // Validate structure
  if (!Array.isArray(parsed.chapters)) {
    throw new Error("Invalid chapters manifest: chapters must be an array");
  }

  for (const chapter of parsed.chapters) {
    if (
      typeof chapter.position !== "number" ||
      typeof chapter.songTitle !== "string" ||
      typeof chapter.startSeconds !== "number" ||
      typeof chapter.endSeconds !== "number" ||
      !Array.isArray(chapter.lines)
    ) {
      throw new Error("Invalid chapter structure");
    }
  }

  return parsed;
}

/**
 * Calculate chapter durations for progress tracking.
 *
 * @param manifest - Chapters manifest
 * @returns Array of chapter durations in seconds
 */
export function getChapterDurations(manifest: ChaptersManifest): number[] {
  return manifest.chapters.map(
    (chapter) => chapter.endSeconds - chapter.startSeconds
  );
}

/**
 * Get total progress percentage within a specific chapter.
 *
 * @param manifest - Chapters manifest
 * @param positionSeconds - Current time position
 * @returns Object with chapter index and progress percentage (0-100)
 */
export function getChapterProgress(
  manifest: ChaptersManifest,
  positionSeconds: number
): { chapterIndex: number; progressPercent: number } | null {
  const chapterIndex = findChapterAtTime(manifest, positionSeconds);
  if (chapterIndex < 0) {
    return null;
  }

  const chapter = manifest.chapters[chapterIndex];
  const chapterDuration = chapter.endSeconds - chapter.startSeconds;

  if (chapterDuration <= 0) {
    return { chapterIndex, progressPercent: 0 };
  }

  const progressInChapter = positionSeconds - chapter.startSeconds;
  const progressPercent = Math.min(
    100,
    Math.max(0, (progressInChapter / chapterDuration) * 100)
  );

  return { chapterIndex, progressPercent };
}
