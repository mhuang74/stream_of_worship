// Client-safe payload mapper for the Songset audition player.
//
// The player only ever sees the JSON returned by `GET /api/songsets/{id}`.
// Keeping the input type structural means this module never pulls a
// `@/lib/db/...` import (or Drizzle/server code) into the client bundle.

import type { RecordingSection, TimelineClip, TimelineItemSource } from "@/lib/arrangements";
import { linesForSourceRange, type GlobalLRCLine, type LRCLine } from "@/lib/render/lrc-parser";

export interface ListenSongsetItemPayload {
  id: string;
  recordingHashPrefix: string | null;
  arrangementSteps: string | null;
  gapBeats: number | null;
  crossfadeEnabled: number | null;
  crossfadeDurationSeconds: number | null;
  song: { title: string | null } | null;
  recording: {
    /** Recording identity for the Lyrics panel (LRC fetch); unused by timing. */
    contentHash: string | null;
    tempoBpm: number | null;
    durationSeconds: number | null;
    sections: RecordingSection[] | null;
    beats: number[] | null;
  } | null;
}

export function toListenTimelineSources(
  items: ListenSongsetItemPayload[],
): TimelineItemSource[] {
  return items.map((item) => ({
    itemId: item.id,
    recordingHashPrefix: item.recordingHashPrefix,
    arrangementSteps: item.arrangementSteps,
    gapBeats: item.gapBeats,
    crossfadeEnabled: item.crossfadeEnabled,
    crossfadeDurationSeconds: item.crossfadeDurationSeconds,
    tempoBpm: item.recording?.tempoBpm ?? null,
    recordingDurationSeconds: item.recording?.durationSeconds ?? null,
    recordingSections: item.recording?.sections ?? null,
    recordingBeats: item.recording?.beats ?? null,
  }));
}

export interface SongsetLyricLine extends GlobalLRCLine {
  /** Same value as globalTimeSeconds; named for readability at call sites. */
  timelineSeconds: number;
}

/**
 * Lyrics lines of ONE Songset Item, placed on the Songset timeline.
 *
 * Each clip takes its own lines via linesForSourceRange with the Item's other
 * clips as siblings (the same tiling the render worker and the Arrangement
 * Editor use), then each line is shifted by the clip's timeline offset.
 * A pickup line that precedes its Step (LYRIC_BOUNDARY_TOLERANCE_SECONDS)
 * clamps to the clip start so it is highlighted at the boundary.
 */
export function toSongsetLyricLines(
  clips: readonly TimelineClip[],
  lines: readonly LRCLine[],
): SongsetLyricLine[] {
  if (clips.length === 0) return [];
  const siblings = clips.map((clip) => ({
    startSeconds: clip.sourceStartSeconds,
    endSeconds: clip.sourceEndSeconds,
  }));
  const result: SongsetLyricLine[] = [];
  for (const clip of clips) {
    const assigned = linesForSourceRange(
      lines as LRCLine[],
      clip.sourceStartSeconds,
      clip.sourceEndSeconds,
      siblings,
    );
    for (const line of assigned) {
      const timeline = Math.max(
        clip.startSeconds,
        clip.startSeconds + (line.timeSeconds - clip.sourceStartSeconds),
      );
      result.push({
        text: line.text,
        localTimeSeconds: line.timeSeconds,
        globalTimeSeconds: timeline,
        timelineSeconds: timeline,
        title: "",
      });
    }
  }
  return result;
}
