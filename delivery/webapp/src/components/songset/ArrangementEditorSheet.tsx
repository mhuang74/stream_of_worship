"use client";

import { useCallback, useId, useMemo, useState } from "react";
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
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Slider } from "@/components/ui/slider";
import { cn } from "@/lib/utils";
import { formatDuration } from "@/lib/format";
import { useLocale } from "@/hooks/useLocale";
import { useArrangementPreview } from "@/hooks/useArrangementPreview";
import type { ArrangementPreviewError } from "@/lib/audio/arrangement-preview";
import {
  MAX_ARRANGEMENT_STEPS,
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

interface SortableDraftRowProps {
  entry: DraftStep;
  index: number;
  displayLabel: string;
  onDuplicate: (id: string) => void;
  onRemove: (id: string) => void;
}

function SortableDraftRow({
  entry,
  index,
  displayLabel,
  onDuplicate,
  onRemove,
}: SortableDraftRowProps) {
  const { t } = useLocale();
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: entry.id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    zIndex: isDragging ? 50 : undefined,
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={cn(
        "flex items-center gap-2 rounded-md border border-border/60 bg-background p-2",
        isDragging && "opacity-50"
      )}
      data-testid="arrangement-draft-step"
      data-section-index={entry.step.sectionIndex}
    >
      <Button
        variant="ghost"
        size="icon-sm"
        className="shrink-0 cursor-grab active:cursor-grabbing touch-none"
        {...attributes}
        {...listeners}
        aria-label={`${t("arrangement.action.dragAria")} ${index + 1}: ${displayLabel}`}
      >
        <GripVertical className="size-4 text-muted-foreground" />
      </Button>
      <span className="shrink-0 text-xs text-muted-foreground w-5 text-right">{index + 1}</span>
      <span className="flex-1 min-w-0 truncate text-sm">{displayLabel}</span>
      <span className="shrink-0 text-xs text-muted-foreground">
        {formatDuration(entry.step.endSeconds - entry.step.startSeconds)}
      </span>
      <Button
        variant="ghost"
        size="icon-sm"
        onClick={() => onDuplicate(entry.id)}
        aria-label={`${t("arrangement.action.duplicateAria")} ${displayLabel}`}
        data-testid={`arrangement-duplicate-${entry.id}`}
      >
        <Copy className="size-4" />
      </Button>
      <Button
        variant="ghost"
        size="icon-sm"
        onClick={() => onRemove(entry.id)}
        aria-label={`${t("arrangement.action.removeAria")} ${displayLabel}`}
        data-testid={`arrangement-remove-${entry.id}`}
      >
        <Trash2 className="size-4 text-destructive" />
      </Button>
    </div>
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
  if (draft.length === 0) return null;
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
  const [isDirty, setIsDirty] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [resetConfirmOpen, setResetConfirmOpen] = useState(false);
  const [dirtyDialogOpen, setDirtyDialogOpen] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [fallbackNotice, setFallbackNotice] = useState(false);

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

  const commitDraft = useCallback(
    (next: DraftStep[]) => {
      preview.stop();
      setPreviewError(null);
      setFallbackNotice(false);
      setDraftState({ draft: next, problem: validateDraftProblem(next, item) });
      setIsDirty(true);
    },
    [item, preview]
  );

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
      if (isDirty) {
        setDirtyDialogOpen(true);
        return;
      }
      preview.stop();
      onOpenChange(false);
    },
    [isDirty, onOpenChange, preview]
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

  const handlePlayToggle = useCallback(() => {
    if (preview.status === "playing") {
      preview.pause();
      return;
    }
    setPreviewError(null);
    void preview.play().catch(() => {
      // The hook reports failures through onPreviewError.
    });
  }, [preview]);

  const handleStartAt = useCallback(
    (itemId: string) => {
      void preview.startAt(itemId).catch(() => {
        // The hook reports failures through onPreviewError.
      });
    },
    [preview]
  );

  const originalDurationSeconds = item.recording?.durationSeconds ?? null;
  const showFallbackNotice = preview.isFallbackMode || fallbackNotice;

  return (
    <>
      <Sheet open={isOpen} onOpenChange={handleRequestClose}>
        <SheetContent
          side="bottom"
          className="data-[side=bottom]:!h-[85vh] sm:data-[side=bottom]:!h-[90vh] overflow-hidden"
          data-testid="arrangement-sheet"
        >
          <SheetHeader className="pb-2">
            <SheetTitle>{t("arrangement.sheet.title")}</SheetTitle>
            <SheetDescription>
              {item.song?.title || t("browse.unknownSong")} — {t("arrangement.sheet.description")}
            </SheetDescription>
          </SheetHeader>

          <div className="flex flex-col h-full min-h-0 pb-28 sm:pb-20">
            <div className="flex-1 overflow-y-auto min-h-0 space-y-4 pr-1">
              <p className="text-xs text-muted-foreground">
                {t("arrangement.note.snap")}
              </p>

              {initProblem !== null && (
                <Alert variant="destructive">
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

              {/* Source palette */}
              <section aria-label={t("arrangement.section.source")}>
                <h3 className="text-sm font-medium mb-2">{t("arrangement.section.source")}</h3>
                {occurrences.length === 0 ? (
                  <p className="text-xs text-muted-foreground">
                    {t("arrangement.palette.empty")}
                  </p>
                ) : (
                  <>
                    <p className="text-xs text-muted-foreground mb-2">
                      {t("arrangement.section.sourceHint")}
                    </p>
                    <div className="flex flex-wrap gap-2">
                      {occurrences.map((occurrence) => (
                        <Button
                          key={`${occurrence.index}-${occurrence.section.label}`}
                          variant="outline"
                          size="sm"
                          onClick={() => addSection(occurrence.index)}
                          disabled={!snappedByIndex.has(occurrence.index) || isSaving}
                          aria-label={`${t("arrangement.action.addAria")}: ${occurrence.displayLabel}`}
                          data-testid={`arrangement-add-${occurrence.index}`}
                        >
                          <Plus className="size-3 mr-1" />
                          {occurrence.displayLabel}
                        </Button>
                      ))}
                    </div>
                  </>
                )}
              </section>

              {/* Draft sequence */}
              <section aria-label={t("arrangement.section.draft")}>
                <div className="flex items-center justify-between mb-2">
                  <h3 className="text-sm font-medium">{t("arrangement.section.draft")}</h3>
                  <Badge variant="outline" className="text-xs">
                    {t("arrangement.draft.count")} {draft.length}/{MAX_ARRANGEMENT_STEPS}
                  </Badge>
                </div>

                {draft.length === 0 ? (
                  <p className="text-xs text-muted-foreground rounded-md border border-dashed p-4 text-center">
                    {t("arrangement.draft.empty")}
                  </p>
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
                      <div className="space-y-2" role="list">
                        {draft.map((entry, index) => {
                          const occurrence = occurrenceByIndex.get(entry.step.sectionIndex);
                          return (
                            <SortableDraftRow
                              key={entry.id}
                              entry={entry}
                              index={index}
                              displayLabel={occurrence?.displayLabel ?? entry.step.label}
                              onDuplicate={duplicateStep}
                              onRemove={removeStep}
                            />
                          );
                        })}
                      </div>
                    </SortableContext>
                  </DndContext>
                )}

                {draft.length === 0 && (
                  <p className="text-xs text-amber-600 mt-2" data-testid="arrangement-empty-error">
                    {t("arrangement.validation.empty")}
                  </p>
                )}
                {tooManySteps && (
                  <p className="text-xs text-amber-600 mt-2" data-testid="arrangement-too-many-error">
                    {t("arrangement.validation.tooMany").replace(
                      "{max}",
                      String(MAX_ARRANGEMENT_STEPS)
                    )}
                  </p>
                )}

                {/* Original vs arranged duration + reset semantics */}
                <div className="mt-3 rounded-md border border-border/60 bg-muted/40 p-2 text-xs">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-muted-foreground">
                      {t("arrangement.duration.originalLabel")}
                    </span>
                    <span>{originalDurationSeconds != null ? formatDuration(originalDurationSeconds) : "—"}</span>
                  </div>
                  <div className="flex items-center justify-between gap-2 mt-0.5">
                    <span className="text-muted-foreground">
                      {t("arrangement.duration.arrangedLabel")}
                    </span>
                    <span className="text-primary">{formatDuration(draftDurationSeconds)}</span>
                  </div>
                  <p className="text-muted-foreground mt-1">
                    {t("arrangement.note.saveBehavior")}
                  </p>
                </div>
              </section>

              {/* Preview transport */}
              <section aria-label={t("arrangement.preview.title")} className="space-y-2">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-medium">{t("arrangement.preview.title")}</h3>
                  <Badge variant="outline" className="text-xs">
                    {t("arrangement.preview.total")} {formatDuration(preview.totalSeconds)}
                  </Badge>
                </div>
                <p className="text-xs text-muted-foreground">
                  {t("arrangement.preview.description")}
                </p>

                {showFallbackNotice && (
                  <p className="text-xs text-amber-600" data-testid="arrangement-fallback-notice">
                    {t("arrangement.preview.fallbackNotice")}
                  </p>
                )}
                {previewError && (
                  <Alert variant="destructive" data-testid="arrangement-preview-error">
                    <AlertTriangle className="size-4" />
                    <AlertDescription>{previewError}</AlertDescription>
                  </Alert>
                )}

                <div className="flex items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={handlePlayToggle}
                    disabled={!isPreviewAvailable || preview.status === "loading"}
                    aria-label={
                      preview.status === "playing"
                        ? t("arrangement.preview.pause")
                        : t("arrangement.preview.play")
                    }
                    data-testid="arrangement-preview-play"
                  >
                    {preview.status === "loading" ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : preview.status === "playing" ? (
                      <Pause className="size-4" />
                    ) : (
                      <Play className="size-4" />
                    )}
                    {preview.status === "loading"
                      ? t("arrangement.preview.loading")
                      : preview.status === "playing"
                        ? t("arrangement.preview.pause")
                        : t("arrangement.preview.play")}
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={preview.stop}
                    disabled={preview.status === "idle"}
                    aria-label={t("arrangement.preview.stop")}
                    data-testid="arrangement-preview-stop"
                  >
                    <Square className="size-4" />
                  </Button>
                  <span className="text-xs text-muted-foreground tabular-nums">
                    {formatDuration(preview.timelinePositionSeconds)} /{" "}
                    {formatDuration(preview.totalSeconds)}
                  </span>
                </div>

                <div role="group" aria-label={t("arrangement.preview.seekAria")}>
                  <Slider
                    value={[Math.min(preview.timelinePositionSeconds, preview.totalSeconds || 0)]}
                    onValueChange={(value) => {
                      const next = Array.isArray(value) ? value[0] : value;
                      if (typeof next === "number") preview.seek(next);
                    }}
                    min={0}
                    max={Math.max(1, preview.totalSeconds)}
                    step={0.5}
                    disabled={!isPreviewAvailable || preview.totalSeconds <= 0}
                  />
                </div>

                <div className="flex flex-wrap gap-1.5">
                  {items.map((entry) => (
                    <Button
                      key={entry.id}
                      variant="ghost"
                      size="sm"
                      className="text-xs text-muted-foreground"
                      onClick={() => handleStartAt(entry.id)}
                      disabled={!isPreviewAvailable}
                      aria-label={`${t("arrangement.preview.startHereAria")}: ${entry.song?.title || t("browse.unknownSong")}`}
                      data-testid={`arrangement-start-at-${entry.id}`}
                    >
                      {t("arrangement.preview.startHere")}
                      <Play className="size-3 ml-1" />
                      {entry.song?.title || t("browse.unknownSong")}
                    </Button>
                  ))}
                </div>
              </section>
            </div>

            {/* Footer is the column's last flex child (BrowseSheet pattern). */}
            <div className="pt-4 border-t mt-4">
              {saveError && (
                <Alert variant="destructive" className="mb-2" data-testid="arrangement-save-error">
                  <AlertTriangle className="size-4" />
                  <AlertDescription>{saveError}</AlertDescription>
                </Alert>
              )}
              <div className="flex flex-wrap items-center justify-between gap-2">
                <Button
                  variant="outline"
                  onClick={() => setResetConfirmOpen(true)}
                  disabled={isSaving}
                  data-testid="arrangement-reset"
                >
                  <RotateCcw className="size-4 mr-1" />
                  {t("arrangement.action.reset")}
                </Button>
                <div className="flex items-center gap-2">
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
                        <Loader2 className="size-4 mr-1 animate-spin" />
                        {t("arrangement.action.saving")}
                      </>
                    ) : (
                      <>
                        <ListMusic className="size-4 mr-1" />
                        {t("arrangement.action.save")}
                      </>
                    )}
                  </Button>
                </div>
              </div>
            </div>
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
