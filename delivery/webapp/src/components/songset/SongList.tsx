"use client";

import { useState, useCallback, useEffect, useRef, useId } from "react";
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  DragEndEvent,
} from "@dnd-kit/core";
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  verticalListSortingStrategy,
  useSortable,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { GripVertical, Trash2, Music, Clock, ChevronRight, Play, Pause, Loader2, ListMusic } from "lucide-react";
import { cn } from "@/lib/utils";
import { useSongPlayback } from "@/hooks/useSongPlayback";
import { useLocale } from "@/hooks/useLocale";
import type { RecordingSection } from "@/lib/arrangements";
import { FavoriteButton } from "./FavoriteButton";
import { ThemeLabel, toSongTheme } from "./ThemeLabel";

function escapeCssSelectorValue(value: string): string {
  const globalCss = (globalThis as { CSS?: { escape?: (v: string) => string } }).CSS;
  if (typeof globalCss?.escape === "function") {
    return globalCss.escape(value);
  }
  // Fallback for older webviews: escape backslash and double-quote, which are
  // the only characters that can break out of a quoted CSS attribute selector.
  return value.replace(/["\\]/g, "\\$&");
}

export interface SongListItem {
  id: string;
  songId: string;
  position: number;
  song: {
    id: string;
    title: string;
    composer: string | null;
    lyricist: string | null;
    albumName: string | null;
    musicalKey: string | null;
    effectiveKey?: string | null;
    effectiveKeyStartPitchClass?: number | null;
    effectiveKeyEndPitchClass?: number | null;
  } | null;
  recording: {
    contentHash: string;
    hashPrefix: string;
    durationSeconds: number | null;
    tempoBpm: number | null;
    musicalKey: string | null;
    effectiveKey?: string | null;
    effectiveKeyStartPitchClass?: number | null;
    effectiveKeyEndPitchClass?: number | null;
    theme?: string | null;
    /** Parsed sections (recordings.sections); absent/NULL means analysis unavailable. */
    sections?: RecordingSection[] | null;
    /** Parsed beats (recordings.beats); empty means analysis unavailable. */
    beats?: number[];
  } | null;
  gapBeats: number;
  crossfadeEnabled: number;
  crossfadeDurationSeconds?: number | null;
  keyShiftSemitones: number;
  tempoRatio: number;
  markedLineCount?: number;
  /** Raw persisted arrangement JSON, verbatim from the API (NULL = untouched). */
  arrangementSteps: string | null;
  /** Arranged duration via getItemDurationSeconds; null when the present arrangement is invalid/unmeasurable. */
  arrangedDurationSeconds: number | null;
  /** Client-computed availability of sections+beats analysis for this item. */
  arrangementAnalysisAvailable: boolean;
}

interface SongListProps {
  items: SongListItem[];
  onReorder: (items: SongListItem[]) => void;
  onRemove: (itemId: string) => void;
  onEditTransition?: (itemId: string) => void;
  onEditArrangement?: (itemId: string) => void;
  readOnly?: boolean;
  className?: string;
  isRemoving?: boolean;
  songsetId?: string;
  highlightSongId?: string | null;
  onHighlightConsumed?: () => void;
  favoriteIds?: Set<string>;
  onToggleFavorite?: (songId: string) => void | Promise<void>;
}

interface SortableSongItemProps {
  item: SongListItem;
  index: number;
  onRemove: (itemId: string) => void;
  onEditTransition?: (itemId: string) => void;
  onEditArrangement?: (itemId: string) => void;
  readOnly?: boolean;
  isPlaying?: boolean;
  isPreviewLoading?: boolean;
  onPlaySong?: (songId: string) => void;
  isConfirming: boolean;
  onRequestConfirm: () => void;
  onCancelConfirm: () => void;
  isRemoving?: boolean;
  isHighlighted?: boolean;
  favoriteIds?: Set<string>;
  onToggleFavorite?: (songId: string) => void | Promise<void>;
}

function SortableSongItem({
  item,
  index,
  onRemove,
  onEditTransition,
  onEditArrangement,
  readOnly = false,
  isPlaying = false,
  isPreviewLoading = false,
  onPlaySong,
  isConfirming,
  onRequestConfirm,
  onCancelConfirm,
  isRemoving = false,
  isHighlighted = false,
  favoriteIds,
  onToggleFavorite,
}: SortableSongItemProps) {
  const { t } = useLocale();
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: item.id, disabled: readOnly });

  const confirmRemove = isConfirming;

  useEffect(() => {
    if (isDragging) onCancelConfirm();
  }, [isDragging, onCancelConfirm]);

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    zIndex: isDragging ? 50 : undefined,
  };

  const formatDuration = (seconds?: number | null) => {
    if (!seconds) return "--:--";
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, "0")}`;
  };

  const hasMarkedLines = (item.markedLineCount ?? 0) > 0;

  // Analysis is usable only when sections and beats are both present; a saved
  // arrangement stays editable even when analysis is unavailable/stale so the
  // user can open the explanation and Reset it.
  const hasArrangement = item.arrangementSteps != null;
  const isArrangeDisabled = !hasArrangement && !item.arrangementAnalysisAvailable;
  const arrangeDisabledReason = t("arrangement.disabled.noAnalysis");

  const itemTheme = item.recording?.theme ? toSongTheme(item.recording.theme) : null;

  return (
    <div
      ref={setNodeRef}
      style={style}
      data-song-id={item.songId}
      className={cn(
        "group",
        isDragging && "opacity-50"
      )}
    >
      <Card className={cn(
        "border-border/50 hover:border-border transition-colors",
        isPlaying && "border-primary/30 bg-primary/5",
        confirmRemove && "border-destructive/40 bg-destructive/5",
        isHighlighted && "ring-2 ring-primary border-primary animate-pulse"
      )}>
        <CardContent className="p-3">
          <div className="flex items-center gap-3">
            {/* Drag handle */}
            {!readOnly && (
              <Button
                variant="ghost"
                size="icon-sm"
                className="cursor-grab active:cursor-grabbing shrink-0 touch-none"
                {...attributes}
                {...listeners}
                aria-label={`${t("browse.dragReorder")}${index + 1}`}
              >
                <GripVertical className="size-4 text-muted-foreground" />
              </Button>
            )}

            <Button
              variant="ghost"
              size="icon-sm"
              className={cn(
                "shrink-0 rounded-full",
                isPlaying && "bg-primary/10 text-primary"
              )}
              onClick={() => onPlaySong?.(item.songId)}
              aria-label={`${isPlaying ? t("browse.pause") : t("browse.play")}${item.song?.title || t("browse.song")}`}
              disabled={!item.recording}
            >
              {isPreviewLoading ? (
                <Loader2 className="size-4 animate-spin" />
              ) : isPlaying ? (
                <Pause className="size-4" />
              ) : (
                <Play className="size-4 ml-0.5" />
              )}
            </Button>

            {(!readOnly || (favoriteIds?.has(item.songId) ?? false)) && (
              <FavoriteButton
                songId={item.songId}
                isFavorite={favoriteIds?.has(item.songId) ?? false}
                onToggle={onToggleFavorite}
              />
            )}

            {/* Song info */}
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <h4 className="font-medium text-sm truncate">
                  {item.song?.title || t("browse.unknownSong")}
                </h4>
                {hasMarkedLines && (
                  <Badge variant="outline" className="text-xs shrink-0 text-amber-600 border-amber-500/50">
                    {item.markedLineCount} {t("browse.marked")}
                  </Badge>
                )}
                {hasArrangement && (
                  <Badge variant="outline" className="text-xs shrink-0 text-primary border-primary/40">
                    {t("arrangement.badge.arranged")}
                  </Badge>
                )}
              </div>
              <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground mt-0.5">
                <span className="flex items-center gap-1">
                  <Music className="size-3" />
                  {item.song?.composer || item.song?.lyricist || t("browse.unknownArtist")}
                </span>
                {item.recording?.durationSeconds && (
                  <span className="flex items-center gap-1" data-testid="original-duration">
                    <Clock className="size-3" />
                    {formatDuration(item.recording.durationSeconds)}
                  </span>
                )}
                {hasArrangement && item.arrangedDurationSeconds != null && (
                  <span
                    className="flex items-center gap-1 text-primary"
                    data-testid="arranged-duration"
                    title={t("arrangement.duration.arrangedLabel")}
                  >
                    <ListMusic className="size-3" />
                    {formatDuration(item.arrangedDurationSeconds)}
                  </span>
                )}
                {hasArrangement && item.arrangedDurationSeconds == null && (
                  <span
                    className="flex items-center gap-1 text-amber-600 dark:text-amber-400"
                    data-testid="arranged-invalid"
                    title={t("arrangement.error.unavailableOrStale")}
                  >
                    <ListMusic className="size-3" />
                    {t("arrangement.duration.unavailable")}
                  </span>
                )}
                {(item.song?.effectiveKey ?? item.song?.musicalKey) && (
                  <span>• {item.song?.effectiveKey ?? item.song?.musicalKey}</span>
                )}
                {item.recording?.tempoBpm && (
                  <span>• {Math.round(item.recording.tempoBpm)} {t("browse.bpm")}</span>
                )}
                {item.song?.albumName && (
                  <span className="truncate hidden sm:inline" data-testid="song-album">
                    • {item.song?.albumName}
                  </span>
                )}
              </div>
              {itemTheme && (
                <div className="mt-1">
                  <ThemeLabel theme={itemTheme} />
                </div>
              )}
            </div>

            {/* Transition indicator (for non-first songs) */}
            {index > 0 && (
              <Button
                variant="ghost"
                size="sm"
                className="shrink-0 text-xs text-muted-foreground hidden md:flex"
                onClick={() => onEditTransition?.(item.id)}
                aria-label={`${t("browse.transition.editBefore")}${item.song?.title || t("browse.song")}${t("browse.transition.ariaGap")}${item.gapBeats} ${t("browse.unit.beats")}${item.crossfadeEnabled ? t("browse.transition.ariaCrossfade") : ""}`}
              >
                {t("browse.transition.gapLabel")}{item.gapBeats} {t("browse.unit.beats")}
                {item.crossfadeEnabled ? t("browse.transition.crossfadeSuffix") : ""}
                <ChevronRight className="size-3 ml-1" />
              </Button>
            )}

            {/* Arrange button — always visible (including mobile), unlike the
                desktop-only transition indicator. */}
            {!readOnly && (
              <Button
                variant="ghost"
                size="sm"
                className="shrink-0 text-xs text-muted-foreground"
                onClick={() => onEditArrangement?.(item.id)}
                disabled={isArrangeDisabled}
                title={isArrangeDisabled ? arrangeDisabledReason : t("arrangement.action.edit")}
                aria-label={`${t("arrangement.action.edit")} ${item.song?.title || t("browse.song")}${isArrangeDisabled ? ` — ${arrangeDisabledReason}` : ""}`}
                data-testid={`arrange-button-${item.id}`}
              >
                <ListMusic className="size-4 md:mr-1" />
                <span className="hidden md:inline">{t("arrangement.action.edit")}</span>
              </Button>
            )}

            {/* Remove button */}
            {!readOnly && (
              <div className="shrink-0 min-w-[32px] flex justify-end">
                {!confirmRemove ? (
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    className="opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity"
                    onClick={onRequestConfirm}
                    disabled={isRemoving}
                    aria-label={`${t("browse.remove")}${item.song?.title || t("browse.song")}`}
                  >
                    <Trash2 className="size-4 text-destructive" />
                  </Button>
                ) : (
                  <Button
                    variant="destructive"
                    size="sm"
                    className="h-7 px-2 text-xs"
                    onClick={() => onRemove(item.id)}
                    disabled={isRemoving}
                    aria-label={`${t("browse.confirmDelete")}${item.song?.title || t("browse.song")}`}
                  >
                    {t("browse.delete")}
                  </Button>
                )}
              </div>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

export function SongList({
  items,
  onReorder,
  onRemove,
  onEditTransition,
  onEditArrangement,
  readOnly = false,
  className,
  isRemoving = false,
  songsetId,
  highlightSongId,
  onHighlightConsumed,
  favoriteIds,
  onToggleFavorite,
}: SongListProps) {
  const { t } = useLocale();
  const [localItems, setLocalItems] = useState(items);
  const prevItemIdsRef = useRef<string | null>(null);
  const [confirmingItemId, setConfirmingItemId] = useState<string | null>(null);
  const dndContextId = useId();
  const [highlightedSongId, setHighlightedSongId] = useState<string | null>(null);

  useEffect(() => {
    if (!confirmingItemId || isRemoving) return;
    const timer = setTimeout(() => setConfirmingItemId(null), 5000);
    return () => clearTimeout(timer);
  }, [confirmingItemId, isRemoving]);

  const resolveSong = useCallback(
    (songId: string) => {
      const item = localItems.find((i) => i.songId === songId);
      if (!item) return null;
      return {
        id: item.songId,
        title: item.song?.title || t("browse.unknownSong"),
        artist: item.song?.composer || item.song?.lyricist || t("browse.unknownArtist"),
        recording: item.recording
          ? {
              hashPrefix: item.recording.hashPrefix,
              contentHash: item.recording.contentHash,
              durationSeconds: item.recording.durationSeconds,
            }
          : null,
      };
    },
    [localItems, t]
  );

  const { playingSongId, previewLoadingSongId, handlePlay: handlePlaySong } = useSongPlayback({
    resolveSong,
    originSongsetId: songsetId,
    noAudioMessage: t("browse.noAudioAvailable"),
    failedToLoadMessage: t("browse.failedToLoadPreview"),
  });

  useEffect(() => {
    const currentItemIds = items.map((i) => i.id).join(",");
    if (prevItemIdsRef.current !== currentItemIds) {
      prevItemIdsRef.current = currentItemIds;
      setLocalItems(items);
    }
  }, [items]);

  // Scroll to and highlight the target song when highlightSongId is provided.
  // Uses requestAnimationFrame with a short retry loop to handle drag-and-drop
  // layout shifts and slow hydration reliably. Once the ring is shown and
  // dismissed, onHighlightConsumed is called so the parent can clear the
  // highlight state (decoupled from URL cleanup).
  useEffect(() => {
    if (!highlightSongId) return;

    let raf: number;
    let timeoutId: ReturnType<typeof setTimeout>;
    let attempts = 0;
    const maxAttempts = 20;

    const tryScroll = () => {
      const el = document.querySelector(
        `[data-song-id="${escapeCssSelectorValue(highlightSongId)}"]`
      );
      if (el) {
        el.scrollIntoView({ behavior: "smooth", block: "center" });
        setHighlightedSongId(highlightSongId);
        dismissTimer = setTimeout(() => {
          setHighlightedSongId(null);
          onHighlightConsumed?.();
        }, 3000);
        return;
      }
      if (++attempts < maxAttempts) {
        timeoutId = setTimeout(() => {
          raf = requestAnimationFrame(tryScroll);
        }, 50);
      } else {
        onHighlightConsumed?.();
      }
    };

    let dismissTimer: ReturnType<typeof setTimeout>;
    raf = requestAnimationFrame(tryScroll);

    return () => {
      cancelAnimationFrame(raf);
      clearTimeout(timeoutId);
      clearTimeout(dismissTimer);
    };
  }, [highlightSongId, onHighlightConsumed]);

  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: {
        distance: 8,
      },
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  );

  const handleDragEnd = useCallback(
    (event: DragEndEvent) => {
      const { active, over } = event;

      if (over && active.id !== over.id) {
        const oldIndex = localItems.findIndex((item) => item.id === active.id);
        const newIndex = localItems.findIndex((item) => item.id === over.id);
        const newItems = arrayMove(localItems, oldIndex, newIndex);
        const updatedItems = newItems.map((item, index) => ({
          ...item,
          position: index,
        }));
        setLocalItems(updatedItems);
        onReorder(updatedItems);
      }
    },
    [onReorder, localItems]
  );

  if (items.length === 0) {
    return (
      <div className={cn("text-center py-12 border-2 border-dashed rounded-lg", className)}>
        <Music className="size-8 mx-auto text-muted-foreground mb-3" />
        <p className="text-muted-foreground">{t("browse.empty.noSongs")}</p>
        <p className="text-sm text-muted-foreground mt-1">
          {t("browse.empty.tapToAdd")}
        </p>
      </div>
    );
  }

  return (
    <DndContext
      id={dndContextId}
      sensors={sensors}
      collisionDetection={closestCenter}
      onDragEnd={handleDragEnd}
    >
      <SortableContext
        items={localItems.map((item) => item.id)}
        strategy={verticalListSortingStrategy}
      >
        <div className={cn("space-y-2", className)} role="list" aria-label={t("browse.songs")}>
          {localItems.map((item, index) => (
            <SortableSongItem
              key={item.id}
              item={item}
              index={index}
              onRemove={onRemove}
              onEditTransition={onEditTransition}
              onEditArrangement={onEditArrangement}
              readOnly={readOnly}
              isPlaying={playingSongId === item.songId}
              isPreviewLoading={previewLoadingSongId === item.songId}
              onPlaySong={handlePlaySong}
              isConfirming={confirmingItemId === item.id}
              onRequestConfirm={() => setConfirmingItemId(item.id)}
              onCancelConfirm={() => setConfirmingItemId(null)}
              isRemoving={isRemoving}
              isHighlighted={highlightedSongId === item.songId}
              favoriteIds={favoriteIds}
              onToggleFavorite={onToggleFavorite}
            />
          ))}
        </div>
      </SortableContext>
    </DndContext>
  );
}
