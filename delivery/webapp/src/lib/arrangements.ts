// Canonical arrangement/timing vocabulary for the Song Arrangement Editor
// (issue #282, spec: specs/song_arrangement_editor.md §1).
//
// Every other webapp layer (persistence, editor API, render metrics, preview
// UI) must reuse this module — never ad hoc JSON.parse arrangement data in
// components.
//
// The Python mirror lives at
// delivery/render-worker/src/sow_render_worker/arrangements.py and must stay
// byte-compatible with this module: the shared numeric fixture
// src/test/fixtures/arrangements.cross_language.json is asserted identical
// (1e-9) in both test suites.

export interface RecordingSection {
  label: string;
  start: number;
  end: number;
}

export interface ArrangementStep {
  /**
   * Null only for a Songset-local LRC-inferred Intro; detected sections use
   * their zero-based Recording section index.
   */
  sectionIndex: number | null;
  label: string;
  startSeconds: number;
  endSeconds: number;
  /**
   * Required for an inferred Intro and for user-adjusted bounds. It skips
   * only beat-snap equality — range validation still applies.
   */
  manualBounds?: true;
}

export interface SongArrangement {
  version: 1;
  steps: ArrangementStep[];
}

export interface TimelineClip {
  recordingHashPrefix: string;
  /** Mirrors ArrangementStep.sectionIndex; -1 for an untouched Recording. */
  sectionIndex: number | null;
  sourceStartSeconds: number;
  sourceEndSeconds: number;
  startSeconds: number;
  endSeconds: number;
}

export interface ArrangementTimelineItem {
  itemId: string;
  recordingHashPrefix: string | null;
  startSeconds: number;
  durationSeconds: number;
  gapBeforeSeconds: number;
  crossfadeSeconds: number;
  clips: TimelineClip[];
}

export interface ArrangementTimeline {
  items: ArrangementTimelineItem[];
  totalSeconds: number;
}

export interface ArrangementItemInput {
  recordingDurationSeconds?: number | null;
  // SongsetItemDetail carries the duration on its recording instead.
  recording?: { durationSeconds?: number | null } | null;
  arrangementSteps?: string | null;
  gapBeats?: number | null;
  crossfadeEnabled?: boolean | number | null;
  crossfadeDurationSeconds?: number | null;
  tempoBpm?: number | null;
}

export interface TimelineItemSource extends ArrangementItemInput {
  itemId: string;
  recordingHashPrefix: string | null;
  /** Current parsed analysis (recordings.sections); undefined = caller has none. */
  recordingSections?: RecordingSection[] | null;
  /** Current parsed beats (recordings.beats); empty = analysis unavailable. */
  recordingBeats?: number[] | null;
}

// Canonical half-up rounding to six decimals, pinned in BOTH languages.
// JS: Math.round(x * 1e6) / 1e6. Python mirror: math.floor(x * 1e6 + 0.5) / 1e6.
// Never Python's built-in round() (half-even) or JS toFixed: their divergence
// at the 6th decimal is exactly the validation tolerance (1e-6) and would
// cause spurious staleness. Identical for nonnegative values, which is all
// the snapping pipeline produces (bounds are clamped to [0, duration]).
function roundSeconds(x: number): number {
  return Math.round(x * 1e6) / 1e6;
}

export const VALIDATION_TOLERANCE_SECONDS = 0.000001;

export const MAX_ARRANGEMENT_STEPS = 64;

// Exact message used when a present arrangement cannot be validated against
// current analysis. The Python mirror raises this same string (repair-or-reset).
export const ARRANGEMENT_UNAVAILABLE_OR_STALE_MESSAGE =
  "Song arrangement is unavailable or stale; repair the Recording analysis or reset the arrangement";

export type ArrangementValidationCode =
  | "ANALYSIS_UNAVAILABLE"
  | "ARRANGEMENT_INVALID"
  | "ARRANGEMENT_STALE";

export type ValidateSongArrangementResult =
  | { ok: true; arrangement: SongArrangement | null }
  | { ok: false; code: ArrangementValidationCode };

export type ParseSongArrangementResult =
  | { present: false }
  | { present: true; arrangement: SongArrangement }
  | { present: true; invalid: true };

// JSON booleans/null/strings are not numbers; NaN/Infinity never pass.
function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

// ---------------------------------------------------------------------------
// Recording analysis parsing
// ---------------------------------------------------------------------------

function isSection(value: unknown): value is RecordingSection {
  return (
    isPlainObject(value) &&
    isNonEmptyString(value.label) &&
    isFiniteNumber(value.start) &&
    isFiniteNumber(value.end)
  );
}

// SQL NULL or unreadable analysis -> null. Sections may touch/overlap and
// need not cover the Recording; each needs a nonempty label and a finite range.
export function parseRecordingSections(
  value: string | null,
): RecordingSection[] | null {
  if (value === null) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    return null;
  }
  if (!Array.isArray(parsed)) return null;
  const sections: RecordingSection[] = [];
  for (const entry of parsed) {
    if (!isSection(entry)) return null;
    sections.push({ label: entry.label, start: entry.start, end: entry.end });
  }
  return sections;
}

// Ascending sort with duplicates removed; both parsing and snapping need
// the identical before-nearest-selection ordering.
function sortedDeduped(beats: number[]): number[] {
  beats.sort((a, b) => a - b);
  const deduped: number[] = [];
  for (const beat of beats) {
    if (deduped.length === 0 || beat !== deduped[deduped.length - 1]) {
      deduped.push(beat);
    }
  }
  return deduped;
}

// Finite in-duration beats, sorted ascending, deduped. Empty means unavailable.
function normalizeBeats(beats: unknown[], durationSeconds: number): number[] {
  if (!isFiniteNumber(durationSeconds) || durationSeconds <= 0) return [];
  const filtered: number[] = [];
  for (const beat of beats) {
    if (!isFiniteNumber(beat)) continue;
    if (beat < 0 || beat > durationSeconds) continue;
    filtered.push(beat);
  }
  return sortedDeduped(filtered);
}

// Finite beats sorted ascending with duplicates removed. Snapping drops
// out-of-duration values; an empty array means analysis is unavailable.
export function parseRecordingBeats(value: string | null): number[] {
  if (value === null) return [];
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    return [];
  }
  if (!Array.isArray(parsed)) return [];
  const beats: number[] = [];
  for (const beat of parsed) {
    if (isFiniteNumber(beat)) beats.push(beat);
  }
  return sortedDeduped(beats);
}

// ---------------------------------------------------------------------------
// Snapping
// ---------------------------------------------------------------------------

// Nearest beat, ties to the earlier beat: a candidate replaces the current
// best only on strict '<' distance, so ascending order keeps the earlier one.
function snapToBeat(x: number, beats: number[]): number {
  let best = beats[0];
  let bestDistance = Math.abs(x - best);
  for (let i = 1; i < beats.length; i++) {
    const distance = Math.abs(x - beats[i]);
    if (distance < bestDistance) {
      best = beats[i];
      bestDistance = distance;
    }
  }
  return best;
}

// Snap every section start/end independently to the nearest detected beat,
// then clamp to [0, durationSeconds] and round canonically. sectionIndex is
// the position in the parsed sections array; invalid sections are skipped but
// keep their indices. Returns null when beats are missing, no section is
// valid, or any snap collapses to zero length (analysis unavailable).
export function buildSnappedSteps(
  sections: RecordingSection[] | null,
  beats: number[],
  durationSeconds: number,
): ArrangementStep[] | null {
  if (!sections || sections.length === 0) return null;
  if (!Array.isArray(beats)) return null;
  const normalizedBeats = normalizeBeats(beats, durationSeconds);
  if (normalizedBeats.length === 0) return null;

  const steps: ArrangementStep[] = [];
  for (let i = 0; i < sections.length; i++) {
    const section = sections[i];
    if (!isSection(section)) continue;
    if (section.start < 0 || section.end > durationSeconds) continue;
    if (section.start >= section.end) continue;

    const start = roundSeconds(
      Math.min(Math.max(snapToBeat(section.start, normalizedBeats), 0), durationSeconds),
    );
    const end = roundSeconds(
      Math.min(Math.max(snapToBeat(section.end, normalizedBeats), 0), durationSeconds),
    );
    if (!(start < end)) return null; // snap collapsed the section to zero length
    steps.push({ sectionIndex: i, label: section.label, startSeconds: start, endSeconds: end });
  }
  return steps.length > 0 ? steps : null;
}

// ---------------------------------------------------------------------------
// Persisted arrangement parsing (SQL NULL vs malformed JSON)
// ---------------------------------------------------------------------------

const BASE_STEP_KEYS = ["sectionIndex", "label", "startSeconds", "endSeconds"] as const;

// Exactly the base keys, plus optionally `manualBounds: true` (never false/1/
// "true"/null — a false value must not survive as a second spelling of
// "not edited"). A null section index is legal only for the inferred Intro.
function isValidStep(step: unknown): step is ArrangementStep {
  if (!isPlainObject(step)) return false;
  const keys = Object.keys(step);
  const hasManual = keys.includes("manualBounds");
  if (keys.length !== (hasManual ? 5 : 4)) return false;
  for (const key of keys) {
    if (key !== "manualBounds" && !(BASE_STEP_KEYS as readonly string[]).includes(key)) {
      return false;
    }
  }
  for (const key of BASE_STEP_KEYS) {
    if (!(key in step)) return false;
  }
  if (hasManual && step.manualBounds !== true) return false;
  const { sectionIndex: index, label } = step;
  if (!isNonEmptyString(label) || [...label].length > 100) return false;
  if (!isFiniteNumber(step.startSeconds) || !isFiniteNumber(step.endSeconds)) return false;
  if (!(step.startSeconds >= 0 && step.startSeconds < step.endSeconds)) return false;
  if (index === null) {
    return label === "intro" && step.startSeconds === 0 && hasManual;
  }
  return typeof index === "number" && Number.isInteger(index) && index >= 0;
}

// Distinguishes NULL (absent arrangement) from malformed JSON (present but
// invalid, kept verbatim for diagnostics and duplication). Exactly the
// persisted schema fields, 1–64 Steps, duplicate indices legal.
export function parseSongArrangement(
  value: string | null,
): ParseSongArrangementResult {
  if (value === null) return { present: false };
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    return { present: true, invalid: true };
  }
  if (
    !isPlainObject(parsed) ||
    Object.keys(parsed).length !== 2 ||
    parsed.version !== 1 ||
    !Array.isArray(parsed.steps) ||
    parsed.steps.length < 1 ||
    parsed.steps.length > MAX_ARRANGEMENT_STEPS
  ) {
    return { present: true, invalid: true };
  }
  for (const step of parsed.steps) {
    if (!isValidStep(step)) return { present: true, invalid: true };
  }
  return {
    present: true,
    arrangement: {
      version: 1,
      steps: parsed.steps.map((step) => ({ ...(step as ArrangementStep) })),
    },
  };
}

// ---------------------------------------------------------------------------
// Validation (analysis availability, structural validity, staleness)
// ---------------------------------------------------------------------------

// At save the server independently rebuilds every expected Step from current
// analysis and accepts only exact labels plus bounds within 1e-6 inclusive
// (pinned rounding makes both sides identical; the tolerance only absorbs
// float noise). Rejects forged cuts and stale analysis.
//
// A null-index Intro and any `manualBounds` Step are Songset-Item-local cuts:
// LRC is never re-fetched or re-validated, so later lyric edits can neither
// invalidate nor rewrite them. Range checks stay unconditional; only the
// snapped-bounds equality is skipped for them.
export function validateSongArrangement(
  value: string | null,
  sections: RecordingSection[] | null,
  beats: number[],
  durationSeconds: number,
): ValidateSongArrangementResult {
  const parsed = parseSongArrangement(value);
  if (!parsed.present) {
    return { ok: true, arrangement: null }; // NULL reset is always valid
  }
  if ("invalid" in parsed) {
    return { ok: false, code: "ARRANGEMENT_INVALID" };
  }

  const steps = parsed.arrangement.steps;
  // Analysis is consulted only when a detected Step exists: an Intro-only
  // arrangement needs no sections or beats to be savable.
  let expectedByIndex: Map<number, ArrangementStep> | null = null;
  if (steps.some((step) => step.sectionIndex !== null)) {
    const snapped = buildSnappedSteps(sections, beats, durationSeconds);
    if (!snapped) return { ok: false, code: "ANALYSIS_UNAVAILABLE" };
    expectedByIndex = new Map(snapped.map((step) => [step.sectionIndex as number, step]));
  }

  for (const step of steps) {
    if (step.sectionIndex !== null) {
      const expected = expectedByIndex?.get(step.sectionIndex);
      if (!expected) return { ok: false, code: "ARRANGEMENT_STALE" };
      if (expected.label !== step.label) return { ok: false, code: "ARRANGEMENT_STALE" };
      if (
        step.manualBounds !== true &&
        (Math.abs(step.startSeconds - expected.startSeconds) >
          VALIDATION_TOLERANCE_SECONDS ||
          Math.abs(step.endSeconds - expected.endSeconds) >
            VALIDATION_TOLERANCE_SECONDS)
      ) {
        return { ok: false, code: "ARRANGEMENT_STALE" };
      }
    }
    if (
      step.startSeconds < 0 ||
      step.endSeconds > durationSeconds ||
      step.startSeconds >= step.endSeconds
    ) {
      return { ok: false, code: "ARRANGEMENT_STALE" };
    }
  }
  return { ok: true, arrangement: parsed.arrangement };
}

// ---------------------------------------------------------------------------
// Durations
// ---------------------------------------------------------------------------

// SongsetItemDetail nests the Recording under `recording`; worker-shaped
// callers pass a flat recordingDurationSeconds. Flat wins when both exist.
function itemRecordingDurationSeconds(
  item: ArrangementItemInput,
): number | null {
  if (isFiniteNumber(item.recordingDurationSeconds)) {
    return item.recordingDurationSeconds;
  }
  const nested = item.recording?.durationSeconds;
  return isFiniteNumber(nested) ? nested : null;
}

// Valid custom arrangement -> sum of Step lengths; untouched -> Recording
// duration; any present but structurally invalid arrangement -> null rather
// than a misleading original duration.
export function getItemDurationSeconds(
  item: ArrangementItemInput,
): number | null {
  const parsed = parseSongArrangement(item.arrangementSteps ?? null);
  if (!parsed.present) {
    return itemRecordingDurationSeconds(item);
  }
  if ("invalid" in parsed) return null;
  return parsed.arrangement.steps.reduce(
    (sum, step) => sum + (step.endSeconds - step.startSeconds),
    0,
  );
}

// Mirrors audio_engine.calculate_gap_ms: an enabled nonzero crossfade
// suppresses the gap; otherwise gapBeats (default 2.0) at tempoBpm, falling
// back to one second per beat when BPM is absent or non-positive.
export function calculateGapSeconds(item: ArrangementItemInput): number {
  if (item.crossfadeEnabled && item.crossfadeDurationSeconds) return 0;
  const gapBeats = item.gapBeats ?? 2.0;
  if (item.tempoBpm && item.tempoBpm > 0) {
    return gapBeats * (60.0 / item.tempoBpm);
  }
  return gapBeats;
}

// Mirrors audio_engine.get_crossfade_ms plus the clamp applied at
// audio_engine.py:248: crossfade = min(requested, incomingArrangedDuration).
function crossfadeSecondsFor(
  item: ArrangementItemInput,
  durationSeconds: number,
): number {
  if (!item.crossfadeEnabled || !item.crossfadeDurationSeconds) return 0;
  return Math.max(0, Math.min(item.crossfadeDurationSeconds, durationSeconds));
}

interface ItemLayout {
  itemDurationSeconds: number | null;
  startSeconds: number;
  gapBeforeSeconds: number;
  crossfadeSeconds: number;
}

// Single layout definition shared by durations and the preview timeline.
// Mirrors audio_engine.generate_songset_audio: item zero starts at 0 with no
// gap/crossfade; later items start at max(0, previousEnd + gap - crossfade)
// with the incoming item owning the fade and the crossfade clamped by its own
// arranged duration. Whole-Songset duration = the last item's end time.
function layoutSongsetItems(
  items: ArrangementItemInput[],
): { layouts: ItemLayout[]; totalSeconds: number | null } {
  let previousEnd = 0;
  let allDurationsKnown = true;
  const layouts: ItemLayout[] = [];
  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    const itemDurationSeconds = getItemDurationSeconds(item);
    if (itemDurationSeconds === null) allDurationsKnown = false;
    const gapBeforeSeconds = i === 0 ? 0 : calculateGapSeconds(item);
    const crossfadeSeconds =
      i === 0 ? 0 : crossfadeSecondsFor(item, itemDurationSeconds ?? 0);
    const startSeconds =
      i === 0 ? 0 : Math.max(0, previousEnd + gapBeforeSeconds - crossfadeSeconds);
    previousEnd = startSeconds + (itemDurationSeconds ?? 0);
    layouts.push({
      itemDurationSeconds,
      startSeconds,
      gapBeforeSeconds,
      crossfadeSeconds,
    });
  }
  return { layouts, totalSeconds: allDurationsKnown ? previousEnd : null };
}

// Whole-Songset selected-audio total = last item's end time (the single
// definition shared with the preview timeline and the worker). Null when any
// present arrangement is structurally invalid or a duration is unknown.
export function getSongsetDurationSeconds(
  items: ArrangementItemInput[],
): number | null {
  return layoutSongsetItems(items).totalSeconds;
}

// ---------------------------------------------------------------------------
// Preview timeline
// ---------------------------------------------------------------------------

export function buildArrangementTimeline(
  items: TimelineItemSource[],
): ArrangementTimeline {
  const { layouts } = layoutSongsetItems(items);
  const timelineItems: ArrangementTimelineItem[] = items.map((item, index) => {
    const layout = layouts[index];
    const durationSeconds = layout.itemDurationSeconds ?? 0;
    const parsed = parseSongArrangement(item.arrangementSteps ?? null);
    const arrangement =
      parsed.present && !("invalid" in parsed) ? parsed.arrangement : null;

    const clips: TimelineClip[] = [];
    if (item.recordingHashPrefix !== null) {
      if (arrangement) {
        // Steps inside an item join directly — no gaps or fades.
        let offset = 0;
        for (const step of arrangement.steps) {
          const length = step.endSeconds - step.startSeconds;
          clips.push({
            recordingHashPrefix: item.recordingHashPrefix,
            sectionIndex: step.sectionIndex,
            sourceStartSeconds: step.startSeconds,
            sourceEndSeconds: step.endSeconds,
            startSeconds: layout.startSeconds + offset,
            endSeconds: layout.startSeconds + offset + length,
          });
          offset += length;
        }
      } else if (!parsed.present) {
        const sourceDuration =
          itemRecordingDurationSeconds(item) ?? durationSeconds;
        clips.push({
          recordingHashPrefix: item.recordingHashPrefix,
          sectionIndex: -1,
          sourceStartSeconds: 0,
          sourceEndSeconds: sourceDuration,
          startSeconds: layout.startSeconds,
          endSeconds: layout.startSeconds + sourceDuration,
        });
      }
    }

    return {
      itemId: item.itemId,
      recordingHashPrefix: item.recordingHashPrefix,
      startSeconds: layout.startSeconds,
      durationSeconds,
      gapBeforeSeconds: layout.gapBeforeSeconds,
      crossfadeSeconds: layout.crossfadeSeconds,
      clips,
    };
  });

  const lastLayout = layouts[layouts.length - 1];
  return {
    items: timelineItems,
    totalSeconds: lastLayout
      ? lastLayout.startSeconds + (lastLayout.itemDurationSeconds ?? 0)
      : 0,
  };
}
