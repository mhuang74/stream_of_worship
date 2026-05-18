"use client";

import { useState, useCallback, useEffect, useRef } from "react";
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
import { GripVertical, Trash2, Music, Clock, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

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
  } | null;
  recording: {
    contentHash: string;
    durationSeconds: number | null;
    tempoBpm: number | null;
    musicalKey: string | null;
  } | null;
  gapBeats: number;
  crossfadeEnabled: number;
  keyShiftSemitones: number;
  tempoRatio: number;
  markedLineCount?: number;
}

interface SongListProps {
  items: SongListItem[];
  onReorder: (items: SongListItem[]) => void;
  onRemove: (itemId: string) => void;
  onEditTransition?: (itemId: string) => void;
  onSelectSong?: (itemId: string) => void;
  readOnly?: boolean;
  className?: string;
}

interface SortableSongItemProps {
  item: SongListItem;
  index: number;
  onRemove: (itemId: string) => void;
  onEditTransition?: (itemId: string) => void;
  onSelectSong?: (itemId: string) => void;
  readOnly?: boolean;
}

function SortableSongItem({
  item,
  index,
  onRemove,
  onEditTransition,
  onSelectSong,
  readOnly = false,
}: SortableSongItemProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: item.id, disabled: readOnly });

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

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={cn(
        "group",
        isDragging && "opacity-50"
      )}
    >
      <Card className="border-border/50 hover:border-border transition-colors">
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
                aria-label={`Drag to reorder song ${index + 1}`}
              >
                <GripVertical className="size-4 text-muted-foreground" />
              </Button>
            )}

            {/* Song number */}
            <span className="text-sm font-medium text-muted-foreground w-6 text-center shrink-0">
              {index + 1}
            </span>

            {/* Song info */}
            <div
              className="flex-1 min-w-0 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
              role={onSelectSong ? "button" : undefined}
              tabIndex={onSelectSong ? 0 : undefined}
              onClick={() => onSelectSong?.(item.id)}
              onKeyDown={(e) => {
                if (onSelectSong && (e.key === "Enter" || e.key === " ")) {
                  e.preventDefault();
                  onSelectSong(item.id);
                }
              }}
              aria-label={onSelectSong ? `Select ${item.song?.title || "song"}` : undefined}
            >
              <div className="flex items-center gap-2">
                <h4 className="font-medium text-sm truncate">
                  {item.song?.title || "Unknown Song"}
                </h4>
                {hasMarkedLines && (
                  <Badge variant="outline" className="text-xs shrink-0 text-amber-600 border-amber-500/50">
                    {item.markedLineCount} marked
                  </Badge>
                )}
              </div>
              <div className="flex items-center gap-2 text-xs text-muted-foreground mt-0.5">
                <span className="flex items-center gap-1">
                  <Music className="size-3" />
                  {item.song?.composer || item.song?.lyricist || "Unknown Artist"}
                </span>
                {item.recording?.durationSeconds && (
                  <span className="flex items-center gap-1">
                    <Clock className="size-3" />
                    {formatDuration(item.recording.durationSeconds)}
                  </span>
                )}
                {item.song?.musicalKey && (
                  <span>• {item.song.musicalKey}</span>
                )}
              </div>
            </div>

            {/* Transition indicator (for non-first songs) */}
            {index > 0 && (
              <Button
                variant="ghost"
                size="sm"
                className="shrink-0 text-xs text-muted-foreground hidden sm:flex"
                onClick={() => onEditTransition?.(item.id)}
                aria-label={`Edit transition before ${item.song?.title || "song"}: gap ${item.gapBeats} beats${item.crossfadeEnabled ? ", crossfade" : ""}`}
              >
                Gap: {item.gapBeats} beats
                {item.crossfadeEnabled ? " + crossfade" : ""}
                <ChevronRight className="size-3 ml-1" />
              </Button>
            )}

            {/* Remove button */}
            {!readOnly && (
              <Button
                variant="ghost"
                size="icon-sm"
                className="shrink-0 opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity"
                onClick={() => onRemove(item.id)}
                aria-label={`Remove ${item.song?.title || "song"}`}
              >
                <Trash2 className="size-4 text-destructive" />
              </Button>
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
  onSelectSong,
  readOnly = false,
  className,
}: SongListProps) {
  const [localItems, setLocalItems] = useState(items);
  const prevItemIdsRef = useRef<string | null>(null);

  useEffect(() => {
    const currentItemIds = items.map((i) => i.id).join(",");
    if (prevItemIdsRef.current !== currentItemIds) {
      prevItemIdsRef.current = currentItemIds;
      setLocalItems(items);
    }
  }, [items]);

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
        <p className="text-muted-foreground">No songs in this songset</p>
        <p className="text-sm text-muted-foreground mt-1">
          Tap the + button to add songs
        </p>
      </div>
    );
  }

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      onDragEnd={handleDragEnd}
    >
      <SortableContext
        items={localItems.map((item) => item.id)}
        strategy={verticalListSortingStrategy}
      >
        <div className={cn("space-y-2", className)} role="list" aria-label="Songs">
          {localItems.map((item, index) => (
            <SortableSongItem
              key={item.id}
              item={item}
              index={index}
              onRemove={onRemove}
              onEditTransition={onEditTransition}
              onSelectSong={onSelectSong}
              readOnly={readOnly}
            />
          ))}
        </div>
      </SortableContext>
    </DndContext>
  );
}
