/**
 * Lyric-line jump targeting for the playback controller keyboard shortcuts.
 *
 * ArrowLeft/ArrowRight jump to the previous/next lyric line (instead of a
 * blind ±10s seek). The targeting mirrors the LyricJumpList cursor so the
 * keyboard and the lyric sheet agree on which line is "current":
 *
 * - The active line is resolved with the same cursor lead (0.3s) the lyric
 *   sheet uses, so the highlight and the hotkeys never disagree.
 * - ArrowRight on the last line of a song crosses into the first line of the
 *   next song that has synced lines.
 * - ArrowLeft uses a music-player restart threshold: more than
 *   LINE_RESTART_THRESHOLD_SECONDS into the current line restarts it;
 *   otherwise it goes to the previous line, crossing into the previous song
 *   when at the song's first line.
 * - A chapter with no synced lyric lines yields no target at all — the
 *   caller falls back to the ±10s seek so the keys are never dead.
 */
import type { Chapter } from "./chapters";
import { findCurrentLyricIndex, type GlobalLRCLine } from "./lrc-parser";

/**
 * How far ahead of a line's timestamp the cursor (and hotkey targeting)
 * considers the line active. Shared with LyricJumpList's highlight cursor.
 */
export const CURSOR_LEAD_SECONDS = 0.3;

/**
 * Pressing ArrowLeft more than this many seconds into the current line
 * restarts the line instead of jumping to the previous one.
 */
export const LINE_RESTART_THRESHOLD_SECONDS = 3;

export type LineJumpDirection = "previous" | "next";

function toGlobalLines(chapter: Chapter): GlobalLRCLine[] {
  return chapter.lines.map((line) => ({
    text: line.text,
    localTimeSeconds: line.startSeconds,
    globalTimeSeconds: line.startSeconds,
    title: "",
  }));
}

/**
 * Start of the first line in `chapter` strictly after `floor`, or null when
 * the chapter has no such line. Bounding the forward cross by the current
 * position keeps an ArrowRight jump monotonic even if a later chapter's lines
 * were already passed.
 */
function firstLineStartAfter(chapter: Chapter | undefined, floor: number): number | null {
  if (!chapter) return null;
  for (const line of chapter.lines) {
    if (line.startSeconds > floor) return line.startSeconds;
  }
  return null;
}

/**
 * Start of the last line in `chapter` strictly before `limit`, or null when
 * the chapter has no such line. Bounding the backward cross by the current
 * chapter's start keeps an ArrowLeft jump monotonic when crossfade makes
 * chapters overlap (a previous chapter's tail lines can fall after the
 * current chapter's start).
 */
function lastLineStartBefore(chapter: Chapter | undefined, limit: number): number | null {
  if (!chapter) return null;
  for (let i = chapter.lines.length - 1; i >= 0; i--) {
    if (chapter.lines[i].startSeconds < limit) return chapter.lines[i].startSeconds;
  }
  return null;
}

/**
 * Resolve the seek target (absolute seconds on the global timeline) for a
 * lyric-line jump, or `null` when no line jump applies (the caller should
 * fall back to its previous behavior, e.g. a ±10s seek).
 *
 * @param chapters - Chapter manifest for the whole set
 * @param currentSongIndex - Index of the chapter currently playing
 * @param currentTime - Current playback position (global seconds)
 * @param direction - Which line to jump to
 */
export function findLineJumpTarget(
  chapters: Chapter[],
  currentSongIndex: number,
  currentTime: number,
  direction: LineJumpDirection
): number | null {
  const chapter = chapters[currentSongIndex];
  if (!chapter || chapter.lines.length === 0) return null;

  const lines = chapter.lines;
  const activeIndex = findCurrentLyricIndex(
    toGlobalLines(chapter),
    currentTime + CURSOR_LEAD_SECONDS
  );

  if (direction === "next") {
    if (activeIndex < lines.length - 1) {
      return lines[activeIndex + 1].startSeconds;
    }
    // On (or past) the last line of this song → first line of the next song
    // that has synced lines, strictly ahead of the current position.
    for (let i = currentSongIndex + 1; i < chapters.length; i++) {
      const target = firstLineStartAfter(chapters[i], currentTime);
      if (target !== null) return target;
    }
    return null;
  }

  // direction === "previous"
  if (activeIndex >= 0) {
    const lineStart = lines[activeIndex].startSeconds;
    if (currentTime - lineStart > LINE_RESTART_THRESHOLD_SECONDS) {
      return lineStart;
    }
    if (activeIndex > 0) {
      return lines[activeIndex - 1].startSeconds;
    }
  }
  // At (or before) the first line of this song → last line of the previous
  // song that has synced lines, strictly before this chapter's start so the
  // jump stays backward even when crossfade overlaps chapters.
  for (let i = currentSongIndex - 1; i >= 0; i--) {
    const target = lastLineStartBefore(chapters[i], chapter.startSeconds);
    if (target !== null) return target;
  }
  return null;
}
