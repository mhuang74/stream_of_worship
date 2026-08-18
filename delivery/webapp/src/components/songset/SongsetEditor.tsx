"use client";

import { useState, useCallback, useEffect } from "react";
import { useRouter } from "next/navigation";
import { SongList, SongListItem } from "./SongList";
import { useFavoriteToggle } from "@/hooks/useFavoriteToggle";
import { TransitionPanel, TransitionSettings } from "./TransitionPanel";
import { RenderStatusBadge, RenderState } from "./RenderStatusBadge";
import { SONGSET_MAX_SONGS, SONGSET_MAX_DURATION_SECONDS } from "@/lib/constants";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Alert, AlertDescription, AlertTitle, AlertAction } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { useLocale } from "@/hooks/useLocale";
import { toast } from "sonner";
import { getRenderFailureText } from "@/lib/render/error-message";
import {
  ArrowLeft,
  MoreVertical,
  Play,
  RefreshCw,
  Edit,
  Copy,
  Trash2,
  Share2,
  AlertTriangle,
  AlertCircle,
  X,
  Monitor,
  Plus,
  Loader2,
  FileAudio,
  FileVideo,
} from "lucide-react";

export interface SongsetEditorProps {
  songset: {
    id: string;
    name: string;
    description: string | null;
    renderState: RenderState;
    isArtifactsStale?: boolean;
    latestRenderJobId: string | null;
    lastFailedRenderJobId: string | null;
    lastCompletedRenderJobId: string | null;
    renderErrorMessage?: string | null;
    failedAt?: string | null;
    updatedAt: string;
  };
  items: SongListItem[];
  onUpdateItems: (items: SongListItem[]) => void;
  onRemoveItem: (itemId: string) => Promise<void>;
  onUpdateTransition: (itemId: string, settings: TransitionSettings) => Promise<void>;
  onRender: () => void;
  onPlay: () => void;
  onRetry: () => void;
  onUpdateDescription: (description: string) => Promise<void>;
  onDuplicate: () => Promise<void>;
  onDelete: () => Promise<void>;
  onShare: () => void;
  onDownloadAudio?: () => void;
  onDownloadVideo?: () => void;
  onAddSongs: () => void;
  isRemoving?: boolean;
  className?: string;
  highlightSongId?: string | null;
  onHighlightConsumed?: () => void;
}

export function SongsetEditor({
  songset,
  items,
  onUpdateItems,
  onRemoveItem,
  onUpdateTransition,
  onRender,
  onPlay,
  onUpdateDescription,
  onDuplicate,
  onDelete,
  onShare,
  onDownloadAudio,
  onDownloadVideo,
  onAddSongs,
  isRemoving = false,
  className,
  highlightSongId,
  onHighlightConsumed,
}: SongsetEditorProps) {
  const router = useRouter();
  const { t } = useLocale();
  const [isStaleBannerDismissed, setIsStaleBannerDismissed] = useState(false);
  const [isEditDescriptionOpen, setIsEditDescriptionOpen] = useState(false);
  const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);
  const [isTransitionSheetOpen, setIsTransitionSheetOpen] = useState(false);
  const [selectedTransitionItem, setSelectedTransitionItem] = useState<SongListItem | null>(null);
  const [descriptionValue, setDescriptionValue] = useState(songset.description || "");
  const [isSavingDescription, setIsSavingDescription] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [isDuplicating, setIsDuplicating] = useState(false);
  const { favoriteIds, setFavoriteIds, toggleFavorite } = useFavoriteToggle();

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/favorites");
        if (!res.ok) return;
        const data = await res.json();
        if (!cancelled && Array.isArray(data.songIds)) {
          setFavoriteIds(new Set(data.songIds));
        }
      } catch {
        // Ignore; favorites simply stay empty until toggled.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [setFavoriteIds]);

  // Calculate total marked lines across all songs
  const totalMarkedLines = items.reduce((sum, item) => sum + (item.markedLineCount ?? 0), 0);
  const totalDurationSeconds = items.reduce(
    (sum, item) => sum + (item.recording?.durationSeconds ?? 0),
    0
  );
  const isDurationOverLimit = totalDurationSeconds > SONGSET_MAX_DURATION_SECONDS;

  // Handle back navigation
  const handleBack = () => {
    router.push("/songsets");
  };

  // Handle reorder
  const handleReorder = useCallback(
    (newItems: SongListItem[]) => {
      onUpdateItems(newItems);
    },
    [onUpdateItems]
  );

  // Handle remove
  const handleRemove = useCallback(
    async (itemId: string) => {
      try {
        await onRemoveItem(itemId);
        toast.success(t("songsets.toast.songRemoved"));
      } catch {
        toast.error(t("songsets.error.removeSongFailed"));
      }
    },
    [onRemoveItem, t]
  );

  // Handle transition edit
  const handleEditTransition = useCallback((itemId: string) => {
    const item = items.find((i) => i.id === itemId);
    if (item) {
      setSelectedTransitionItem(item);
      setIsTransitionSheetOpen(true);
    }
  }, [items]);

  // Handle transition save
  const handleTransitionSave = useCallback(
    async (settings: TransitionSettings) => {
      if (!selectedTransitionItem) return;
      try {
        await onUpdateTransition(selectedTransitionItem.id, settings);
        toast.success(t("songsets.toast.transitionUpdated"));
        setIsTransitionSheetOpen(false);
        setSelectedTransitionItem(null);
      } catch {
        toast.error(t("songsets.error.updateTransitionFailed"));
      }
    },
    [selectedTransitionItem, onUpdateTransition, t]
  );

  // Handle description save
  const handleSaveDescription = async () => {
    setIsSavingDescription(true);
    try {
      await onUpdateDescription(descriptionValue);
      setIsEditDescriptionOpen(false);
      toast.success(t("songsets.toast.descriptionUpdated"));
    } catch {
      toast.error(t("songsets.error.updateDescriptionFailed"));
    } finally {
      setIsSavingDescription(false);
    }
  };

  // Handle delete
  const handleDelete = async () => {
    setIsDeleting(true);
    try {
      await onDelete();
      router.push("/songsets");
      toast.success(t("songsets.toast.songsetDeleted"));
    } catch {
      toast.error(t("songsets.error.deleteFailed"));
      setIsDeleting(false);
      setIsDeleteDialogOpen(false);
    }
  };

  // Handle duplicate
  const handleDuplicate = async () => {
    setIsDuplicating(true);
    try {
      await onDuplicate();
      toast.success(t("songsets.toast.songsetDuplicated"));
    } catch {
      toast.error(t("songsets.error.duplicateFailed"));
    } finally {
      setIsDuplicating(false);
    }
  };

  // Get transition settings from selected item
  const getTransitionSettings = (item: SongListItem): TransitionSettings => ({
    gapBeats: item.gapBeats,
    crossfadeEnabled: item.crossfadeEnabled === 1,
    crossfadeDurationSeconds: item.crossfadeEnabled === 1 ? 2 : 0,
    keyShiftSemitones: item.keyShiftSemitones,
    tempoRatio: item.tempoRatio,
  });

  // Find previous song for transition context
  const getPreviousSong = (item: SongListItem) => {
    const index = items.findIndex((i) => i.id === item.id);
    if (index > 0) {
      const prevItem = items[index - 1];
      return {
        title: prevItem.song?.title || t("songsets.unknown"),
        key: prevItem.song?.effectiveKey ?? prevItem.song?.musicalKey,
        tempoBpm: prevItem.recording?.tempoBpm,
        exitPitchClass:
          prevItem.song?.effectiveKeyEndPitchClass ??
          prevItem.recording?.effectiveKeyEndPitchClass,
      };
    }
    return undefined;
  };

  return (
    <div className={cn("min-h-screen flex flex-col", className)}>
      {/* App Bar */}
      <header className="sticky top-0 z-40 bg-background border-b">
        <div className="flex items-center gap-2 p-4">
          <Button
            variant="ghost"
            size="icon"
            onClick={handleBack}
            aria-label={t("songsets.aria.goBack")}
          >
            <ArrowLeft className="size-5" />
          </Button>

          <div className="flex-1 min-w-0">
            <h1 className="font-semibold text-lg truncate">{songset.name}</h1>
            <p className="text-xs text-muted-foreground">
              {items.length} {t(items.length === 1 ? "songsets.unit.song" : "songsets.unit.songs")}
              {isDurationOverLimit && (
                <Badge variant="outline" className="ml-2 text-amber-600 border-amber-500/50 text-xs">
                  {t("songsets.overDurationLimit")}
                </Badge>
              )}
            </p>
          </div>

          {/* Render status badge */}
          <RenderStatusBadge
            state={songset.renderState}
            errorMessage={songset.renderErrorMessage}
            failedAt={songset.failedAt ? new Date(songset.failedAt) : null}
          />

          {/* Overflow menu */}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" aria-label={t("songsets.aria.moreOptions")}>
                <MoreVertical className="size-5" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-48">
              <DropdownMenuItem onClick={onRender}>
                <RefreshCw className="size-4 mr-2" />
                {t("songsets.action.render")}
              </DropdownMenuItem>
              <DropdownMenuItem onClick={onPlay}>
                <Play className="size-4 mr-2" />
                {t("songsets.action.play")}
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={() => setIsEditDescriptionOpen(true)}>
                <Edit className="size-4 mr-2" />
                {t("songsets.action.editDescription")}
              </DropdownMenuItem>
              <DropdownMenuItem onClick={handleDuplicate} disabled={isDuplicating}>
                {isDuplicating ? (
                  <Loader2 className="size-4 mr-2 animate-spin" />
                ) : (
                  <Copy className="size-4 mr-2" />
                )}
                {t("songsets.action.duplicate")}
              </DropdownMenuItem>
              <DropdownMenuItem onClick={onShare}>
                <Share2 className="size-4 mr-2" />
                {t("songsets.action.share")}
              </DropdownMenuItem>
              <DropdownMenuItem
                onClick={onDownloadAudio}
                disabled={!songset.lastCompletedRenderJobId}
              >
                <FileAudio className="size-4 mr-2" />
                {t("songsets.action.downloadAudio")}
              </DropdownMenuItem>
              <DropdownMenuItem
                onClick={onDownloadVideo}
                disabled={!songset.lastCompletedRenderJobId}
              >
                <FileVideo className="size-4 mr-2" />
                {t("songsets.action.downloadVideo")}
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                onClick={() => setIsDeleteDialogOpen(true)}
                className="text-destructive focus:text-destructive"
              >
                <Trash2 className="size-4 mr-2" />
                {t("songsets.action.delete")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </header>

      {/* Render failure alert */}
      {songset.renderState === "failed" && (
        <Alert variant="destructive" className="rounded-none border-x-0">
          <AlertCircle className="size-4" />
          <AlertTitle>{t("songsets.alert.renderFailed")}</AlertTitle>
          <AlertDescription>
            {getRenderFailureText(
              songset.renderErrorMessage,
              songset.failedAt ? new Date(songset.failedAt) : null
            )}
          </AlertDescription>
          <AlertAction>
            <Button size="sm" variant="outline" onClick={onRender}>
              {t("songsets.action.renderAgain")}
            </Button>
          </AlertAction>
        </Alert>
      )}

      {/* Stale banner */}
      {songset.isArtifactsStale && !isStaleBannerDismissed && (
        <Alert variant="destructive" className="rounded-none border-x-0">
          <AlertTriangle className="size-4" />
          <AlertTitle>{t("songsets.alert.artifactsStale")}</AlertTitle>
          <AlertDescription className="flex items-center gap-2 flex-wrap">
            <span>{t("songsets.alert.staleDescription")}</span>
            <div className="flex items-center gap-2">
              <Button size="sm" variant="outline" onClick={onRender}>
                {t("songsets.action.reRender")}
              </Button>
              <Button size="sm" variant="ghost" onClick={onPlay}>
                {t("songsets.action.playAnyway")}
              </Button>
              <Button
                size="icon-sm"
                variant="ghost"
                onClick={() => setIsStaleBannerDismissed(true)}
                aria-label={t("songsets.aria.dismiss")}
              >
                <X className="size-4" />
              </Button>
            </div>
          </AlertDescription>
        </Alert>
      )}

      {/* Marked lines badge (phone only) */}
      {totalMarkedLines > 0 && (
        <div className="lg:hidden">
          <Alert className="rounded-none border-x-0 bg-amber-50 dark:bg-amber-950/20">
            <AlertTriangle className="size-4 text-amber-600" />
            <AlertDescription className="flex items-center gap-2">
              <Badge variant="outline" className="text-amber-600 border-amber-500/50">
                {totalMarkedLines} {t("songsets.markedLines")}
              </Badge>
              <span className="text-sm text-muted-foreground flex items-center gap-1">
                <Monitor className="size-3" />
                {t("songsets.markedLinesHint")}
              </span>
            </AlertDescription>
          </Alert>
        </div>
      )}

      {/* Main content */}
      <main className="flex-1 p-4 pb-24">
        {/* Description */}
        {songset.description && (
          <p className="text-sm text-muted-foreground mb-4">
            {songset.description}
          </p>
        )}

        {/* Song list */}
        <SongList
          items={items}
          onReorder={handleReorder}
          onRemove={handleRemove}
          onEditTransition={handleEditTransition}
          isRemoving={isRemoving}
          songsetId={songset.id}
          highlightSongId={highlightSongId}
          onHighlightConsumed={onHighlightConsumed}
          favoriteIds={favoriteIds}
          onToggleFavorite={(songId) => void toggleFavorite(songId)}
        />
      </main>

      {/* FAB for adding songs */}
      {items.length < SONGSET_MAX_SONGS ? (
        <Button
          size="icon-lg"
          className="fixed bottom-20 right-4 lg:bottom-8 lg:right-8 shadow-lg"
          onClick={onAddSongs}
          aria-label={t("songsets.aria.addSongs")}
        >
          <Plus className="size-6" />
        </Button>
      ) : (
        <div className="fixed bottom-20 right-4 lg:bottom-8 lg:right-8 bg-muted text-muted-foreground text-sm px-4 py-2 rounded-full">
          {SONGSET_MAX_SONGS} {t("songsets.maxSongsReached")}
        </div>
      )}

      {/* Transition Sheet */}
      {selectedTransitionItem && (
        <TransitionPanel
          isOpen={isTransitionSheetOpen}
          onOpenChange={setIsTransitionSheetOpen}
          fromSong={getPreviousSong(selectedTransitionItem)}
          toSong={{
            title: selectedTransitionItem.song?.title || t("songsets.unknown"),
            key: selectedTransitionItem.song?.effectiveKey ?? selectedTransitionItem.song?.musicalKey,
            tempoBpm: selectedTransitionItem.recording?.tempoBpm,
            entryPitchClass:
              selectedTransitionItem.song?.effectiveKeyStartPitchClass ??
              selectedTransitionItem.recording?.effectiveKeyStartPitchClass,
          }}
          settings={getTransitionSettings(selectedTransitionItem)}
          onChange={handleTransitionSave}
        />
      )}

      {/* Edit Description Dialog */}
      <Dialog open={isEditDescriptionOpen} onOpenChange={setIsEditDescriptionOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("songsets.dialog.editDescriptionTitle")}</DialogTitle>
            <DialogDescription>
              {t("songsets.dialog.editDescriptionDescription")}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label htmlFor="description">{t("songsets.label.description")}</Label>
              <Textarea
                id="description"
                value={descriptionValue}
                onChange={(e) => setDescriptionValue(e.target.value)}
                placeholder={t("songsets.placeholder.description")}
                rows={3}
                disabled={isSavingDescription}
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setIsEditDescriptionOpen(false)}
              disabled={isSavingDescription}
            >
              {t("songsets.action.cancel")}
            </Button>
            <Button
              onClick={handleSaveDescription}
              disabled={isSavingDescription}
            >
              {isSavingDescription ? (
                <>
                  <Loader2 className="size-4 mr-2 animate-spin" />
                  {t("songsets.loading.saving")}
                </>
              ) : (
                t("songsets.action.save")
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <Dialog open={isDeleteDialogOpen} onOpenChange={setIsDeleteDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("songsets.dialog.deleteTitle")}</DialogTitle>
            <DialogDescription>
              {t("songsets.dialog.deleteNamedDescription").replace(
                "{name}",
                songset.name
              )}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setIsDeleteDialogOpen(false)}
              disabled={isDeleting}
            >
              {t("songsets.action.cancel")}
            </Button>
            <Button
              variant="destructive"
              onClick={handleDelete}
              disabled={isDeleting}
            >
              {isDeleting ? (
                <>
                  <Loader2 className="size-4 mr-2 animate-spin" />
                  {t("songsets.loading.deleting")}
                </>
              ) : (
                t("songsets.action.delete")
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
