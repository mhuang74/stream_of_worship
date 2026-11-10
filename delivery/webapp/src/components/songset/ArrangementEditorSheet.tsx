"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import { formatDuration } from "@/lib/format";
import { useLocale } from "@/hooks/useLocale";
import { useSongLyrics } from "@/hooks/useSongLyrics";
import { useArrangementPreview } from "@/hooks/useArrangementPreview";
import type { ArrangementPreviewError } from "@/lib/audio/arrangement-preview";
import {
  isValidLRC,
  linesForSourceRange,
  parseLRC,
  type LRCLine,
} from "@/lib/render/lrc-parser";
import {
  MAX_ARRANGEMENT_STEPS,
  buildArrangementTimeline,
  buildSnappedSteps,
  parseSongArrangement,
  validateSongArrangement,
} from "@/lib/arrangements";
import type {
  ArrangementStep,
  ArrangementTimeline,
  ArrangementTimelineItem,
  RecordingSection,
  SongArrangement,
  TimelineClip,
  TimelineItemSource,
} from "@/lib/arrangements";
import type { SongListItem } from "./SongList";
import {
  AlertTriangle,
  Copy,
  GripVertical,
  ListMusic,
  Loader2,
  Minus,
  Pause,
  Pencil,
  Play,
  Plus,
  RotateCcw,
  Square,
  Trash2,
  Undo2,
} from "lucide-react";

export interface ArrangementEditorSheetProps {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  item: SongListItem;
  items: SongListItem[];
  /** Resolves on success; throws a translated message on failure. */
  onSave: (arrangement: SongArrangement | null) => Promise<void>;
}

/** A draft step with a stable dnd identity so duplicates stay distinguishable. */
interface DraftStep {
  id: string;
  step: ArrangementStep;
}

let draftStepSequence = 0;

/** One bounds-nudge step, matching the displayed `m:ss.s` precision. */
const BOUNDS_STEP_SECONDS = 0.1;
/** How much of the candidate boundary an audition plays. */
const BOUNDS_AUDITION_SECONDS = 3;
/** Beats of material each side of a component junction in a transition review. */
const TRANSITION_REVIEW_BEATS = 4;
/** Transition review passes before the transport stops. */
const TRANSITION_REVIEW_PASSES = 2;
/** Beats of neighbouring-song material at each end of the arrangement preview. */
const ARRANGEMENT_CONTEXT_BEATS = 8;
/** Pointer travel that turns a strip tap into a scrub. */
const SCRUB_CLICK_TOLERANCE_PX = 5;
/** First sung LRC line at or below this time is treated as noise, not an outro-less Intro. */
const INFERRED_INTRO_MIN_SECONDS = 3;
/** In-sheet undo depth; Reset is the persisted restore and is not part of it. */
const HISTORY_LIMIT = 50;

function createDraftStep(step: ArrangementStep): DraftStep {
  draftStepSequence += 1;
  return { id: `draft-step-${draftStepSequence}`, step };
}

function arrangementToDraft(arrangement: SongArrangement): DraftStep[] {
  return arrangement.steps.map(createDraftStep);
}

function draftToArrangement(draft: DraftStep[]): SongArrangement {
  return { version: 1, steps: draft.map((entry) => entry.step) };
}

/** Structural identity of a draft, used to clear the dirty flag on undo. */
function draftKey(draft: DraftStep[]): string {
  return draft
    .map(
      ({ step }) =>
        `${step.sectionIndex}:${step.startSeconds}:${step.endSeconds}:${step.manualBounds ? 1 : 0}`
    )
    .join("|");
}

/** Score timecode (m:ss). Durations use formatDuration for app consistency. */
function formatTimecode(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "0:00";
  const total = Math.floor(seconds);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

/** Bounds timecode: m:ss.s, the precision the popover edits and displays. */
function formatTimecodeTenths(seconds: number): string {
  const totalTenths = Math.round((Number.isFinite(seconds) && seconds > 0 ? seconds : 0) * 10);
  const minutes = Math.floor(totalTenths / 600);
  const rest = totalTenths % 600;
  return `${minutes}:${String(Math.floor(rest / 10)).padStart(2, "0")}.${rest % 10}`;
}

/** Parse m:ss.s; anything else is a hard null (the popover shows an error). */
function parseTimecodeTenths(value: string): number | null {
  const match = /^(\d+):([0-5]\d)\.(\d)$/.exec(value.trim());
  if (!match) return null;
  return Number(match[1]) * 60 + Number(match[2]) + Number(match[3]) / 10;
}

/** The one range rule: inside the recording, nonempty, start before end. */
function isValidRange(
  startSeconds: number,
  endSeconds: number,
  durationSeconds: number | null
): boolean {
  return (
    durationSeconds !== null &&
    startSeconds >= 0 &&
    startSeconds < endSeconds &&
    endSeconds <= durationSeconds
  );
}

/** A user-adjusted Step: same identity, new bounds, always flagged manual. */
function candidateBounds(
  step: ArrangementStep,
  startSeconds: number,
  endSeconds: number,
  manual: boolean
): ArrangementStep {
  const next: ArrangementStep = {
    sectionIndex: step.sectionIndex,
    label: step.label,
    startSeconds,
    endSeconds,
  };
  if (manual) next.manualBounds = true;
  return next;
}

/** Index of the line being sung at a recording-local time, or -1. */
function activeLyricIndex(lines: LRCLine[], localSeconds: number): number {
  let current = -1;
  for (let index = 0; index < lines.length; index += 1) {
    if (lines[index].timeSeconds <= localSeconds) current = index;
    else break;
  }
  return current;
}

interface BoundsEditorPopoverProps {
  index: number;
  step: ArrangementStep;
  displayLabel: string;
  open: boolean;
  disabled: boolean;
  recordingDurationSeconds: number | null;
  /** Raw detected (or raw inferred) Step for Reset, without the manual flag. */
  resetStep: ArrangementStep | null;
  onOpen: () => void;
  onClose: () => void;
  onCandidate: (candidate: ArrangementStep | null) => void;
  onApply: (step: ArrangementStep) => void;
  onAudition: (side: "start" | "end") => void;
}

/**
 * Temporary bounds transaction for one Step: fields, 0.1 s steppers, and 3 s
 * auditions stay local until Apply. Cancel discards everything, so no keystroke
 * can desync the saved arrangement from the lyrics and timecodes it claims.
 */
function BoundsEditorPopover({
  index,
  step,
  displayLabel,
  open,
  disabled,
  recordingDurationSeconds,
  resetStep,
  onOpen,
  onClose,
  onCandidate,
  onApply,
  onAudition,
}: BoundsEditorPopoverProps) {
  const { t } = useLocale();
  const fieldId = useId();
  const helpId = useId();
  const [startText, setStartText] = useState(() => formatTimecodeTenths(step.startSeconds));
  const [endText, setEndText] = useState(() => formatTimecodeTenths(step.endSeconds));
  const [hasLocalEdits, setHasLocalEdits] = useState(false);
  const [resetToDetected, setResetToDetected] = useState<ArrangementStep | null>(null);

  // A null-index inferred Intro always starts at zero; its start is read-only.
  const isFixedStart = step.sectionIndex === null;
  const parsedStart = isFixedStart ? 0 : parseTimecodeTenths(startText);
  const parsedEnd = parseTimecodeTenths(endText);
  const fieldsValid =
    parsedStart !== null &&
    parsedEnd !== null &&
    isValidRange(parsedStart, parsedEnd, recordingDurationSeconds);
  // Reset supplies the raw persisted values: rounding them would make a legacy
  // Step stale against its own analysis.
  const candidate =
    resetToDetected ??
    (parsedStart !== null && parsedEnd !== null && fieldsValid
      ? candidateBounds(step, parsedStart, parsedEnd, true)
      : null);
  const candidateKey = candidate
    ? `${candidate.startSeconds}:${candidate.endSeconds}:${candidate.manualBounds ? 1 : 0}`
    : "invalid";

  // The sheet only needs the bounds, never the keystrokes: notify on identity
  // change so the audition and the strip follow the candidate live. The close
  // path needs no notification — the sheet's closeBoundsEditor clears the
  // override and the invalid flag together.
  useEffect(() => {
    if (!open) return;
    onCandidate(candidate);
    // candidateKey is the full identity of `candidate`; the object itself is
    // recreated each render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, candidateKey]);

  /** The stepped value, or null when the nudge would leave the valid range. */
  const nudgeTarget = (side: "start" | "end", delta: number): number | null => {
    const current = side === "start" ? parsedStart : parsedEnd;
    const other = side === "start" ? parsedEnd : parsedStart;
    if (current === null || other === null) return null;
    const next = Math.round((current + delta) * 10) / 10;
    const valid =
      side === "start"
        ? isValidRange(next, other, recordingDurationSeconds)
        : isValidRange(other, next, recordingDurationSeconds);
    return valid ? next : null;
  };

  const nudge = (side: "start" | "end", delta: number) => {
    const next = nudgeTarget(side, delta);
    if (next === null) return;
    if (side === "start") setStartText(formatTimecodeTenths(next));
    else setEndText(formatTimecodeTenths(next));
    setHasLocalEdits(true);
    setResetToDetected(null);
  };

  const handleReset = () => {
    if (!resetStep) return;
    setStartText(formatTimecodeTenths(resetStep.startSeconds));
    setEndText(formatTimecodeTenths(resetStep.endSeconds));
    setHasLocalEdits(true);
    setResetToDetected(resetStep);
  };

  const stepButton = (side: "start" | "end", delta: number) => (
    <Button
      variant="outline"
      size="icon-sm"
      className="h-11 min-w-11 shrink-0"
      onClick={() => nudge(side, delta)}
      disabled={disabled || nudgeTarget(side, delta) === null}
      aria-label={`${delta < 0 ? t("arrangement.bounds.decrease") : t("arrangement.bounds.increase")} — ${
        side === "start" ? t("arrangement.bounds.start") : t("arrangement.bounds.end")
      } ${displayLabel}`}
      data-testid={`arrangement-bounds-${side}-${delta < 0 ? "dec" : "inc"}-${index}`}
    >
      {delta < 0 ? <Minus className="size-4" /> : <Plus className="size-4" />}
    </Button>
  );

  const auditionButton = (side: "start" | "end") => (
    <Button
      variant="outline"
      size="icon-sm"
      className="h-11 min-w-11 shrink-0"
      onClick={() => onAudition(side)}
      disabled={disabled || candidate === null}
      aria-label={
        side === "start" ? t("arrangement.bounds.auditionStart") : t("arrangement.bounds.auditionEnd")
      }
      data-testid={`arrangement-bounds-audition-${side}-${index}`}
    >
      <Play className="size-4" />
    </Button>
  );

  return (
    <Popover open={open} onOpenChange={(next) => (next ? onOpen() : onClose())}>
      <PopoverTrigger
        className="ml-auto flex h-8 shrink-0 items-center gap-1 rounded-md px-1.5 font-mono text-[11px] tabular-nums text-muted-foreground hover:bg-muted"
        aria-label={t("arrangement.bounds.rangeAria").replace("{label}", displayLabel)}
        data-testid={`arrangement-bounds-trigger-${index}`}
      >
        {formatTimecodeTenths(step.startSeconds)}–{formatTimecodeTenths(step.endSeconds)}
        <Pencil className="size-3" aria-hidden="true" />
      </PopoverTrigger>
      <PopoverContent
        className="w-[min(22rem,calc(100vw-2rem))]"
        data-testid={`arrangement-bounds-popover-${index}`}
      >
        <div className="flex flex-col gap-3">
          {isFixedStart ? (
            <div className="flex items-center gap-2">
              <span className="text-[11px] font-medium text-muted-foreground">
                {t("arrangement.bounds.fixedStart")}
              </span>
              <output
                className="ml-auto font-mono text-[13px] tabular-nums"
                data-testid={`arrangement-bounds-fixed-start-${index}`}
              >
                0:00.0
              </output>
            </div>
          ) : (
            <div className="flex items-center gap-1.5">
              <label
                htmlFor={`${fieldId}-start`}
                className="w-9 shrink-0 text-[11px] font-medium text-muted-foreground"
              >
                {t("arrangement.bounds.start")}
              </label>
              {stepButton("start", -BOUNDS_STEP_SECONDS)}
              <Input
                id={`${fieldId}-start`}
                value={startText}
                onChange={(event) => {
                  setStartText(event.target.value);
                  setHasLocalEdits(true);
                  setResetToDetected(null);
                }}
                inputMode="decimal"
                autoComplete="off"
                aria-invalid={!fieldsValid}
                aria-describedby={helpId}
                aria-label={t("arrangement.bounds.startAria").replace("{label}", displayLabel)}
                data-testid={`arrangement-bounds-input-start-${index}`}
                className="h-11 w-[5.5rem] text-center font-mono text-[13px] tabular-nums"
              />
              {stepButton("start", BOUNDS_STEP_SECONDS)}
              {auditionButton("start")}
            </div>
          )}

          <div className="flex items-center gap-1.5">
            <label
              htmlFor={`${fieldId}-end`}
              className="w-9 shrink-0 text-[11px] font-medium text-muted-foreground"
            >
              {t("arrangement.bounds.end")}
            </label>
            {stepButton("end", -BOUNDS_STEP_SECONDS)}
            <Input
              id={`${fieldId}-end`}
              value={endText}
              onChange={(event) => {
                setEndText(event.target.value);
                setHasLocalEdits(true);
                setResetToDetected(null);
              }}
              inputMode="decimal"
              autoComplete="off"
              aria-invalid={!fieldsValid}
              aria-describedby={helpId}
              aria-label={t("arrangement.bounds.endAria").replace("{label}", displayLabel)}
              data-testid={`arrangement-bounds-input-end-${index}`}
              className="h-11 w-[5.5rem] text-center font-mono text-[13px] tabular-nums"
            />
            {stepButton("end", BOUNDS_STEP_SECONDS)}
            {auditionButton("end")}
          </div>

          {fieldsValid ? (
            <p id={helpId} className="text-[11px] leading-relaxed text-muted-foreground">
              {t("arrangement.bounds.auditionNote")}
            </p>
          ) : (
            <p
              id={helpId}
              className="text-[11px] leading-relaxed text-destructive"
              data-testid={`arrangement-bounds-error-${index}`}
            >
              {t("arrangement.bounds.invalid")}
            </p>
          )}

          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              className="h-11"
              onClick={() => {
                if (candidate) onApply(candidate);
              }}
              disabled={disabled || !hasLocalEdits || candidate === null}
              data-testid={`arrangement-bounds-apply-${index}`}
            >
              {t("arrangement.bounds.apply")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-11"
              onClick={onClose}
              data-testid={`arrangement-bounds-cancel-${index}`}
            >
              {t("arrangement.bounds.cancel")}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-11"
              onClick={handleReset}
              disabled={disabled || resetStep === null}
              data-testid={`arrangement-bounds-reset-${index}`}
            >
              {isFixedStart
                ? t("arrangement.intro.reset")
                : t("arrangement.bounds.resetDetected")}
            </Button>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
}

interface SortableStepBlockProps {
  entry: DraftStep;
  index: number;
  displayLabel: string;
  nextLabel: string;
  isLast: boolean;
  lines: LRCLine[];
  lyricsLoading: boolean;
  lyricsAvailable: boolean;
  activeLineIndex: number;
  isActive: boolean;
  isPlaying: boolean;
  disabled: boolean;
  boundsOpen: boolean;
  /**
   * Lock for the bounds transaction itself. The row's own `disabled` also
   * covers "another Step is being edited", which must not disable the editor
   * the user just opened.
   */
  boundsDisabled: boolean;
  recordingDurationSeconds: number | null;
  resetStep: ArrangementStep | null;
  onOpenBounds: (id: string) => void;
  onCloseBounds: () => void;
  onBoundsCandidate: (id: string, candidate: ArrangementStep | null) => void;
  onBoundsApply: (id: string, step: ArrangementStep) => void;
  onAuditionBounds: (side: "start" | "end") => void;
  onDuplicate: (id: string) => void;
  onRemove: (id: string) => void;
  onReviewTransition: (index: number) => void;
}

/**
 * One arrangement component: its label tab, adjustable timecode, transition
 * review, and the lyric lines that belong to that section. Lyrics are the
 * primary content; the toolbar is deliberately quiet.
 */
function SortableStepBlock({
  entry,
  index,
  displayLabel,
  nextLabel,
  isLast,
  lines,
  lyricsLoading,
  lyricsAvailable,
  activeLineIndex,
  isActive,
  isPlaying,
  disabled,
  boundsOpen,
  boundsDisabled,
  recordingDurationSeconds,
  resetStep,
  onOpenBounds,
  onCloseBounds,
  onBoundsCandidate,
  onBoundsApply,
  onAuditionBounds,
  onDuplicate,
  onRemove,
  onReviewTransition,
}: SortableStepBlockProps) {
  const { t } = useLocale();
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: entry.id, disabled });
  const activeLineRef = useRef<HTMLParagraphElement | null>(null);

  useEffect(() => {
    if (activeLineIndex < 0) return;
    activeLineRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [activeLineIndex]);

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    zIndex: isDragging ? 50 : undefined,
  };

  return (
    <li
      ref={setNodeRef}
      style={style}
      className={cn(
        "overflow-hidden rounded-xl border bg-card text-card-foreground transition-colors",
        isActive ? "border-primary/50 ring-1 ring-primary/20" : "border-border/70",
        isDragging && "opacity-60 shadow-lg"
      )}
      data-testid="arrangement-draft-step"
      data-section-index={
        entry.step.sectionIndex === null ? "inferred-intro" : String(entry.step.sectionIndex)
      }
    >
      <div className="flex items-center gap-1.5 px-2 py-1.5">
        <Button
          variant="ghost"
          size="icon-sm"
          className="shrink-0 cursor-grab touch-none active:cursor-grabbing"
          disabled={disabled}
          {...attributes}
          {...listeners}
          aria-label={`${t("arrangement.action.dragAria")} ${index + 1}: ${displayLabel}`}
        >
          <GripVertical className="size-4 text-muted-foreground" />
        </Button>
        <span className="w-4 shrink-0 text-right font-mono text-[11px] text-muted-foreground">
          {index + 1}
        </span>
        <span className="min-w-0 shrink-0 truncate rounded-md bg-muted px-1.5 py-0.5 text-[10px] font-semibold tracking-[0.1em] text-foreground/75 uppercase">
          {displayLabel}
        </span>
        {entry.step.manualBounds === true && (
          <span
            className="shrink-0 rounded bg-amber-100 px-1 py-0.5 text-[10px] font-semibold text-amber-800 dark:bg-amber-500/15 dark:text-amber-400"
            data-testid={`arrangement-bounds-edited-${index}`}
          >
            {t("arrangement.bounds.edited")}
          </span>
        )}
        <BoundsEditorPopover
          // Keyed on the committed bounds: Apply must remount the session so
          // the fields re-seed from the new Step and Apply starts disabled.
          key={`${entry.id}:${entry.step.startSeconds}:${entry.step.endSeconds}:${
            entry.step.manualBounds ? 1 : 0
          }`}
          index={index}
          step={entry.step}
          displayLabel={displayLabel}
          open={boundsOpen}
          disabled={boundsDisabled}
          recordingDurationSeconds={recordingDurationSeconds}
          resetStep={resetStep}
          onOpen={() => onOpenBounds(entry.id)}
          onClose={onCloseBounds}
          onCandidate={(candidate) => onBoundsCandidate(entry.id, candidate)}
          onApply={(step) => onBoundsApply(entry.id, step)}
          onAudition={onAuditionBounds}
        />
        <Button
          variant="ghost"
          size="sm"
          className={cn("shrink-0 px-1.5", isPlaying && "text-primary")}
          onClick={() => onReviewTransition(index)}
          disabled={disabled || isLast}
          aria-label={t("arrangement.preview.reviewTransitionAria")
            .replaceAll("{fromLabel}", displayLabel)
            .replaceAll("{toLabel}", nextLabel)
            .replaceAll("{beats}", String(TRANSITION_REVIEW_BEATS))}
          data-testid={`arrangement-review-transition-${index}`}
        >
          <Play className="size-4" />
          <span className="hidden text-[11px] sm:inline">
            {t("arrangement.preview.reviewTransition")}
          </span>
        </Button>
        <Button
          variant="ghost"
          size="icon-sm"
          className="shrink-0"
          onClick={() => onDuplicate(entry.id)}
          disabled={disabled}
          aria-label={`${t("arrangement.action.duplicateAria")} ${displayLabel}`}
          data-testid={`arrangement-duplicate-${index}`}
        >
          <Copy className="size-4" />
        </Button>
        <Button
          variant="ghost"
          size="icon-sm"
          className="shrink-0"
          onClick={() => onRemove(entry.id)}
          disabled={disabled}
          aria-label={`${t("arrangement.action.removeAria")} ${displayLabel}`}
          data-testid={`arrangement-remove-${index}`}
        >
          <Trash2 className="size-4 text-destructive" />
        </Button>
      </div>

      <div className="border-t border-border/60 px-3 py-2">
        {lyricsLoading ? (
          <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
            {t("arrangement.lyrics.loading")}
          </p>
        ) : !lyricsAvailable ? (
          <p className="text-sm text-muted-foreground">
            {t("arrangement.lyrics.unavailable")}
          </p>
        ) : lines.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {t("arrangement.lyrics.instrumental")}
          </p>
        ) : (
          <div className="space-y-0.5">
            {lines.map((line, lineIndex) => {
              const active = lineIndex === activeLineIndex;
              const key = `${line.timeSeconds}-${lineIndex}`;
              if (line.text.length === 0) {
                // Gap markers blank the display during instrumental stretches.
                return <span key={key} aria-hidden="true" className="block h-3" />;
              }
              return (
                <p
                  key={key}
                  ref={active ? activeLineRef : undefined}
                  data-testid="arrangement-lyric-line"
                  data-active={active ? "true" : undefined}
                  className={cn(
                    "border-l-2 border-transparent pl-2 font-serif text-[15px] leading-relaxed text-foreground/75 transition-colors",
                    active && "border-primary font-semibold text-foreground"
                  )}
                >
                  {line.text}
                </p>
              );
            })}
          </div>
        )}
      </div>
    </li>
  );
}

interface SourceOccurrence {
  index: number;
  section: RecordingSection;
  displayLabel: string;
}

function buildSourceOccurrences(sections: RecordingSection[] | null): SourceOccurrence[] {
  if (!sections) return [];
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

interface DraftInit {
  draft: DraftStep[];
  /**
   * `invalid` = saved JSON is malformed; `stale` = the steps no longer match
   * current analysis (or analysis is missing); `no-analysis` = nothing to snap.
   */
  problem: "invalid" | "stale" | "no-analysis" | null;
}

type DraftProblem = DraftInit["problem"];

/**
 * Validate a draft against the item's current analysis. The server repeats
 * this check; doing it here keeps Preview/Save disabled until the user has a
 * sequence that can actually be saved.
 */
function validateDraftProblem(
  draft: DraftStep[],
  item: SongListItem
): DraftProblem {
  // An empty or over-limit draft is a live editing state, not a stale saved
  // arrangement: the amber step-count message plus disabled Save/Preview is
  // the only feedback, never a destructive "invalid or stale" alert.
  if (draft.length === 0 || draft.length > MAX_ARRANGEMENT_STEPS) return null;
  const duration = item.recording?.durationSeconds ?? null;
  if (duration == null) return "stale";
  const result = validateSongArrangement(
    JSON.stringify(draftToArrangement(draft)),
    item.recording?.sections ?? null,
    item.recording?.beats ?? [],
    duration
  );
  if (result.ok) return null;
  return result.code === "ARRANGEMENT_INVALID" ? "invalid" : "stale";
}

/** One synchronous derivation at mount — the sheet is remounted per item. */
function deriveDraftInit(item: SongListItem): DraftInit {
  const parsed = parseSongArrangement(item.arrangementSteps);
  if (parsed.present) {
    const draft =
      "invalid" in parsed ? [] : arrangementToDraft(parsed.arrangement);
    return {
      draft,
      problem: "invalid" in parsed ? "invalid" : validateDraftProblem(draft, item),
    };
  }

  const duration = item.recording?.durationSeconds ?? null;
  const snapped =
    duration != null
      ? buildSnappedSteps(
          item.recording?.sections ?? null,
          item.recording?.beats ?? [],
          duration
        )
      : null;
  return {
    draft: snapped ? snapped.map(createDraftStep) : [],
    problem: snapped ? null : "no-analysis",
  };
}

/**
 * A transport window in Songset-timeline seconds. `includesPreviousTail` and
 * `includesNextOpening` say whether neighbouring-song material sits inside it;
 * the Sheet owns the end of every window.
 */
interface ArrangementWindow {
  startSeconds: number;
  endSeconds: number;
  includesPreviousTail: boolean;
  includesNextOpening: boolean;
  /** Seconds of next-song material actually inside the window. */
  actualOpeningSeconds: number;
}

/**
 * `kind` distinguishes the looped transition review, a one-shot bounds
 * audition, and the whole-arrangement preview the rail's button starts.
 */
interface PlaybackWindow extends ArrangementWindow {
  kind: "review" | "audition" | "arrangement";
}

interface StripGesture {
  pointerId: number;
  startX: number;
  startY: number;
  wasPlaying: boolean;
  /** Band start hit on pointer down; null when the tap missed every band. */
  bandStartSeconds: number | null;
  dragging: boolean;
  rectLeft: number;
  rectWidth: number;
}

/** Seconds per beat; 1 s/beat when tempo is unusable (same fallback as calculateGapSeconds). */
function beatSeconds(tempoBpm: number | null | undefined): number {
  return tempoBpm && tempoBpm > 0 ? 60 / tempoBpm : 1;
}

/**
 * The junction window for component `index`: 4 beats before the join into
 * `index + 1` and 4 beats after, clamped to the two adjacent clips so no other
 * component is heard. Null when either side is missing.
 */
function buildTransitionWindow(
  clips: TimelineClip[],
  index: number,
  tempoBpm: number | null
): ArrangementWindow | null {
  const first = clips[index];
  const second = clips[index + 1];
  if (!first || !second) return null;
  const join = first.endSeconds;
  const lead = TRANSITION_REVIEW_BEATS * beatSeconds(tempoBpm);
  const startSeconds = Math.max(first.startSeconds, join - lead);
  const endSeconds = Math.min(second.endSeconds, join + lead);
  if (!(endSeconds > startSeconds)) return null;
  return {
    startSeconds,
    endSeconds,
    includesPreviousTail: false,
    includesNextOpening: false,
    actualOpeningSeconds: 0,
  };
}

interface ActiveClipRef {
  item: ArrangementTimelineItem;
  clip: TimelineClip;
  clipIndex: number;
}

/**
 * Every timeline clip under `position`: two during a crossfade, one inside a
 * single item. Used for status copy so an overlap names both sides.
 */
function activeClipRefs(
  timeline: ArrangementTimeline,
  position: number
): ActiveClipRef[] {
  const refs: ActiveClipRef[] = [];
  for (const timelineItem of timeline.items) {
    const clipIndex = timelineItem.clips.findIndex(
      (clip) => position >= clip.startSeconds && position < clip.endSeconds
    );
    if (clipIndex >= 0) {
      refs.push({ item: timelineItem, clip: timelineItem.clips[clipIndex], clipIndex });
    }
  }
  return refs;
}

interface StripBand {
  key: string;
  kind: "previous" | "step" | "next";
  clipIndex: number | null;
  label: string;
  startSeconds: number;
  endSeconds: number;
}

export function ArrangementEditorSheet({
  isOpen,
  onOpenChange,
  item,
  items,
  onSave,
}: ArrangementEditorSheetProps) {
  const { t } = useLocale();
  const dndContextId = useId();

  // Initialized synchronously at mount; the client remounts the sheet per
  // item (`key`), so there is no cross-item state to reset in an effect.
  const [{ draft, problem: initProblem }, setDraftState] = useState<DraftInit>(() =>
    deriveDraftInit(item)
  );
  const initialDraftRef = useRef<DraftStep[]>(draft);
  const [history, setHistory] = useState<DraftStep[][]>([]);
  const [isDirty, setIsDirty] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [resetConfirmOpen, setResetConfirmOpen] = useState(false);
  const [dirtyDialogOpen, setDirtyDialogOpen] = useState(false);
  const [addDialogOpen, setAddDialogOpen] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [fallbackNotice, setFallbackNotice] = useState(false);
  // Bounds editing is one open transaction at a time: the id of the Step being
  // edited, its candidate (auditioned by the transport but never committed),
  // and whether the candidate is currently unsavable.
  const [boundsEditorId, setBoundsEditorId] = useState<string | null>(null);
  const [boundsPreviewOverride, setBoundsPreviewOverride] =
    useState<{ id: string; step: ArrangementStep } | null>(null);
  const [boundsEditorInvalid, setBoundsEditorInvalid] = useState(false);
  // Transition review: which junction is being reviewed, which pass it has
  // started, and whether the passes are done. The whole-arrangement preview
  // shares the pass-free half of this state.
  const [reviewClipIndex, setReviewClipIndex] = useState<number | null>(null);
  const [reviewPass, setReviewPass] = useState(1);
  const [reviewCompleted, setReviewCompleted] = useState(false);
  const [arrangementCompleted, setArrangementCompleted] = useState(false);
  const [playbackWindow, setPlaybackWindow] = useState<PlaybackWindow | null>(null);
  const boundaryHandledRef = useRef<{ key: string; pass: number } | null>(null);
  const stripGestureRef = useRef<StripGesture | null>(null);
  const suppressStripClickRef = useRef(false);

  const sections = item.recording?.sections ?? null;

  // Palette entries are the current analysis snapped once per source section.
  const snappedByIndex = useMemo(() => {
    const duration = item.recording?.durationSeconds ?? null;
    const snapped =
      duration != null
        ? buildSnappedSteps(
            item.recording?.sections ?? null,
            item.recording?.beats ?? [],
            duration
          )
        : null;
    return new Map((snapped ?? []).map((step) => [step.sectionIndex, step]));
  }, [item.recording?.sections, item.recording?.beats, item.recording?.durationSeconds]);

  const occurrences = useMemo(() => buildSourceOccurrences(sections), [sections]);
  const occurrenceByIndex = useMemo(
    () => new Map(occurrences.map((occurrence) => [occurrence.index, occurrence])),
    [occurrences]
  );

  const draftSteps = useMemo(() => draft.map((entry) => entry.step), [draft]);

  // Occurrence counts shown in the add dialog ("in arrangement ×2"). The
  // inferred Intro has no source index, so the key is nullable.
  const stepCountBySectionIndex = useMemo(() => {
    const counts = new Map<number | null, number>();
    for (const entry of draft) {
      counts.set(
        entry.step.sectionIndex,
        (counts.get(entry.step.sectionIndex) ?? 0) + 1
      );
    }
    return counts;
  }, [draft]);

  const labelForDraftIndex = useCallback(
    (index: number): string | null => {
      const entry = draft[index];
      if (!entry) return null;
      if (entry.step.sectionIndex === null) {
        return t("arrangement.intro.inferredShort");
      }
      return (
        occurrenceByIndex.get(entry.step.sectionIndex)?.displayLabel ??
        entry.step.label
      );
    },
    [draft, occurrenceByIndex, t]
  );

  const draftDurationSeconds = useMemo(
    () =>
      draftSteps.reduce(
        (sum, step) => sum + Math.max(0, step.endSeconds - step.startSeconds),
        0
      ),
    [draftSteps]
  );

  const isDraftValid = draft.length >= 1 && draft.length <= MAX_ARRANGEMENT_STEPS;
  const tooManySteps = draft.length > MAX_ARRANGEMENT_STEPS;
  // Preview and Save stay blocked while the draft cannot validate against the
  // current analysis (stale/malformed saved steps) or analysis is missing, and
  // while a bounds session holds an unsavable candidate.
  const isPreviewAvailable = isDraftValid && initProblem === null && !boundsEditorInvalid;
  const canSave = isDraftValid && initProblem === null && !isSaving;

  // Lyrics are the real recording's LRC, shown per component, used to infer
  // the Intro, and highlighted from the source time the transport plays.
  const lyrics = useSongLyrics(item.recording?.contentHash ?? undefined);
  const parsedLyrics = useMemo<LRCLine[] | null>(() => {
    if (lyrics.lrcContent === null) return null;
    return isValidLRC(lyrics.lrcContent) ? parseLRC(lyrics.lrcContent) : null;
  }, [lyrics.lrcContent]);

  /** Raw first sung LRC timestamp: the Intro end and the Reset reference. */
  const firstLrcTimeSeconds = useMemo<number | null>(() => {
    if (!parsedLyrics) return null;
    const line = parsedLyrics.find((entry) => entry.text.trim().length > 0);
    return line ? line.timeSeconds : null;
  }, [parsedLyrics]);

  const recordingDurationSeconds = item.recording?.durationSeconds ?? null;

  // A first lyric inside the first seconds is noise (or nothing to cut), so no
  // Intro is offered; the Recording duration bounds the inferred end.
  const inferredIntroEndSeconds = useMemo<number | null>(() => {
    if (firstLrcTimeSeconds === null || recordingDurationSeconds === null) return null;
    if (!(firstLrcTimeSeconds > INFERRED_INTRO_MIN_SECONDS)) return null;
    if (firstLrcTimeSeconds > recordingDurationSeconds) return null;
    return firstLrcTimeSeconds;
  }, [firstLrcTimeSeconds, recordingDurationSeconds]);

  // The picker is meaningful when analysis snapped a section or an Intro can
  // be inferred from the lyrics.
  const canAddSection =
    !isSaving &&
    draft.length < MAX_ARRANGEMENT_STEPS &&
    ((occurrences.length > 0 && snappedByIndex.size > 0) ||
      inferredIntroEndSeconds !== null);

  // A bounds candidate overrides only the auditioned Step; the draft, the
  // dirty flag, and every lyric block keep the committed bounds.
  const previewDraft = useMemo<DraftStep[]>(() => {
    if (!boundsPreviewOverride) return draft;
    return draft.map((entry) =>
      entry.id === boundsPreviewOverride.id
        ? { ...entry, step: boundsPreviewOverride.step }
        : entry
    );
  }, [boundsPreviewOverride, draft]);

  // The selected item previews the draft; siblings keep their saved values.
  // The previous sibling is the tail source and the next one the Opening
  // source, so both must stay in the input. Sibling analysis rides along so the
  // transport can validate every saved arrangement against its current
  // sections/beats: a stale sibling blocks preview instead of auditioning an
  // unrenderable mix.
  const previewItems = useMemo<TimelineItemSource[]>(() => {
    const draftArrangement = isDraftValid
      ? JSON.stringify(draftToArrangement(previewDraft))
      : null;
    return items.map((entry) => ({
      itemId: entry.id,
      recordingHashPrefix: entry.recording?.hashPrefix ?? null,
      arrangementSteps:
        entry.id === item.id ? draftArrangement : entry.arrangementSteps,
      gapBeats: entry.gapBeats,
      crossfadeEnabled: entry.crossfadeEnabled,
      crossfadeDurationSeconds: entry.crossfadeDurationSeconds ?? null,
      tempoBpm: entry.recording?.tempoBpm ?? null,
      recordingDurationSeconds: entry.recording?.durationSeconds ?? null,
      recordingSections: entry.recording?.sections ?? null,
      recordingBeats: entry.recording?.beats ?? [],
    }));
  }, [items, item.id, previewDraft, isDraftValid]);

  const timeline = useMemo(() => buildArrangementTimeline(previewItems), [previewItems]);
  const songTimelineItem = useMemo(
    () => timeline.items.find((entry) => entry.itemId === item.id) ?? null,
    [timeline, item.id]
  );

  const itemIndex = items.findIndex((entry) => entry.id === item.id);
  const previousItem = itemIndex > 0 ? items[itemIndex - 1] : null;
  const nextItem =
    itemIndex >= 0 && itemIndex + 1 < items.length ? items[itemIndex + 1] : null;
  const previousTitle = previousItem?.song?.title || t("browse.unknownSong");
  const nextSongTitle = nextItem?.song?.title || t("browse.unknownSong");
  const nextTimelineItem = useMemo(
    () =>
      nextItem
        ? timeline.items.find((entry) => entry.itemId === nextItem.id) ?? null
        : null,
    [nextItem, timeline]
  );
  const previousTimelineItem = useMemo(
    () =>
      previousItem
        ? timeline.items.find((entry) => entry.itemId === previousItem.id) ?? null
        : null,
    [previousItem, timeline]
  );

  // The previous song is context only through the label its final Step carries;
  // an untouched or unreadable sibling plays as "Full song".
  const previousFinalClipLabel = useMemo(() => {
    const parsed = parseSongArrangement(previousItem?.arrangementSteps ?? null);
    if (!parsed.present || "invalid" in parsed) return null;
    const steps = parsed.arrangement.steps;
    return steps.length > 0 ? steps[steps.length - 1].label : null;
  }, [previousItem]);

  const handlePreviewError = useCallback(
    (error: ArrangementPreviewError) => {
      setPreviewError(
        error.kind === "no-recording"
          ? t("arrangement.error.noRecording")
          : error.kind === "invalid-arrangement"
            ? t("arrangement.error.invalidArrangement")
            : t("arrangement.error.playbackFailed")
      );
    },
    [t]
  );

  const handleFallbackNotice = useCallback(() => {
    setFallbackNotice(true);
  }, []);

  const preview = useArrangementPreview({
    items: previewItems,
    active: isOpen && isPreviewAvailable,
    onPreviewError: handlePreviewError,
    onFallbackNotice: handleFallbackNotice,
  });

  // The arrangement preview window: every component, always with an 8-beat
  // lead-in from the previous item and an 8-beat lead-out into the next. Each
  // bookend is measured in the tempo of the song actually heard there, falling
  // back to this song's tempo, then 1 s/beat. A missing neighbour yields no
  // bookend, and a neighbour's own extent is the hard limit.
  const arrangementWindow = useMemo<ArrangementWindow | null>(() => {
    const clips = songTimelineItem?.clips ?? [];
    if (clips.length === 0) return null;
    const songStartSeconds = clips[0].startSeconds;
    const songEndSeconds = clips[clips.length - 1].endSeconds;
    let startSeconds = songStartSeconds;
    if (previousTimelineItem) {
      const leadIn =
        ARRANGEMENT_CONTEXT_BEATS *
        beatSeconds(previousItem?.recording?.tempoBpm ?? item.recording?.tempoBpm);
      startSeconds = Math.max(
        previousTimelineItem.startSeconds,
        songStartSeconds - leadIn
      );
    }
    let endSeconds = songEndSeconds;
    let actualOpeningSeconds = 0;
    if (nextTimelineItem && nextTimelineItem.clips.length > 0) {
      const leadOut =
        ARRANGEMENT_CONTEXT_BEATS *
        beatSeconds(nextItem?.recording?.tempoBpm ?? item.recording?.tempoBpm);
      const contextEnd = Math.min(
        nextTimelineItem.startSeconds + nextTimelineItem.durationSeconds,
        songEndSeconds + leadOut
      );
      if (contextEnd > songEndSeconds) {
        actualOpeningSeconds = contextEnd - nextTimelineItem.startSeconds;
        endSeconds = contextEnd;
      }
    }
    return {
      startSeconds,
      endSeconds,
      includesPreviousTail: startSeconds < songStartSeconds,
      includesNextOpening: endSeconds > songEndSeconds,
      actualOpeningSeconds,
    };
  }, [
    item.recording?.tempoBpm,
    nextItem?.recording?.tempoBpm,
    nextTimelineItem,
    previousItem?.recording?.tempoBpm,
    previousTimelineItem,
    songTimelineItem,
  ]);

  const position = preview.timelinePositionSeconds;
  // The strip always shows the whole arrangement window, never the shorter
  // review or audition window that currently governs playback.
  const windowDurationSeconds = arrangementWindow
    ? arrangementWindow.endSeconds - arrangementWindow.startSeconds
    : 0;
  const windowPositionSeconds = arrangementWindow
    ? Math.min(
        Math.max(position - arrangementWindow.startSeconds, 0),
        windowDurationSeconds
      )
    : 0;

  const blockLyrics = useMemo(
    () =>
      draft.map((entry) =>
        parsedLyrics
          ? linesForSourceRange(
              parsedLyrics,
              entry.step.startSeconds,
              entry.step.endSeconds,
              draft.map((other) => other.step)
            )
          : []
      ),
    [draft, parsedLyrics]
  );

  // Clips map 1:1 to draft steps, so the clip index is the component index.
  // Paused and scrubbed positions highlight too; only idle/error is neutral.
  const hasPlayerPosition =
    preview.status === "playing" || preview.status === "paused";
  const activeClipIndex =
    hasPlayerPosition && songTimelineItem
      ? songTimelineItem.clips.findIndex(
          (clip) => position >= clip.startSeconds && position < clip.endSeconds
        )
      : -1;
  const activeClip =
    activeClipIndex >= 0 ? songTimelineItem?.clips[activeClipIndex] ?? null : null;
  const activeLocalSeconds = activeClip
    ? activeClip.sourceStartSeconds + (position - activeClip.startSeconds)
    : null;
  const activeLineIndex =
    activeLocalSeconds === null
      ? -1
      : activeLyricIndex(blockLyrics[activeClipIndex] ?? [], activeLocalSeconds);

  // Exactly one automatic return per transition review (pass 1→2); pass 2 stops
  // the transport, so no further seek can happen. An audition and the whole
  // arrangement play once and pause. The guard ref keys on the window and pass,
  // so one boundary event is handled once even though the position keeps
  // ticking.
  const windowKey = playbackWindow
    ? `${playbackWindow.kind}:${playbackWindow.startSeconds}:${playbackWindow.endSeconds}`
    : null;

  useEffect(() => {
    if (!playbackWindow || !windowKey) return;
    if (preview.status !== "playing") return;
    if (position < playbackWindow.endSeconds - 0.02) return;
    const handled = boundaryHandledRef.current;
    if (handled && handled.key === windowKey && handled.pass === reviewPass) return;
    boundaryHandledRef.current = { key: windowKey, pass: reviewPass };
    if (playbackWindow.kind === "audition") {
      preview.pause();
      return;
    }
    if (playbackWindow.kind === "arrangement") {
      preview.pause();
      // eslint-disable-next-line react-hooks/set-state-in-effect -- the completion flag is the boundary's outcome, not derivable state
      setArrangementCompleted(true);
      return;
    }
    if (reviewPass < TRANSITION_REVIEW_PASSES) {
      // The transport position is an external event; the pass counter is the
      // render-visible consequence of reaching the boundary.
      setReviewPass(reviewPass + 1);
      preview.seek(playbackWindow.startSeconds);
    } else {
      preview.pause();
      setReviewCompleted(true);
    }
  }, [playbackWindow, preview, position, reviewPass, windowKey]);

  /**
   * A strip tap or key seek lands inside the arrangement window and always
   * hands transport control back to the arrangement window, so a scrub is never
   * governed by the 8-beat review end.
   */
  const seekWithinWindow = useCallback(
    (seconds: number) => {
      if (!arrangementWindow) return;
      const target = Math.min(
        Math.max(seconds, arrangementWindow.startSeconds),
        arrangementWindow.endSeconds
      );
      preview.seek(target);
      setReviewClipIndex(null);
      setReviewPass(1);
      setReviewCompleted(false);
      setArrangementCompleted(false);
      boundaryHandledRef.current = null;
      if (playbackWindow !== null) {
        setPlaybackWindow({ kind: "arrangement", ...arrangementWindow });
      }
    },
    [arrangementWindow, playbackWindow, preview]
  );

  /** Play the whole arrangement once from its window start. */
  const startArrangementPreview = useCallback(() => {
    if (isSaving || !isPreviewAvailable || boundsEditorId !== null || !arrangementWindow) {
      return;
    }
    setReviewClipIndex(null);
    setReviewPass(1);
    setReviewCompleted(false);
    setArrangementCompleted(false);
    boundaryHandledRef.current = null;
    setPreviewError(null);
    setPlaybackWindow({ kind: "arrangement", ...arrangementWindow });
    void preview.startAtSeconds(arrangementWindow.startSeconds).catch(() => {
      // The hook reports failures through onPreviewError.
    });
  }, [arrangementWindow, boundsEditorId, isPreviewAvailable, isSaving, preview]);

  /** Review the junction into component `index`: 4 beats each side, twice. */
  const startTransitionReview = useCallback(
    (index: number) => {
      if (isSaving || !isPreviewAvailable || boundsEditorId !== null) return;
      const win = buildTransitionWindow(
        songTimelineItem?.clips ?? [],
        index,
        item.recording?.tempoBpm ?? null
      );
      if (!win) return;
      setReviewClipIndex(index);
      setReviewPass(1);
      setReviewCompleted(false);
      setArrangementCompleted(false);
      boundaryHandledRef.current = null;
      setPreviewError(null);
      setPlaybackWindow({ kind: "review", ...win });
      void preview.startAtSeconds(win.startSeconds).catch(() => {
        // The hook reports failures through onPreviewError.
      });
    },
    [boundsEditorId, isPreviewAvailable, isSaving, item.recording?.tempoBpm, preview, songTimelineItem]
  );

  const handlePreviewToggle = useCallback(() => {
    if (
      !isPreviewAvailable ||
      !arrangementWindow ||
      boundsEditorId !== null ||
      preview.status === "loading" ||
      isSaving
    ) {
      return;
    }
    if (preview.status === "playing") {
      preview.pause();
      return;
    }
    // Resume a mid-window pause without restarting; anything at the window
    // boundary (or after completion/stop, or a paused transition review)
    // restarts the whole arrangement from its start.
    const resumable =
      playbackWindow?.kind === "arrangement" &&
      !arrangementCompleted &&
      (preview.status === "paused" || preview.status === "idle") &&
      position > arrangementWindow.startSeconds + 0.25 &&
      position < arrangementWindow.endSeconds - 0.25;
    if (resumable) {
      setPlaybackWindow({ kind: "arrangement", ...arrangementWindow });
      setPreviewError(null);
      void preview.play().catch(() => {
        // The hook reports failures through onPreviewError.
      });
      return;
    }
    startArrangementPreview();
  }, [
    arrangementCompleted,
    arrangementWindow,
    boundsEditorId,
    isPreviewAvailable,
    isSaving,
    playbackWindow,
    position,
    preview,
    startArrangementPreview,
  ]);

  const handleStop = useCallback(() => {
    preview.stop();
    setPlaybackWindow(null);
    setReviewClipIndex(null);
    setReviewPass(1);
    setReviewCompleted(false);
    setArrangementCompleted(false);
    boundaryHandledRef.current = null;
  }, [preview]);

  const handleStripPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || !arrangementWindow) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const bandStartAttr =
      (event.target as HTMLElement | null)
        ?.closest("[data-band-start-seconds]")
        ?.getAttribute("data-band-start-seconds") ?? null;
    stripGestureRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      wasPlaying: preview.status === "playing",
      bandStartSeconds: bandStartAttr === null ? null : Number(bandStartAttr),
      dragging: false,
      rectLeft: rect.left,
      rectWidth: rect.width,
    };
    // Down alone seeks nothing: a tap seeks to the band, a drag scrubs.
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };

  const handleStripPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const gesture = stripGestureRef.current;
    if (!gesture || gesture.pointerId !== event.pointerId || !arrangementWindow) return;
    const dx = event.clientX - gesture.startX;
    const dy = event.clientY - gesture.startY;
    if (!gesture.dragging) {
      if (
        Math.abs(dx) <= SCRUB_CLICK_TOLERANCE_PX &&
        Math.abs(dy) <= SCRUB_CLICK_TOLERANCE_PX
      ) {
        return;
      }
      // Leaving the tap tolerance is a scrub: the tap target is gone and
      // playback pauses instead of fighting the drag.
      gesture.dragging = true;
      gesture.bandStartSeconds = null;
      if (gesture.wasPlaying) preview.pause();
    }
    const fraction =
      gesture.rectWidth > 0
        ? Math.min(
            Math.max((event.clientX - gesture.rectLeft) / gesture.rectWidth, 0),
            1
          )
        : 0;
    seekWithinWindow(
      arrangementWindow.startSeconds +
        fraction * (arrangementWindow.endSeconds - arrangementWindow.startSeconds)
    );
  };

  const handleStripPointerUp = () => {
    const gesture = stripGestureRef.current;
    stripGestureRef.current = null;
    suppressStripClickRef.current = true;
    if (!gesture) return;
    if (!gesture.dragging) {
      if (gesture.bandStartSeconds !== null) {
        seekWithinWindow(gesture.bandStartSeconds);
      }
      return;
    }
    if (gesture.wasPlaying) {
      void preview.play().catch(() => {
        // The hook reports failures through onPreviewError.
      });
    }
  };

  const handleStripPointerCancel = () => {
    stripGestureRef.current = null;
    suppressStripClickRef.current = true;
  };

  const handleStripClickCapture = (event: React.MouseEvent<HTMLDivElement>) => {
    if (!suppressStripClickRef.current) return;
    suppressStripClickRef.current = false;
    event.preventDefault();
    event.stopPropagation();
  };

  const handleBandClick = (event: React.MouseEvent<HTMLButtonElement>) => {
    // Pointer taps are handled on pointer up; only keyboard activation
    // (`detail === 0`) needs the click path, so a drag never seeks twice.
    if (event.detail !== 0) return;
    const bandStart = event.currentTarget.dataset.bandStartSeconds;
    if (bandStart === undefined) return;
    seekWithinWindow(Number(bandStart));
  };

  const handlePlayheadKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (!arrangementWindow) return;
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      seekWithinWindow(position - 1);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      seekWithinWindow(position + 1);
    } else if (event.key === "Home") {
      event.preventDefault();
      seekWithinWindow(arrangementWindow.startSeconds);
    } else if (event.key === "End") {
      event.preventDefault();
      seekWithinWindow(arrangementWindow.endSeconds);
    }
  };

  const closeBoundsEditor = useCallback(() => {
    setBoundsEditorId(null);
    setBoundsPreviewOverride(null);
    setBoundsEditorInvalid(false);
  }, []);

  const openBoundsEditor = useCallback(
    (id: string) => {
      if (isSaving) return;
      preview.stop();
      setPlaybackWindow(null);
      setReviewClipIndex(null);
      setReviewPass(1);
      setReviewCompleted(false);
      setArrangementCompleted(false);
      boundaryHandledRef.current = null;
      setBoundsEditorId(id);
      setBoundsPreviewOverride(null);
      setBoundsEditorInvalid(false);
    },
    [isSaving, preview]
  );

  const handleBoundsCandidate = useCallback(
    (id: string, candidate: ArrangementStep | null) => {
      preview.stop();
      setPlaybackWindow(null);
      setReviewClipIndex(null);
      setReviewPass(1);
      setReviewCompleted(false);
      setArrangementCompleted(false);
      boundaryHandledRef.current = null;
      if (candidate === null) {
        // An unsavable candidate blocks preview: the strip and the audition
        // cannot claim a window the arrangement does not have.
        setBoundsPreviewOverride(null);
        setBoundsEditorInvalid(true);
        return;
      }
      setBoundsEditorInvalid(false);
      setBoundsPreviewOverride({ id, step: candidate });
    },
    [preview]
  );

  /** The raw Step Reset restores: detected bounds, or the inferred Intro end. */
  const resetStepFor = useCallback(
    (step: ArrangementStep): ArrangementStep | null => {
      if (step.sectionIndex === null) {
        if (firstLrcTimeSeconds === null) return null;
        return {
          sectionIndex: null,
          label: "intro",
          startSeconds: 0,
          endSeconds: firstLrcTimeSeconds,
          manualBounds: true,
        };
      }
      return snappedByIndex.get(step.sectionIndex) ?? null;
    },
    [firstLrcTimeSeconds, snappedByIndex]
  );

  // The audition plays inside the *candidate* clip, so what is heard is what
  // Apply would save.
  const auditionBounds = useCallback(
    (side: "start" | "end") => {
      if (boundsEditorId === null || boundsEditorInvalid) return;
      const index = draft.findIndex((entry) => entry.id === boundsEditorId);
      const clip = index >= 0 ? songTimelineItem?.clips[index] : undefined;
      if (!clip) return;
      const startSeconds =
        side === "start"
          ? clip.startSeconds
          : Math.max(clip.startSeconds, clip.endSeconds - BOUNDS_AUDITION_SECONDS);
      const endSeconds =
        side === "start"
          ? Math.min(clip.startSeconds + BOUNDS_AUDITION_SECONDS, clip.endSeconds)
          : clip.endSeconds;
      if (!(endSeconds > startSeconds)) return;
      setPreviewError(null);
      setPlaybackWindow({
        kind: "audition",
        startSeconds,
        endSeconds,
        includesPreviousTail: false,
        includesNextOpening: false,
        actualOpeningSeconds: 0,
      });
      boundaryHandledRef.current = null;
      setReviewCompleted(false);
      void preview.startAtSeconds(startSeconds).catch(() => {
        // The hook reports failures through onPreviewError.
      });
    },
    [boundsEditorId, boundsEditorInvalid, draft, preview, songTimelineItem]
  );

  /**
   * Any draft mutation ends the reviewed junction, the pass counter, and the
   * boundary guard: the transport is stopped, so the rail must return to the
   * arrangement preview rather than claim a review that is no longer playing.
   */
  const resetReview = useCallback(() => {
    setReviewClipIndex(null);
    setReviewPass(1);
    setReviewCompleted(false);
    setArrangementCompleted(false);
    boundaryHandledRef.current = null;
  }, []);

  const commitDraft = useCallback(
    (next: DraftStep[], recordHistory = true) => {
      // A pending save owns the draft: drag, duplicate, and remove must not
      // mutate it underneath the request that is already in flight.
      if (isSaving) return;
      preview.stop();
      setPlaybackWindow(null);
      setPreviewError(null);
      setFallbackNotice(false);
      setDraftState({ draft: next, problem: validateDraftProblem(next, item) });
      if (recordHistory) {
        setHistory((entries) => [...entries.slice(-(HISTORY_LIMIT - 1)), draft]);
      }
      resetReview();
      closeBoundsEditor();
      setIsDirty(draftKey(next) !== draftKey(initialDraftRef.current));
    },
    [closeBoundsEditor, draft, isSaving, item, preview, resetReview]
  );

  const applyBounds = useCallback(
    (id: string, nextStep: ArrangementStep) => {
      const index = draft.findIndex((entry) => entry.id === id);
      if (index < 0) {
        closeBoundsEditor();
        return;
      }
      const next = draft.map((entry, i) =>
        i === index ? { ...entry, step: nextStep } : entry
      );
      commitDraft(next);
    },
    [closeBoundsEditor, commitDraft, draft]
  );

  const undo = useCallback(() => {
    if (isSaving || history.length === 0) return;
    preview.stop();
    setPlaybackWindow(null);
    setPreviewError(null);
    setFallbackNotice(false);
    const previous = history[history.length - 1];
    setHistory(history.slice(0, -1));
    setDraftState({
      draft: previous,
      problem: validateDraftProblem(previous, item),
    });
    resetReview();
    closeBoundsEditor();
    setIsDirty(draftKey(previous) !== draftKey(initialDraftRef.current));
  }, [closeBoundsEditor, history, isSaving, item, preview, resetReview]);

  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || event.shiftKey) return;
      if (event.key.toLowerCase() !== "z") return;
      const target = event.target as HTMLElement | null;
      if (
        target &&
        (target.isContentEditable ||
          /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))
      ) {
        return;
      }
      event.preventDefault();
      undo();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, undo]);

  const addSection = useCallback(
    (sectionIndex: number) => {
      const snapped = snappedByIndex.get(sectionIndex);
      if (!snapped) return;
      commitDraft([...draft, createDraftStep(snapped)]);
    },
    [commitDraft, draft, snappedByIndex]
  );

  const handleAddFromDialog = useCallback(
    (sectionIndex: number) => {
      const snapped = snappedByIndex.get(sectionIndex);
      if (!snapped || isSaving || draft.length >= MAX_ARRANGEMENT_STEPS) return;
      addSection(sectionIndex);
      setAddDialogOpen(false);
    },
    [addSection, draft.length, isSaving, snappedByIndex]
  );

  /**
   * Append the LRC-inferred Intro: a Songset-local cut from zero to the first
   * sung line, flagged manual so later lyric edits can never stale it.
   */
  const addInferredIntro = useCallback(() => {
    if (inferredIntroEndSeconds === null || isSaving || draft.length >= MAX_ARRANGEMENT_STEPS) {
      return;
    }
    commitDraft([
      ...draft,
      createDraftStep({
        sectionIndex: null,
        label: "intro",
        startSeconds: 0,
        endSeconds: inferredIntroEndSeconds,
        manualBounds: true,
      }),
    ]);
    setAddDialogOpen(false);
  }, [commitDraft, draft, inferredIntroEndSeconds, isSaving]);

  const duplicateStep = useCallback(
    (id: string) => {
      const index = draft.findIndex((entry) => entry.id === id);
      if (index < 0) return;
      const next = [...draft];
      next.splice(index + 1, 0, createDraftStep(draft[index].step));
      commitDraft(next);
    },
    [commitDraft, draft]
  );

  const removeStep = useCallback(
    (id: string) => {
      commitDraft(draft.filter((entry) => entry.id !== id));
    },
    [commitDraft, draft]
  );

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  );

  const handleDragEnd = useCallback(
    (event: DragEndEvent) => {
      const { active, over } = event;
      if (!over || active.id === over.id) return;
      const oldIndex = draft.findIndex((entry) => entry.id === active.id);
      const newIndex = draft.findIndex((entry) => entry.id === over.id);
      if (oldIndex < 0 || newIndex < 0) return;
      commitDraft(arrayMove(draft, oldIndex, newIndex));
    },
    [commitDraft, draft]
  );

  const savedSteps = useMemo(() => (isDraftValid ? draftSteps : null), [
    isDraftValid,
    draftSteps,
  ]);

  const handleRequestClose = useCallback(
    (open: boolean) => {
      if (open) {
        onOpenChange(true);
        return;
      }
      // A pending save decides the sheet's fate: it closes on success and
      // stays open on failure, so a close request mid-save is ignored.
      if (isSaving) return;
      setAddDialogOpen(false);
      if (isDirty) {
        setDirtyDialogOpen(true);
        return;
      }
      closeBoundsEditor();
      preview.stop();
      onOpenChange(false);
    },
    [closeBoundsEditor, isDirty, isSaving, onOpenChange, preview]
  );

  const handleDiscard = useCallback(() => {
    setAddDialogOpen(false);
    setDirtyDialogOpen(false);
    setIsDirty(false);
    closeBoundsEditor();
    preview.stop();
    onOpenChange(false);
  }, [closeBoundsEditor, onOpenChange, preview]);

  const handleSave = useCallback(async () => {
    if (!savedSteps || isSaving) return;
    setIsSaving(true);
    setSaveError(null);
    preview.stop();
    closeBoundsEditor();
    try {
      setAddDialogOpen(false);
      await onSave(draftToArrangement(draft));
      setIsDirty(false);
      onOpenChange(false);
    } catch (error) {
      setSaveError(
        error instanceof Error && error.message
          ? error.message
          : t("arrangement.error.saveFailed")
      );
    } finally {
      setIsSaving(false);
    }
  }, [closeBoundsEditor, draft, isSaving, onOpenChange, onSave, preview, savedSteps, t]);

  const handleReset = useCallback(async () => {
    if (isSaving) return;
    setIsSaving(true);
    setSaveError(null);
    preview.stop();
    closeBoundsEditor();
    try {
      await onSave(null);
      setIsDirty(false);
      setResetConfirmOpen(false);
      onOpenChange(false);
    } catch (error) {
      setSaveError(
        error instanceof Error && error.message
          ? error.message
          : t("arrangement.error.saveFailed")
      );
      setResetConfirmOpen(false);
    } finally {
      setIsSaving(false);
    }
  }, [closeBoundsEditor, isSaving, onOpenChange, onSave, preview, t]);

  const originalDurationSeconds = item.recording?.durationSeconds ?? null;
  const showFallbackNotice = preview.isFallbackMode || fallbackNotice;
  const songTitle = item.song?.title || t("browse.unknownSong");

  const metaParts: string[] = [];
  if (item.song?.musicalKey) {
    metaParts.push(
      t("arrangement.meta.key").replace("{key}", item.song.musicalKey)
    );
  }
  if (item.recording?.tempoBpm) {
    metaParts.push(
      t("arrangement.meta.tempo").replace(
        "{bpm}",
        String(Math.round(item.recording.tempoBpm))
      )
    );
  }
  metaParts.push(
    `${t("arrangement.duration.arrangedLabel")} ${formatDuration(draftDurationSeconds)}`
  );
  metaParts.push(
    `${t("arrangement.duration.originalLabel")} ${
      originalDurationSeconds != null
        ? formatDuration(originalDurationSeconds)
        : "—"
    }`
  );

  // Whole-timeline position labels: a crossfade names both songs/components,
  // so no copy can claim one song owns the audible moment.
  const savedStepsByItemId = useMemo(() => {
    const map = new Map<string, ArrangementStep[] | null>();
    for (const entry of items) {
      const parsed = parseSongArrangement(entry.arrangementSteps ?? null);
      map.set(
        entry.id,
        parsed.present && !("invalid" in parsed) ? parsed.arrangement.steps : null
      );
    }
    return map;
  }, [items]);

  const activeRefs = useMemo(
    () => activeClipRefs(timeline, position),
    [timeline, position]
  );

  const statusLabel = useMemo(() => {
    if (!hasPlayerPosition || activeRefs.length === 0) return "";
    const songTitleFor = (itemId: string) =>
      items.find((entry) => entry.id === itemId)?.song?.title ||
      t("browse.unknownSong");
    const labelFor = (ref: ActiveClipRef) => {
      if (ref.item.itemId === item.id) return labelForDraftIndex(ref.clipIndex) ?? "";
      const saved = savedStepsByItemId.get(ref.item.itemId) ?? null;
      if (saved) {
        return saved[ref.clipIndex]?.label ?? t("arrangement.preview.opening");
      }
      return ref.item.itemId === nextItem?.id
        ? t("arrangement.preview.opening")
        : t("arrangement.preview.fullSong");
    };
    if (activeRefs.length >= 2) {
      const [first, second] = activeRefs;
      return t("arrangement.preview.crossfade")
        .replace("{fromSong}", songTitleFor(first.item.itemId))
        .replace("{fromLabel}", labelFor(first))
        .replace("{toSong}", songTitleFor(second.item.itemId))
        .replace("{toLabel}", labelFor(second));
    }
    const [only] = activeRefs;
    return (preview.status === "playing"
      ? t("arrangement.preview.playingAt")
      : t("arrangement.preview.pausedAt")
    )
      .replace("{song}", songTitleFor(only.item.itemId))
      .replace("{label}", labelFor(only));
  }, [
    activeRefs,
    hasPlayerPosition,
    item.id,
    items,
    labelForDraftIndex,
    nextItem?.id,
    preview.status,
    savedStepsByItemId,
    t,
  ]);

  // The rail always names what will play: every component, plus the
  // neighbouring-song bookends when they exist.
  const arrangementSummary = useMemo(
    () =>
      arrangementWindow
        ? t("arrangement.preview.arrangementSummary").replace(
            "{count}",
            String(draft.length)
          )
        : null,
    [arrangementWindow, draft.length, t]
  );

  // The lead-in line names both sides of the hand-over as it will actually
  // play; a neighbour with no readable saved arrangement plays whole.
  const previousHandover = useMemo(() => {
    if (!arrangementWindow?.includesPreviousTail) return null;
    return t("arrangement.preview.handover")
      .replace("{fromSong}", previousTitle)
      .replace(
        "{fromLabel}",
        previousFinalClipLabel ?? t("arrangement.preview.fullSong")
      )
      .replace("{toSong}", songTitle)
      .replace("{toLabel}", labelForDraftIndex(0) ?? "");
  }, [
    arrangementWindow,
    labelForDraftIndex,
    previousFinalClipLabel,
    previousTitle,
    songTitle,
    t,
  ]);

  // Strip bands always follow the arrangement window, so every component is
  // visible before playback; a review window is drawn as an overlay on top.
  const stripBands = useMemo<StripBand[]>(() => {
    if (!arrangementWindow) return [];
    const windowStart = arrangementWindow.startSeconds;
    const windowEnd = arrangementWindow.endSeconds;
    const bands: StripBand[] = [];
    const clips = songTimelineItem?.clips ?? [];
    const songStart = clips[0]?.startSeconds ?? windowStart;
    if (arrangementWindow.includesPreviousTail) {
      const end = Math.min(songStart, windowEnd);
      if (end > windowStart) {
        bands.push({
          key: "previous",
          kind: "previous",
          clipIndex: null,
          label: previousTitle,
          startSeconds: windowStart,
          endSeconds: end,
        });
      }
    }
    clips.forEach((clip, index) => {
      const start = Math.max(clip.startSeconds, windowStart);
      const end = Math.min(clip.endSeconds, windowEnd);
      if (!(end > start)) return;
      bands.push({
        key: `clip-${index}`,
        kind: "step",
        clipIndex: index,
        label: labelForDraftIndex(index) ?? "",
        startSeconds: start,
        endSeconds: end,
      });
    });
    if (arrangementWindow.includesNextOpening && nextTimelineItem) {
      const start = Math.max(nextTimelineItem.startSeconds, windowStart);
      if (windowEnd > start) {
        bands.push({
          key: "next",
          kind: "next",
          clipIndex: null,
          label: `${nextSongTitle} · ${t("arrangement.preview.opening")}`,
          startSeconds: start,
          endSeconds: windowEnd,
        });
      }
    }
    return bands;
  }, [
    arrangementWindow,
    labelForDraftIndex,
    nextSongTitle,
    nextTimelineItem,
    previousTitle,
    songTimelineItem,
    t,
  ]);

  // The transition review draws the 8-beat window it will play on the strip,
  // so the reduced scope of the component action is visible, not implied.
  const reviewRegionStyle = useMemo(() => {
    if (reviewClipIndex === null || !arrangementWindow || windowDurationSeconds <= 0) {
      return null;
    }
    const win = buildTransitionWindow(
      songTimelineItem?.clips ?? [],
      reviewClipIndex,
      item.recording?.tempoBpm ?? null
    );
    if (!win) return null;
    const toPercent = (seconds: number) =>
      ((seconds - arrangementWindow.startSeconds) / windowDurationSeconds) * 100;
    return {
      left: `${toPercent(win.startSeconds)}%`,
      width: `${toPercent(win.endSeconds) - toPercent(win.startSeconds)}%`,
    };
  }, [
    arrangementWindow,
    item.recording?.tempoBpm,
    reviewClipIndex,
    songTimelineItem,
    windowDurationSeconds,
  ]);

  const activeBandKey =
    hasPlayerPosition
      ? stripBands.find(
          (band) => position >= band.startSeconds && position < band.endSeconds
        )?.key ?? null
      : null;

  return (
    <>
      <Sheet open={isOpen} onOpenChange={handleRequestClose}>
        <SheetContent
          side="bottom"
          className="gap-0 overflow-hidden p-0 data-[side=bottom]:!h-[94dvh] md:data-[side=bottom]:!h-[90vh]"
          data-testid="arrangement-sheet"
        >
          <SheetHeader className="shrink-0 gap-0 border-b px-4 py-3 pr-12 text-left sm:px-5">
            <SheetTitle className="font-serif text-xl leading-tight font-semibold">
              {songTitle}
            </SheetTitle>
            <SheetDescription className="mt-0.5 text-xs text-muted-foreground">
              {metaParts.join(" · ")}
            </SheetDescription>
            <div className="mt-2 flex flex-wrap items-center gap-1">
              <span
                className="mr-auto inline-flex items-center gap-1.5 text-[11px] text-muted-foreground"
                data-testid="arrangement-status"
              >
                <span
                  aria-hidden="true"
                  className={cn(
                    "size-1.5 rounded-full",
                    isDirty ? "bg-amber-500" : "bg-primary"
                  )}
                />
                {isDirty
                  ? t("arrangement.status.unsaved")
                  : t("arrangement.status.saved")}
              </span>
              <Button
                variant="ghost"
                size="sm"
                onClick={undo}
                disabled={history.length === 0 || isSaving}
                aria-label={t("arrangement.action.undoAria")}
                data-testid="arrangement-undo"
              >
                <Undo2 className="size-3.5" />
                <span className="hidden sm:inline">{t("arrangement.action.undo")}</span>
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setResetConfirmOpen(true)}
                disabled={isSaving}
                aria-label={t("arrangement.action.reset")}
                data-testid="arrangement-reset"
              >
                <RotateCcw className="size-3.5" />
                <span className="hidden sm:inline">{t("arrangement.action.reset")}</span>
              </Button>
            </div>
          </SheetHeader>

          <div className="flex min-h-0 flex-1 flex-col">
            <div className="min-h-0 flex-1 overflow-y-auto">
              <div className="lg:grid lg:grid-cols-[minmax(0,1fr)_19rem] lg:items-start">
                {/* Score column: components and their lyrics. */}
                <div className="min-w-0 px-4 py-3 sm:px-5">
                  {initProblem !== null && (
                    <Alert variant="destructive" className="mb-3">
                      <AlertTriangle className="size-4" />
                      <AlertDescription>
                        {initProblem === "invalid"
                          ? t("arrangement.error.invalidArrangement")
                          : initProblem === "stale"
                            ? t("arrangement.error.unavailableOrStale")
                            : t("arrangement.disabled.noAnalysis")}
                      </AlertDescription>
                    </Alert>
                  )}

                  <div className="mb-3 flex items-center justify-between gap-2">
                    <p
                      className="text-xs text-muted-foreground"
                      data-testid="arrangement-component-count"
                    >
                      <span className="font-mono text-sm text-foreground">
                        {draft.length}
                      </span>{" "}
                      {t("arrangement.draft.countLabel")}
                    </p>
                    <div className="flex shrink-0 items-center gap-2">
                      <span className="font-mono text-[10px] text-muted-foreground/70">
                        {draft.length}/{MAX_ARRANGEMENT_STEPS}
                      </span>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-8 text-xs"
                        onClick={() => setAddDialogOpen(true)}
                        disabled={!canAddSection}
                        data-testid="arrangement-add-section"
                      >
                        <Plus className="size-3.5" />
                        {t("arrangement.action.addSection")}
                      </Button>
                    </div>
                  </div>

                  <p className="mb-2 text-[11px] leading-relaxed text-muted-foreground">
                    {t("arrangement.note.reorderHint")}
                  </p>

                  {/* Draft sequence: one bounded block per component. */}
                  {draft.length === 0 ? (
                    <div className="rounded-xl border border-dashed px-6 py-8 text-center">
                      <p className="mx-auto max-w-[38ch] text-sm leading-relaxed text-muted-foreground">
                        {t("arrangement.draft.empty")}
                      </p>
                    </div>
                  ) : (
                    <DndContext
                      id={dndContextId}
                      sensors={sensors}
                      collisionDetection={closestCenter}
                      onDragEnd={handleDragEnd}
                    >
                      <SortableContext
                        items={draft.map((entry) => entry.id)}
                        strategy={verticalListSortingStrategy}
                      >
                        <ol
                          role="list"
                          aria-label={t("arrangement.section.draft")}
                          className="space-y-2.5"
                        >
                          {draft.map((entry, index) => (
                            <SortableStepBlock
                              key={entry.id}
                              entry={entry}
                              index={index}
                              displayLabel={labelForDraftIndex(index) ?? ""}
                              nextLabel={
                                index + 1 < draft.length
                                  ? labelForDraftIndex(index + 1) ?? ""
                                  : ""
                              }
                              isLast={index === draft.length - 1}
                              lines={blockLyrics[index] ?? []}
                              lyricsLoading={lyrics.loading}
                              lyricsAvailable={parsedLyrics !== null}
                              activeLineIndex={
                                index === activeClipIndex ? activeLineIndex : -1
                              }
                              isActive={index === activeClipIndex}
                              isPlaying={
                                preview.status === "playing" &&
                                index === activeClipIndex
                              }
                              disabled={isSaving || boundsEditorId !== null}
                              boundsDisabled={isSaving}
                              boundsOpen={boundsEditorId === entry.id}
                              recordingDurationSeconds={recordingDurationSeconds}
                              resetStep={resetStepFor(entry.step)}
                              onOpenBounds={openBoundsEditor}
                              onCloseBounds={closeBoundsEditor}
                              onBoundsCandidate={handleBoundsCandidate}
                              onBoundsApply={applyBounds}
                              onAuditionBounds={auditionBounds}
                              onDuplicate={duplicateStep}
                              onRemove={removeStep}
                              onReviewTransition={startTransitionReview}
                            />
                          ))}
                        </ol>
                      </SortableContext>
                    </DndContext>
                  )}

                  {draft.length === 0 && (
                    <p
                      className="mt-2 text-xs text-amber-600"
                      data-testid="arrangement-empty-error"
                    >
                      {t("arrangement.validation.empty")}
                    </p>
                  )}
                  {tooManySteps && (
                    <p
                      className="mt-2 text-xs text-amber-600"
                      data-testid="arrangement-too-many-error"
                    >
                      {t("arrangement.validation.tooMany").replace(
                        "{max}",
                        String(MAX_ARRANGEMENT_STEPS)
                      )}
                    </p>
                  )}

                  <p className="mt-4 text-[11px] leading-relaxed text-muted-foreground">
                    {t("arrangement.note.saveBehavior")}
                  </p>
                </div>

                {/* Preview rail: the arrangement preview (every component plus
                    the 8-beat neighbouring-song bookends) and the transition
                    review of the junction selected on a component row. */}
                <aside
                  aria-label={t("arrangement.preview.title")}
                  className="border-t px-4 py-4 sm:px-5 lg:sticky lg:top-0 lg:border-t-0 lg:border-l"
                >
                  <div className="space-y-3">
                    <div className="flex items-baseline justify-between gap-2">
                      <h3 className="font-serif text-base font-semibold">
                        {t("arrangement.preview.title")}
                      </h3>
                      <span className="font-mono text-[11px] tabular-nums text-muted-foreground">
                        {formatTimecode(windowDurationSeconds)}
                      </span>
                    </div>

                    {isPreviewAvailable && arrangementWindow && (
                      <p
                        className="text-[11px] leading-relaxed text-muted-foreground"
                        data-testid="arrangement-preview-summary"
                      >
                        {arrangementSummary}
                      </p>
                    )}

                    {isPreviewAvailable && arrangementWindow && previousHandover && (
                      <p className="text-[11px] leading-relaxed text-muted-foreground">
                        {previousHandover}
                      </p>
                    )}

                    {isPreviewAvailable && arrangementWindow?.includesNextOpening && (
                      <p
                        className="text-[11px] leading-relaxed text-muted-foreground"
                        data-testid="arrangement-next-opening-notice"
                      >
                        {t("arrangement.preview.nextOpening")
                          .replace("{song}", nextSongTitle)
                          .replace(
                            "{seconds}",
                            String(Math.round(arrangementWindow.actualOpeningSeconds))
                          )}
                      </p>
                    )}

                    {isPreviewAvailable && arrangementWindow && reviewClipIndex !== null && (
                      <div className="space-y-1">
                        <p className="text-[11px] leading-relaxed text-muted-foreground">
                          {t("arrangement.preview.pairSummary")
                            .replace(
                              "{fromLabel}",
                              labelForDraftIndex(reviewClipIndex) ?? ""
                            )
                            .replace(
                              "{toLabel}",
                              labelForDraftIndex(reviewClipIndex + 1) ?? ""
                            )}
                        </p>
                        <p className="text-[11px] leading-relaxed text-muted-foreground">
                          {t("arrangement.preview.reviewWindow").replaceAll(
                            "{beats}",
                            String(TRANSITION_REVIEW_BEATS)
                          )}
                        </p>
                        <p
                          className="font-mono text-[11px] tabular-nums text-primary"
                          data-testid="arrangement-preview-pass"
                        >
                          {reviewCompleted
                            ? t("arrangement.preview.completed")
                            : t("arrangement.preview.pass").replace(
                                "{current}",
                                String(reviewPass)
                              )}
                        </p>
                      </div>
                    )}

                    {isPreviewAvailable && arrangementWindow && windowDurationSeconds > 0 && (
                      <div
                        role="group"
                        aria-label={t("arrangement.preview.timelineAria")}
                        data-testid="arrangement-preview-timeline"
                        className="relative h-12 touch-none overflow-hidden rounded-lg border border-border/70 bg-muted/40 select-none"
                        onPointerDown={handleStripPointerDown}
                        onPointerMove={handleStripPointerMove}
                        onPointerUp={handleStripPointerUp}
                        onPointerCancel={handleStripPointerCancel}
                        onClickCapture={handleStripClickCapture}
                      >
                        {stripBands.map((band) => {
                          const width =
                            ((band.endSeconds - band.startSeconds) /
                              windowDurationSeconds) *
                            100;
                          const style = {
                            left: `${
                              ((band.startSeconds - arrangementWindow.startSeconds) /
                                windowDurationSeconds) *
                              100
                            }%`,
                            width: `calc(${width}% - 2px)`,
                          };
                          const bandClass = cn(
                            "absolute inset-y-1.5 flex min-w-0 items-center overflow-hidden rounded-[3px]",
                            band.key === activeBandKey
                              ? "bg-primary/70"
                              : band.kind === "step"
                                ? "bg-primary/25"
                                : "bg-foreground/15"
                          );
                          // Every band is a tap target: a tap seeks there, so a
                          // context band is as reachable as a component band.
                          return (
                            <button
                              type="button"
                              key={band.key}
                              data-band-start-seconds={band.startSeconds}
                              data-clip-index={band.clipIndex ?? undefined}
                              onClick={handleBandClick}
                              className={cn(bandClass, "text-left")}
                              style={style}
                            >
                              <span className="truncate px-1 text-[10px]">
                                {band.label}
                              </span>
                              {band.key === activeBandKey && (
                                <div
                                  aria-hidden="true"
                                  className="absolute inset-y-0 left-0 bg-primary/60"
                                  style={{
                                    width: `${
                                      ((position - band.startSeconds) /
                                        (band.endSeconds - band.startSeconds)) *
                                      100
                                    }%`,
                                  }}
                                />
                              )}
                            </button>
                          );
                        })}
                        {reviewRegionStyle && (
                          <div
                            aria-hidden="true"
                            data-testid="arrangement-transition-region"
                            className="pointer-events-none absolute inset-y-1 rounded-[3px] ring-2 ring-primary/70"
                            style={reviewRegionStyle}
                          />
                        )}
                        <div
                          role="slider"
                          tabIndex={0}
                          aria-label={t("arrangement.preview.timelineAria")}
                          aria-valuemin={0}
                          aria-valuemax={Math.round(windowDurationSeconds)}
                          aria-valuenow={Math.round(windowPositionSeconds)}
                          aria-valuetext={`${formatTimecode(windowPositionSeconds)} / ${formatTimecode(windowDurationSeconds)}`}
                          data-testid="arrangement-preview-playhead"
                          onKeyDown={handlePlayheadKeyDown}
                          className="absolute inset-y-0 w-2 -translate-x-1/2 cursor-ew-resize bg-foreground/80 outline-none focus-visible:ring-2 focus-visible:ring-ring"
                          style={{
                            left: `${
                              windowDurationSeconds > 0
                                ? (windowPositionSeconds / windowDurationSeconds) * 100
                                : 0
                            }%`,
                          }}
                        />
                      </div>
                    )}

                    <div className="flex items-center gap-2">
                      <Button
                        size="sm"
                        className="h-11 flex-1"
                        onClick={handlePreviewToggle}
                        disabled={
                          !isPreviewAvailable ||
                          !arrangementWindow ||
                          boundsEditorId !== null ||
                          preview.status === "loading" ||
                          isSaving
                        }
                        aria-label={
                          preview.status === "playing"
                            ? t("arrangement.preview.pause")
                            : t("arrangement.preview.play")
                        }
                        data-testid="arrangement-preview-play"
                      >
                        {preview.status === "loading" ? (
                          <Loader2 className="size-3.5 animate-spin" />
                        ) : preview.status === "playing" ? (
                          <Pause className="size-3.5" />
                        ) : (
                          <Play className="size-3.5" />
                        )}
                        {preview.status === "loading"
                          ? t("arrangement.preview.loading")
                          : preview.status === "playing"
                            ? t("arrangement.preview.pause")
                            : t("arrangement.preview.play")}
                      </Button>
                      <Button
                        variant="outline"
                        size="icon"
                        className="h-11 min-w-11"
                        onClick={handleStop}
                        disabled={preview.status === "idle" || boundsEditorId !== null}
                        aria-label={t("arrangement.preview.stop")}
                        data-testid="arrangement-preview-stop"
                      >
                        <Square className="size-3.5" />
                      </Button>
                    </div>

                    <div className="flex items-baseline justify-between gap-2 font-mono text-[11px] tabular-nums text-muted-foreground">
                      <span>
                        {formatTimecode(windowPositionSeconds)} /{" "}
                        {formatTimecode(windowDurationSeconds)}
                      </span>
                      <span
                        role="status"
                        aria-live="polite"
                        className="truncate text-right"
                        data-testid="arrangement-preview-phase"
                      >
                        {statusLabel}
                      </span>
                    </div>

                    {showFallbackNotice && (
                      <p
                        className="text-[11px] leading-relaxed text-amber-600"
                        data-testid="arrangement-fallback-notice"
                      >
                        {t("arrangement.preview.fallbackNotice")}
                      </p>
                    )}
                    {previewError && (
                      <Alert variant="destructive" data-testid="arrangement-preview-error">
                        <AlertTriangle className="size-4" />
                        <AlertDescription>{previewError}</AlertDescription>
                      </Alert>
                    )}
                  </div>
                </aside>
              </div>
            </div>

            <footer className="shrink-0 border-t px-4 py-3 sm:px-5">
              {saveError && (
                <Alert
                  variant="destructive"
                  className="mb-2"
                  data-testid="arrangement-save-error"
                >
                  <AlertTriangle className="size-4" />
                  <AlertDescription>{saveError}</AlertDescription>
                </Alert>
              )}
              <div className="flex items-center justify-end gap-2">
                <Button
                  variant="ghost"
                  onClick={() => handleRequestClose(false)}
                  disabled={isSaving}
                >
                  {t("arrangement.action.cancel")}
                </Button>
                <Button
                  onClick={() => void handleSave()}
                  disabled={!canSave}
                  data-testid="arrangement-save"
                >
                  {isSaving ? (
                    <>
                      <Loader2 className="size-4 animate-spin" />
                      {t("arrangement.action.saving")}
                    </>
                  ) : (
                    <>
                      <ListMusic className="size-4" />
                      {t("arrangement.action.save")}
                    </>
                  )}
                </Button>
              </div>
            </footer>

            <Dialog
              open={addDialogOpen}
              onOpenChange={(open) => setAddDialogOpen(open)}
            >
              <DialogContent
                className="sm:max-w-md"
                data-testid="arrangement-add-dialog"
              >
                <DialogHeader>
                  <DialogTitle>{t("arrangement.addDialog.title")}</DialogTitle>
                  <DialogDescription>
                    {t("arrangement.addDialog.description")}
                  </DialogDescription>
                </DialogHeader>
                <div className="flex max-h-[min(52vh,380px)] flex-col gap-1.5 overflow-y-auto overscroll-contain pr-0.5">
                  {lyrics.loading && (
                    <div className="flex h-11 w-full items-center gap-2 px-3 text-[13px] text-muted-foreground">
                      <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
                      {t("arrangement.lyrics.loading")}
                    </div>
                  )}
                  {!lyrics.loading && inferredIntroEndSeconds !== null && (
                    <Button
                      variant="outline"
                      className="h-11 w-full justify-start gap-2.5 px-3 text-left"
                      onClick={addInferredIntro}
                      disabled={isSaving || draft.length >= MAX_ARRANGEMENT_STEPS}
                      data-testid="arrangement-add-inferred-intro"
                    >
                      <span className="min-w-0 flex-1 truncate text-[13px] font-semibold">
                        {t("arrangement.intro.inferred")}
                      </span>
                      <span className="shrink-0 font-mono text-[10.5px] text-muted-foreground">
                        {t("arrangement.intro.firstLyric").replace(
                          "{timecode}",
                          formatTimecodeTenths(inferredIntroEndSeconds)
                        )}
                      </span>
                      <Plus className="size-4 text-primary" />
                    </Button>
                  )}
                  {occurrences.map((occurrence) => {
                    const snapped = snappedByIndex.get(occurrence.index);
                    const count = stepCountBySectionIndex.get(occurrence.index) ?? 0;
                    return (
                      <Button
                        key={`${occurrence.index}-${occurrence.section.label}`}
                        variant="outline"
                        className="h-11 w-full justify-start gap-2.5 px-3 text-left"
                        onClick={() => handleAddFromDialog(occurrence.index)}
                        disabled={
                          !snapped ||
                          isSaving ||
                          draft.length >= MAX_ARRANGEMENT_STEPS
                        }
                        aria-label={t("arrangement.addDialog.rowAria").replace(
                          "{label}",
                          occurrence.displayLabel
                        )}
                        data-testid={`arrangement-add-${occurrence.index}`}
                      >
                        <span className="min-w-0 flex-1 truncate text-[13px] font-semibold">
                          {occurrence.displayLabel}
                        </span>
                        {snapped && (
                          <span className="shrink-0 font-mono text-[10.5px] text-muted-foreground">
                            {formatTimecode(
                              snapped.endSeconds - snapped.startSeconds
                            )}
                          </span>
                        )}
                        {count > 0 && (
                          <span className="shrink-0 text-[10.5px] text-muted-foreground">
                            {t("arrangement.addDialog.rowCount").replace(
                              "{count}",
                              String(count)
                            )}
                          </span>
                        )}
                        <Plus className="size-4 text-primary" />
                      </Button>
                    );
                  })}
                </div>
              </DialogContent>
            </Dialog>
          </div>
        </SheetContent>
      </Sheet>

      {/* Unsaved-changes confirmation */}
      <AlertDialog open={dirtyDialogOpen} onOpenChange={setDirtyDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("arrangement.dialog.dirtyTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("arrangement.dialog.dirtyDescription")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("arrangement.dialog.keepEditing")}</AlertDialogCancel>
            <AlertDialogAction onClick={handleDiscard}>
              {t("arrangement.dialog.discard")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Reset-to-NULL confirmation */}
      <AlertDialog open={resetConfirmOpen} onOpenChange={setResetConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("arrangement.dialog.resetTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("arrangement.dialog.resetDescription")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("arrangement.action.cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={() => void handleReset()}>
              {t("arrangement.dialog.resetConfirm")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
