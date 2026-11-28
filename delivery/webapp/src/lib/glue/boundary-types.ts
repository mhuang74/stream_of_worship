import type { GlueBeats } from "./descriptor";

export const songsetBoundaries = ["intro", "outro"] as const;
export type SongsetBoundary = (typeof songsetBoundaries)[number];
export type GlueTarget = "junction" | SongsetBoundary;

export interface BoundarySettings {
  enabled: boolean;
  beats: GlueBeats;
  takeId: string | null;
  revision?: string;
  selections?: Record<string, string>;
}

export type SongsetBoundarySettings = Partial<Record<SongsetBoundary, BoundarySettings>>;

export const defaultBoundarySettings: BoundarySettings = { enabled: false, beats: 8, takeId: null };

export function hasEnabledBoundary(settings?: SongsetBoundarySettings): boolean {
  return songsetBoundaries.some((target) => settings?.[target]?.enabled);
}

export function boundaryDuration(
  boundaries: readonly { selectedTake: { seconds: number } | null }[]
): number {
  return boundaries.reduce((sum, boundary) => sum + (boundary.selectedTake?.seconds ?? 0), 0);
}

export function withBoundaryDuration(
  duration: number | null,
  boundaries: readonly { selectedTake: { seconds: number } | null }[]
): number | null {
  return duration === null ? null : duration + boundaryDuration(boundaries);
}
