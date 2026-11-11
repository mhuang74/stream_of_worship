// Section Label vocabulary: the single numbering rule for Recording sections.
//
// Recording section labels arrive verbatim from analysis ("verse", "chorus",
// "bridge", …). Every surface that shows them — the Arrange sheet's Step
// names and the Section Label headings above lyric lines in the Song pullup,
// the Songset audition pullup and the Worship controller sheet — derives its
// text here, so two surfaces can never number the same Recording differently
// (issues #304–#306).

import type { RecordingSection } from "@/lib/arrangements";

export interface SectionHeading {
  /** Zero-based index into the Recording's sections array. */
  index: number;
  section: RecordingSection;
  /**
   * The section label, occurrence-numbered only when that label repeats:
   * `chorus` alone, but `chorus 1`, `chorus 2` when a second chorus exists.
   */
  displayLabel: string;
}

/** One heading per Recording section, in section order; `[]` without sections. */
export function buildSectionHeadings(
  sections: RecordingSection[] | null
): SectionHeading[] {
  if (!sections || sections.length === 0) return [];
  const totals = new Map<string, number>();
  for (const section of sections) {
    totals.set(section.label, (totals.get(section.label) ?? 0) + 1);
  }
  const seen = new Map<string, number>();
  return sections.map((section, index) => {
    const occurrence = (seen.get(section.label) ?? 0) + 1;
    seen.set(section.label, occurrence);
    const repeated = (totals.get(section.label) ?? 0) > 1;
    return {
      index,
      section,
      displayLabel: repeated ? `${section.label} ${occurrence}` : section.label,
    };
  });
}

/**
 * Index of the section whose `[start, end)` contains `timeSeconds`, or -1.
 * Sections may leave gaps; a position in a gap matches nothing.
 */
export function sectionIndexAtPosition(
  sections: RecordingSection[],
  timeSeconds: number
): number {
  for (let index = 0; index < sections.length; index += 1) {
    const section = sections[index]!;
    if (timeSeconds >= section.start && timeSeconds < section.end) return index;
  }
  return -1;
}

/**
 * Heading for each of `positions` (positions in the Recording's own timeline).
 *
 * A label is emitted only where it changes, so callers render a heading before
 * the first line of each section run. A position that matches no section
 * inherits the previous heading, so a heading never vanishes mid-list; a run
 * before any matched section emits nothing. Without section data every entry
 * is `null`, which renders the flat list callers showed before.
 */
export function headingForPositions(
  sections: RecordingSection[] | null,
  positions: readonly number[]
): (string | null)[] {
  const headings = buildSectionHeadings(sections);
  if (headings.length === 0) return positions.map(() => null);
  const labels = headings.map((heading) => heading.displayLabel);
  // Resolution is sticky: an uncovered position keeps the previous heading.
  let resolved: string | null = null;
  let emitted: string | null = null;
  return positions.map((position) => {
    const index = sectionIndexAtPosition(sections!, position);
    if (index >= 0) resolved = labels[index]!;
    if (resolved === null || resolved === emitted) return null;
    emitted = resolved;
    return resolved;
  });
}
