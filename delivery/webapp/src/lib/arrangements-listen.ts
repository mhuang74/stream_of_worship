// Client-safe payload mapper for the Songset Listen dialog (spec §3.6).
//
// The dialog only ever sees the JSON returned by `GET /api/songsets/{id}`.
// Keeping the input type structural means this module never pulls a
// `@/lib/db/...` import (or Drizzle/server code) into the client bundle.

import type { RecordingSection, TimelineItemSource } from "@/lib/arrangements";

export interface ListenSongsetItemPayload {
  id: string;
  recordingHashPrefix: string | null;
  arrangementSteps: string | null;
  gapBeats: number | null;
  crossfadeEnabled: number | null;
  crossfadeDurationSeconds: number | null;
  song: { title: string | null } | null;
  recording: {
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
