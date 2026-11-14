// Client-safe payload mapper for the Songset audition player.
//
// The player only ever sees the JSON returned by `GET /api/songsets/{id}`.
// Keeping the input type structural means this module never pulls a
// `@/lib/db/...` import (or Drizzle/server code) into the client bundle.

import type { RecordingSection, TimelineClip, TimelineItemSource } from "@/lib/arrangements";
import {
  linesForSourceRange,
  lyricBoundaryToleranceSeconds,
  type GlobalLRCLine,
  type LRCLine,
} from "@/lib/render/lrc-parser";

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
 * clips as siblings (the same ownership the render worker and the Arrangement
 * Editor apply), then each line is shifted by the clip's timeline offset.
 * Ownership tolerance is two beats at the Item's Recording tempo, so a pickup
 * line that precedes its Step clamps to the clip start and is still highlighted
 * at the boundary. At the end the following LRC line's timestamp decides: a
 * line is kept only while its display ends within the tolerance of the Step, so
 * the next section's first line is not sung in this cut and is dropped — except
 * that a line starting more than two beats before the end is always kept, so a
 * clip ending inside an instrumental gap keeps its last line. A kept line never
 * highlights past the clip end, the same clamp the render worker's
 * remap_lrc_to_clips applies.
 */
export function toSongsetLyricLines(
  clips: readonly TimelineClip[],
  lines: readonly LRCLine[],
  tempoBpm: number | null,
): SongsetLyricLine[] {
  if (clips.length === 0) return [];
  const toleranceSeconds = lyricBoundaryToleranceSeconds(tempoBpm);
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
      toleranceSeconds,
    );
    for (const line of assigned) {
      // `TimelineClip.endSeconds` is `startSeconds + (sourceEnd - sourceStart)`
      // (see `buildArrangementTimeline`), so this end clamp is the same
      // `start_time_seconds + span - 0.001` clamp the render worker applies.
      const timeline = Math.max(
        clip.startSeconds,
        Math.min(
          clip.endSeconds - 0.001,
          clip.startSeconds + (line.timeSeconds - clip.sourceStartSeconds),
        ),
      );
      result.push({
        text: line.text,
        // Heading lookup position, derived from the *clamped* timeline second
        // by the same expression `chapter-sections.ts#sourcePositionFor` uses.
        // A line kept by the ownership tolerance past the clip end would
        // otherwise be labelled with a section this cut never reaches.
        localTimeSeconds: clip.sourceStartSeconds + (timeline - clip.startSeconds),
        globalTimeSeconds: timeline,
        timelineSeconds: timeline,
        title: "",
      });
    }
  }
  return result;
}
