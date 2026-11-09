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
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Slider } from "@/components/ui/slider";
import { cn } from "@/lib/utils";
import { formatDuration } from "@/lib/format";
import { useLocale } from "@/hooks/useLocale";
import { useSongLyrics } from "@/hooks/useSongLyrics";
import { useArrangementPreview } from "@/hooks/useArrangementPreview";
import type { ArrangementPreviewError } from "@/lib/audio/arrangement-preview";
import { isValidLRC, parseLRC, type LRCLine } from "@/lib/render/lrc-parser";
import {
  MAX_ARRANGEMENT_STEPS,
  buildArrangementTimeline,
  buildSnappedSteps,
  parseSongArrangement,
  validateSongArrangement,
} from "@/lib/arrangements";
import type {
  ArrangementStep,
  RecordingSection,
  SongArrangement,
  TimelineItemSource,
} from "@/lib/arrangements";
import type { SongListItem } from "./SongList";
import {
  AlertTriangle,
  Copy,
  GripVertical,
  ListMusic,
  Loader2,
  Pause,
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

/** Lead-in window from the preceding songset item, per the preview panel. */
const LEAD_IN_DEFAULT_SECONDS = 10;
const LEAD_IN_MAX_SECONDS = 30;
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
    .map(({ step }) => `${step.sectionIndex}:${step.startSeconds}:${step.endSeconds}`)
    .join("|");
}

/** Score timecode (m:ss). Durations use formatDuration for app consistency. */
function formatTimecode(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "0:00";
  const total = Math.floor(seconds);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

/** LRC lines whose timestamp falls inside one Step's source range. */
function lyricsInRange(
  lines: LRCLine[],
  startSeconds: number,
  endSeconds: number
): LRCLine[] {
  return lines.filter(
    (line) => line.timeSeconds >= startSeconds && line.timeSeconds < endSeconds
  );
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

interface SortableStepBlockProps {
  entry: DraftStep;
  index: number;
  displayLabel: string;
  lines: LRCLine[];
  lyricsLoading: boolean;
  lyricsAvailable: boolean;
  activeLineIndex: number;
  isActive: boolean;
  isPlaying: boolean;
  disabled: boolean;
  onPlay: (id: string) => void;
  onDuplicate: (id: string) => void;
  onRemove: (id: string) => void;
}

/**
 * One arrangement component: its label tab, source timecode, transport, and
 * the lyric lines that belong to that section. Lyrics are the primary content;
 * the toolbar is deliberately quiet.
 */
function SortableStepBlock({
  entry,
  index,
  displayLabel,
  lines,
  lyricsLoading,
  lyricsAvailable,
  activeLineIndex,
  isActive,
  isPlaying,
  disabled,
  onPlay,
  onDuplicate,
  onRemove,
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

  const startLabel = formatTimecode(entry.step.startSeconds);
  const endLabel = formatTimecode(entry.step.endSeconds);

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
      data-section-index={entry.step.sectionIndex}
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
        <span className="ml-auto shrink-0 font-mono text-[11px] tabular-nums text-muted-foreground">
          {startLabel}–{endLabel}
        </span>
        <Button
          variant="ghost"
          size="icon-sm"
          className={cn(
            "shrink-0",
            isPlaying && "bg-primary/10 text-primary hover:bg-primary/15 hover:text-primary"
          )}
          onClick={() => onPlay(entry.id)}
          disabled={disabled}
          aria-label={`${isPlaying ? t("arrangement.action.pauseSectionAria") : t("arrangement.action.playSectionAria")} ${index + 1}: ${displayLabel}`}
          data-testid={`arrangement-section-play-${entry.id}`}
        >
          {isPlaying ? <Pause className="size-4" /> : <Play className="size-4" />}
        </Button>
        <Button
          variant="ghost"
          size="icon-sm"
          className="shrink-0"
          onClick={() => onDuplicate(entry.id)}
          disabled={disabled}
          aria-label={`${t("arrangement.action.duplicateAria")} ${displayLabel}`}
          data-testid={`arrangement-duplicate-${entry.id}`}
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
          data-testid={`arrangement-remove-${entry.id}`}
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

/** Playback window in Songset-timeline seconds; stops at `endSeconds`. */
interface PlaybackWindow {
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
  const leadInId = useId();

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
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [fallbackNotice, setFallbackNotice] = useState(false);
  const [leadInSeconds, setLeadInSeconds] = useState(LEAD_IN_DEFAULT_SECONDS);
  const [playbackWindow, setPlaybackWindow] = useState<PlaybackWindow | null>(
    null
  );

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

  const labelForDraftIndex = useCallback(
    (index: number): string | null => {
      const entry = draft[index];
      if (!entry) return null;
      return (
        occurrenceByIndex.get(entry.step.sectionIndex)?.displayLabel ??
        entry.step.label
      );
    },
    [draft, occurrenceByIndex]
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
  // current analysis (stale/malformed saved steps) or analysis is missing.
  const isPreviewAvailable = isDraftValid && initProblem === null;
  const canSave = isDraftValid && initProblem === null && !isSaving;

  // The selected item previews the draft; siblings keep their saved values.
  // The previous sibling is the lead-in source, so it must stay in the input.
  const previewItems = useMemo<TimelineItemSource[]>(() => {
    const draftArrangement = isDraftValid ? JSON.stringify(draftToArrangement(draft)) : null;
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
    }));
  }, [items, item.id, draft, isDraftValid]);

  const timeline = useMemo(() => buildArrangementTimeline(previewItems), [previewItems]);
  const songTimelineItem = useMemo(
    () => timeline.items.find((entry) => entry.itemId === item.id) ?? null,
    [timeline, item.id]
  );

  const previousItem = useMemo(() => {
    const index = items.findIndex((entry) => entry.id === item.id);
    return index > 0 ? items[index - 1] : null;
  }, [items, item.id]);
  const previousTitle = previousItem?.song?.title || t("browse.unknownSong");

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

  // Song window: the lead-in tail of the preceding item, then this arranged
  // song. Preview never plays the rest of the Songset.
  const songStartSeconds = songTimelineItem?.startSeconds ?? 0;
  const songEndSeconds = songStartSeconds + (songTimelineItem?.durationSeconds ?? 0);
  const leadInMaxSeconds = Math.max(
    0,
    Math.min(LEAD_IN_MAX_SECONDS, Math.floor(songStartSeconds))
  );
  const effectiveLeadInSeconds = Math.min(leadInSeconds, leadInMaxSeconds);
  const windowStartSeconds = Math.max(0, songStartSeconds - effectiveLeadInSeconds);
  const windowEndSeconds = isPreviewAvailable ? songEndSeconds : songStartSeconds;
  const windowTotalSeconds = Math.max(0, windowEndSeconds - windowStartSeconds);

  const position = preview.timelinePositionSeconds;
  const windowPositionSeconds = Math.min(
    Math.max(position - windowStartSeconds, 0),
    windowTotalSeconds
  );

  // Lyrics are the real recording's LRC, shown per component and highlighted
  // from the source time the transport is currently playing.
  const lyrics = useSongLyrics(item.recording?.contentHash ?? undefined);
  const parsedLyrics = useMemo<LRCLine[] | null>(() => {
    if (lyrics.lrcContent === null) return null;
    return isValidLRC(lyrics.lrcContent) ? parseLRC(lyrics.lrcContent) : null;
  }, [lyrics.lrcContent]);

  const blockLyrics = useMemo(
    () =>
      draft.map((entry) =>
        parsedLyrics
          ? lyricsInRange(
              parsedLyrics,
              entry.step.startSeconds,
              entry.step.endSeconds
            )
          : []
      ),
    [draft, parsedLyrics]
  );

  // Clips map 1:1 to draft steps, so the clip index is the component index.
  const activeClipIndex =
    preview.status === "playing" && songTimelineItem
      ? songTimelineItem.clips.findIndex(
          (clip) => position >= clip.startSeconds && position < clip.endSeconds
        )
      : -1;
  const activeClip =
    activeClipIndex >= 0 ? songTimelineItem?.clips[activeClipIndex] ?? null : null;
  const activeLocalSeconds = activeClip
    ? activeClip.sourceStartSeconds + (position - activeClip.startSeconds)
    : null;
  const activeLineIndex = activeLocalSeconds === null
    ? -1
    : activeLyricIndex(blockLyrics[activeClipIndex] ?? [], activeLocalSeconds);
  const activeLabel =
    activeClipIndex >= 0 ? labelForDraftIndex(activeClipIndex) : null;

  // A window only ever ends by itself: the transport owns the whole timeline,
  // so the Sheet pauses it at the component/song boundary.
  useEffect(() => {
    if (!playbackWindow) return;
    if (preview.status !== "playing") return;
    if (position < playbackWindow.endSeconds - 0.02) return;
    preview.pause();
  }, [playbackWindow, preview, position]);

  const startWindow = useCallback(
    (startSeconds: number) => {
      setPreviewError(null);
      void preview.startAtSeconds(startSeconds).catch(() => {
        // The hook reports failures through onPreviewError.
      });
    },
    [preview]
  );

  const handlePlaySection = useCallback(
    (id: string) => {
      if (isSaving) return;
      const index = draft.findIndex((entry) => entry.id === id);
      const clip = index >= 0 ? songTimelineItem?.clips[index] : undefined;
      if (!clip) return;
      const isPlayingThisSection =
        preview.status === "playing" &&
        position >= clip.startSeconds &&
        position < clip.endSeconds;
      if (isPlayingThisSection) {
        preview.pause();
        return;
      }
      setPlaybackWindow({
        startSeconds: clip.startSeconds,
        endSeconds: clip.endSeconds,
      });
      startWindow(clip.startSeconds);
    },
    [draft, isSaving, position, preview, songTimelineItem, startWindow]
  );

  const handlePreviewToggle = useCallback(() => {
    if (isSaving || !isPreviewAvailable || windowTotalSeconds <= 0) return;
    if (preview.status === "playing") {
      preview.pause();
      return;
    }
    // Resume a mid-window pause; anything at the window boundary restarts.
    const resumable =
      preview.status === "paused" &&
      position > windowStartSeconds + 0.25 &&
      position < windowEndSeconds - 0.25;
    setPlaybackWindow({
      startSeconds: windowStartSeconds,
      endSeconds: windowEndSeconds,
    });
    if (resumable) {
      setPreviewError(null);
      void preview.play().catch(() => {
        // The hook reports failures through onPreviewError.
      });
      return;
    }
    startWindow(windowStartSeconds);
  }, [
    isPreviewAvailable,
    isSaving,
    position,
    preview,
    startWindow,
    windowEndSeconds,
    windowStartSeconds,
    windowTotalSeconds,
  ]);

  const handleStop = useCallback(() => {
    preview.stop();
    setPlaybackWindow(null);
  }, [preview]);

  const phaseLabel = useMemo(() => {
    if (preview.status === "playing") {
      if (position < songStartSeconds) {
        return previousItem
          ? t("arrangement.preview.phaseLeadIn").replace("{song}", previousTitle)
          : null;
      }
      return activeLabel
        ? t("arrangement.preview.phasePlaying").replace("{label}", activeLabel)
        : null;
    }
    if (preview.status === "paused") return t("arrangement.preview.phasePaused");
    return null;
  }, [
    activeLabel,
    position,
    previousItem,
    previousTitle,
    preview.status,
    songStartSeconds,
    t,
  ]);

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
      setIsDirty(true);
    },
    [draft, isSaving, item, preview]
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
    setIsDirty(draftKey(previous) !== draftKey(initialDraftRef.current));
  }, [history, isSaving, item, preview]);

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
      if (isDirty) {
        setDirtyDialogOpen(true);
        return;
      }
      preview.stop();
      onOpenChange(false);
    },
    [isDirty, isSaving, onOpenChange, preview]
  );

  const handleDiscard = useCallback(() => {
    setDirtyDialogOpen(false);
    setIsDirty(false);
    preview.stop();
    onOpenChange(false);
  }, [onOpenChange, preview]);

  const handleSave = useCallback(async () => {
    if (!savedSteps || isSaving) return;
    setIsSaving(true);
    setSaveError(null);
    preview.stop();
    try {
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
  }, [draft, isSaving, onOpenChange, onSave, preview, savedSteps, t]);

  const handleReset = useCallback(async () => {
    if (isSaving) return;
    setIsSaving(true);
    setSaveError(null);
    preview.stop();
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
  }, [isSaving, onOpenChange, onSave, preview, t]);

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

  const leadInNote =
    leadInMaxSeconds === 0
      ? previousItem
        ? t("arrangement.preview.leadInUnavailable")
        : t("arrangement.preview.leadInFirst")
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

                  <div className="mb-3 flex items-baseline justify-between gap-2">
                    <p
                      className="text-xs text-muted-foreground"
                      data-testid="arrangement-component-count"
                    >
                      <span className="font-mono text-sm text-foreground">
                        {draft.length}
                      </span>{" "}
                      {t("arrangement.draft.countLabel")}
                    </p>
                    <span className="font-mono text-[10px] text-muted-foreground/70">
                      {draft.length}/{MAX_ARRANGEMENT_STEPS}
                    </span>
                  </div>

                  {/* Source palette */}
                  <section aria-label={t("arrangement.section.source")} className="mb-4">
                    <h3 className="mb-1.5 text-[11px] font-semibold tracking-[0.12em] text-muted-foreground uppercase">
                      {t("arrangement.section.source")}
                    </h3>
                    {occurrences.length === 0 ? (
                      <p className="text-xs text-muted-foreground">
                        {t("arrangement.palette.empty")}
                      </p>
                    ) : (
                      <>
                        <div className="flex flex-wrap gap-1.5">
                          {occurrences.map((occurrence) => (
                            <Button
                              key={`${occurrence.index}-${occurrence.section.label}`}
                              variant="outline"
                              size="sm"
                              className="h-7 text-xs"
                              onClick={() => addSection(occurrence.index)}
                              disabled={!snappedByIndex.has(occurrence.index) || isSaving}
                              aria-label={`${t("arrangement.action.addAria")}: ${occurrence.displayLabel}`}
                              data-testid={`arrangement-add-${occurrence.index}`}
                            >
                              <Plus className="size-3" />
                              {occurrence.displayLabel}
                            </Button>
                          ))}
                        </div>
                        <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">
                          {t("arrangement.section.sourceHint")}
                        </p>
                      </>
                    )}
                  </section>

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
                              displayLabel={
                                occurrenceByIndex.get(entry.step.sectionIndex)
                                  ?.displayLabel ?? entry.step.label
                              }
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
                              disabled={isSaving}
                              onPlay={handlePlaySection}
                              onDuplicate={duplicateStep}
                              onRemove={removeStep}
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

                {/* Preview rail: this song only, with an adjustable lead-in. */}
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
                        {formatTimecode(windowTotalSeconds)}
                      </span>
                    </div>

                    {previousItem && (
                      <p className="text-[11px] leading-relaxed text-muted-foreground">
                        {t("arrangement.preview.description").replace(
                          "{song}",
                          previousTitle
                        )}
                      </p>
                    )}

                    {windowTotalSeconds > 0 && (
                      <div
                        role="img"
                        aria-label={t("arrangement.preview.timelineAria")}
                        data-testid="arrangement-preview-timeline"
                        className="relative h-9 overflow-hidden rounded-lg border border-border/70 bg-muted/40"
                      >
                        {effectiveLeadInSeconds > 0 && (
                          <div
                            aria-hidden="true"
                            className="absolute inset-y-0 left-0 border-r border-dashed border-border/80 bg-muted"
                            style={{
                              width: `${(effectiveLeadInSeconds / windowTotalSeconds) * 100}%`,
                            }}
                          />
                        )}
                        {(songTimelineItem?.clips ?? []).map((clip, clipIndex) => (
                          <div
                            key={`${clip.sourceStartSeconds}-${clipIndex}`}
                            aria-hidden="true"
                            className={cn(
                              "absolute inset-y-1.5 rounded-[3px]",
                              clipIndex === activeClipIndex
                                ? "bg-primary"
                                : "bg-primary/25"
                            )}
                            style={{
                              left: `${((clip.startSeconds - windowStartSeconds) / windowTotalSeconds) * 100}%`,
                              width: `calc(${((clip.endSeconds - clip.startSeconds) / windowTotalSeconds) * 100}% - 2px)`,
                            }}
                          />
                        ))}
                        <div
                          aria-hidden="true"
                          className="absolute inset-y-0 w-0.5 bg-foreground/80"
                          style={{
                            left: `${(windowPositionSeconds / windowTotalSeconds) * 100}%`,
                          }}
                        />
                      </div>
                    )}

                    <div data-testid="arrangement-lead-in">
                      <div className="mb-1 flex items-baseline justify-between gap-2">
                        <label
                          htmlFor={leadInId}
                          className="text-[11px] font-medium text-foreground/80"
                        >
                          {t("arrangement.preview.leadIn")}
                        </label>
                        <output
                          htmlFor={leadInId}
                          className="font-mono text-[11px] tabular-nums text-muted-foreground"
                        >
                          {t("arrangement.preview.seconds").replace(
                            "{seconds}",
                            String(effectiveLeadInSeconds)
                          )}
                        </output>
                      </div>
                      <Slider
                        id={leadInId}
                        value={[effectiveLeadInSeconds]}
                        onValueChange={(value) => {
                          const next = Array.isArray(value) ? value[0] : value;
                          if (typeof next === "number") {
                            setLeadInSeconds(
                              Math.max(0, Math.min(leadInMaxSeconds, Math.round(next)))
                            );
                          }
                        }}
                        min={0}
                        max={Math.max(1, leadInMaxSeconds)}
                        step={1}
                        disabled={leadInMaxSeconds === 0 || isSaving}
                        aria-label={t("arrangement.preview.leadInAria")}
                      />
                      {leadInNote && (
                        <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                          {leadInNote}
                        </p>
                      )}
                    </div>

                    <div className="flex items-center gap-2">
                      <Button
                        size="sm"
                        className="flex-1"
                        onClick={handlePreviewToggle}
                        disabled={
                          !isPreviewAvailable ||
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
                        onClick={handleStop}
                        disabled={preview.status === "idle"}
                        aria-label={t("arrangement.preview.stop")}
                        data-testid="arrangement-preview-stop"
                      >
                        <Square className="size-3.5" />
                      </Button>
                    </div>

                    <div className="flex items-baseline justify-between gap-2 font-mono text-[11px] tabular-nums text-muted-foreground">
                      <span>
                        {formatTimecode(windowPositionSeconds)} /{" "}
                        {formatTimecode(windowTotalSeconds)}
                      </span>
                      <span
                        className="truncate text-right"
                        data-testid="arrangement-preview-phase"
                      >
                        {phaseLabel ?? ""}
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
