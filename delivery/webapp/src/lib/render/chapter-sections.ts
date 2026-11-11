// Chapter line → Section Label headings for the Worship controller sheet
// (issue #306).
//
// The chapters manifest carries GLOBAL Songset-timeline seconds: the render
// worker already remapped each line through the arranged clips. To label a
// line we must map it back to its position in the source Recording, and that
// is only meaningful while the manifest describes the Song Arrangement it was
// rendered from. So this module returns `null` — render flat — whenever the
// chapters and the current timeline do not line up; a wrong label is never an
// acceptable fallback.

import {
  buildArrangementTimeline,
  type TimelineClip,
  type TimelineItemSource,
} from "@/lib/arrangements";
import type { Chapter } from "@/lib/render/chapters";
import { headingForPositions } from "@/lib/section-labels";

/**
 * How far a chapter's start may sit from its Item's timeline start and still
 * be trusted to describe the current Song Arrangement. The worker writes a
 * chapter start from the same layout the webapp computes, so a real match is
 * exact; the tolerance only absorbs JSON rounding.
 */
const CHAPTER_MATCH_TOLERANCE_SECONDS = 0.5;

/**
 * Per-chapter headings, one entry per chapter line (null = no heading).
 *
 * Returns `null` when the sheet must render flat: no Items, a chapter count
 * that differs from the Item count, a chapter start that does not match its
 * Item's timeline start, or a timeline the Item list cannot produce. A chapter
 * whose Recording has no section data yields all-null entries — flat too.
 */
export function chapterLineHeadings(
  chapters: Chapter[],
  items: TimelineItemSource[],
  toleranceSeconds: number = CHAPTER_MATCH_TOLERANCE_SECONDS,
): (string | null)[][] | null {
  if (items.length === 0 || chapters.length !== items.length) return null;

  const timeline = buildArrangementTimeline(items);
  const rows: (string | null)[][] = [];
  for (let index = 0; index < chapters.length; index += 1) {
    const chapter = chapters[index];
    const item = timeline.items[index];
    const source = items[index];
    if (!chapter || !item || !source) return null;
    if (
      !Number.isFinite(chapter.startSeconds) ||
      Math.abs(chapter.startSeconds - item.startSeconds) > toleranceSeconds
    ) {
      return null;
    }
    const headings = headingsForChapter(chapter, item.clips, source);
    if (headings === null) return null;
    rows.push(headings);
  }
  return rows;
}

function headingsForChapter(
  chapter: Chapter,
  clips: TimelineClip[],
  source: TimelineItemSource
): (string | null)[] | null {
  if (clips.length === 0) return null;
  const positions: number[] = [];
  for (const line of chapter.lines) {
    const position = sourcePositionFor(clips, line.startSeconds);
    // A line the arranged clips cannot reach means the manifest does not
    // describe this Song Arrangement: no headings rather than a wrong one.
    if (position === null) return null;
    positions.push(position);
  }
  return headingForPositions(source.recordingSections ?? null, positions);
}

/**
 * Map a global timeline second back to a position in the Recording through the
 * Item's arranged clips, or `null` when no clip covers it (the sheet then
 * renders flat). A second at the very end of the last clip resolves to that
 * clip's source end.
 */
function sourcePositionFor(clips: TimelineClip[], timelineSeconds: number): number | null {
  for (let index = 0; index < clips.length; index += 1) {
    const clip = clips[index];
    if (!clip) continue;
    if (timelineSeconds < clip.startSeconds) continue;
    if (timelineSeconds < clip.endSeconds) {
      const offset = Math.max(0, timelineSeconds - clip.startSeconds);
      return Math.min(clip.sourceStartSeconds + offset, clip.sourceEndSeconds);
    }
    const next = clips[index + 1];
    if (!next && timelineSeconds === clip.endSeconds) return clip.sourceEndSeconds;
  }
  return null;
}
