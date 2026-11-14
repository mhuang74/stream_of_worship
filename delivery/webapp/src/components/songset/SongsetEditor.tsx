"use client";

import { useState, useCallback, useEffect } from "react";
import { SongList, SongListItem } from "./SongList";
import { useFavoriteToggle } from "@/hooks/useFavoriteToggle";
import { TransitionPanel, TransitionSettings } from "./TransitionPanel";
import type { RenderState } from "./RenderStatusBadge";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ThemeLabel, toSongTheme } from "./ThemeLabel";
import { Alert, AlertDescription, AlertTitle, AlertAction } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { formatClockDuration, formatShortDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useLocale } from "@/hooks/useLocale";
import { toast } from "sonner";
import { getRenderFailureText } from "@/lib/render/error-message";
import { useSongsetListBack } from "@/hooks/useSongsetListBack";
import {
  ArrowLeft,
  MoreVertical,
  Play,
  Headphones,
  RefreshCw,
  Edit,
  Copy,
  Trash2,
  Share2,
  AlertTriangle,
  AlertCircle,
  X,
  XCircle,
  Monitor,
  Plus,
  Loader2,
  FileAudio,
  FileVideo,
  Clock,
  WifiOff,
} from "lucide-react";

export interface SongsetEditorProps {
  songset: {
    id: string;
    name: string;
    description: string | null;
    createdAt: string;
    renderState: RenderState;
    isArtifactsStale?: boolean;
    latestRenderJobId: string | null;
    lastFailedRenderJobId: string | null;
    lastCompletedRenderJobId: string | null;
    renderErrorMessage?: string | null;
    failedAt?: string | null;
    /** Estimated render duration of the in-flight job, in seconds. */
    latestRenderJobEstimatedTotalSeconds?: number | null;
    updatedAt: string;
  };
  items: SongListItem[];
  onUpdateItems: (items: SongListItem[]) => void;
  onRemoveItem: (itemId: string) => Promise<void>;
  onUpdateTransition: (itemId: string, settings: TransitionSettings) => Promise<void>;
  onEditArrangement?: (itemId: string) => void;
  onRender: () => void;
  /** Present ⇒ the kebab shows "Cancel render" instead of "Render" while a
   * job is active. */
  onCancelRender?: () => void;
  isCancellingRender?: boolean;
  onPlay: () => void;
  onListen: () => void;
  onRetry: () => void;
  onUpdateDescription: (description: string) => Promise<void>;
  onRename: (name: string) => Promise<void>;
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
  /** Slot for the offline download control (OfflineStatus), rendered by the
   * client host that owns the render-job state. */
  offlineStatusSlot?: React.ReactNode;
  /** True when the offline copy exists, so the header metadata line shows the
   * "Offline" badge. */
  isOfflineAvailable?: boolean;
}

export function SongsetEditor({
  songset,
  items,
  onUpdateItems,
  onRemoveItem,
  onUpdateTransition,
  onEditArrangement,
  onRender,
  onCancelRender,
  isCancellingRender = false,
  onPlay,
  onListen,
  onUpdateDescription,
  onRename,
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
  offlineStatusSlot,
  isOfflineAvailable = false,
}: SongsetEditorProps) {
  const backToList = useSongsetListBack();
  const { t, locale } = useLocale();
  const [isStaleBannerDismissed, setIsStaleBannerDismissed] = useState(false);
  const [isEditDescriptionOpen, setIsEditDescriptionOpen] = useState(false);
  const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);
  const [isTransitionSheetOpen, setIsTransitionSheetOpen] = useState(false);
  const [selectedTransitionItem, setSelectedTransitionItem] = useState<SongListItem | null>(null);
  const [descriptionValue, setDescriptionValue] = useState(songset.description || "");
  const [isSavingDescription, setIsSavingDescription] = useState(false);
  const [isRenameOpen, setIsRenameOpen] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [isSavingName, setIsSavingName] = useState(false);
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
  // Duration checks use arranged durations only. A stored-but-invalid
  // arrangement yields null and must never fall back to the original
  // Recording duration: Render is blocked with the analysis message instead.
  const hasUnknownDuration = items.some((item) => item.arrangedDurationSeconds == null);
  const totalDurationSeconds = items.reduce(
    (sum, item) => sum + (item.arrangedDurationSeconds ?? 0),
    0
  );
  const isDurationOverLimit =
    !hasUnknownDuration && totalDurationSeconds > SONGSET_MAX_DURATION_SECONDS;
  const themes = [...new Set(items.map((i) => i.recording?.theme).filter(Boolean))] as string[];
  // Worship playback requires a completed render. A stale render still has a
  // completed job, so stale is enabled by construction.
  const canWorship = Boolean(songset.lastCompletedRenderJobId);

  // Handle back navigation
  const handleBack = () => {
    backToList();
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

  // Handle rename save
  const handleRename = async () => {
    setIsSavingName(true);
    try {
      await onRename(renameValue.trim());
      setIsRenameOpen(false);
      toast.success(t("songsets.toast.renamed"));
    } catch {
      toast.error(t("songsets.error.renameFailed"));
    } finally {
      setIsSavingName(false);
    }
  };

  // Handle delete
  const handleDelete = async () => {
    setIsDeleting(true);
    try {
      await onDelete();
      backToList();
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
            {songset.description && (
              <p className="text-sm text-muted-foreground line-clamp-2 break-words mt-0.5">
                {songset.description}
              </p>
            )}
            {themes.length > 0 && (
              <div className="flex flex-wrap items-center gap-1 mt-1">
                {themes.map((theme) => {
                  const safeTheme = toSongTheme(theme);
                  return safeTheme ? <ThemeLabel key={theme} theme={safeTheme} /> : null;
                })}
              </div>
            )}
            <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
              <span>
                {items.length}{" "}
                {t(items.length === 1 ? "songsets.unit.song" : "songsets.unit.songs")}
              </span>
              {!hasUnknownDuration && (
                <span className="inline-flex items-center gap-1">
                  <Clock className="size-3" />
                  {formatClockDuration(totalDurationSeconds)}
                </span>
              )}
              {isDurationOverLimit && (
                <Badge variant="outline" className="text-amber-600 border-amber-500/50 text-xs">
                  {t("songsets.overDurationLimit")}
                </Badge>
              )}
              {hasUnknownDuration && (
                <Badge variant="outline" className="text-amber-600 border-amber-500/50 text-xs">
                  {t("arrangement.duration.unavailable")}
                </Badge>
              )}
            </p>
          </div>

          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                {/* Span keeps the tooltip reachable over the disabled button. */}
                <span className="inline-flex" data-testid="songset-worship-trigger">
                  <Button
                    variant="default"
                    size="sm"
                    className="shrink-0 gap-1.5"
                    onClick={onPlay}
                    disabled={!canWorship}
                    data-testid="songset-worship"
                    aria-label={
                      canWorship
                        ? t("songsets.action.worship")
                        : t("control.songsetNotRendered")
                    }
                  >
                    <Play className="size-4" />
                    <span className="hidden sm:inline">{t("songsets.action.worship")}</span>
                  </Button>
                </span>
              </TooltipTrigger>
              <TooltipContent side="top" className="max-w-80 whitespace-normal">
                {canWorship ? t("songsets.action.worship") : t("control.songsetNotRendered")}
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>

          <Button
            variant="outline"
            size="sm"
            className="shrink-0 gap-1.5"
            onClick={onListen}
            data-testid="songset-listen"
            title={t("songsets.action.listen")}
            aria-label={t("songsets.action.listen")}
          >
            <Headphones className="size-4" />
            <span className="hidden sm:inline">{t("songsets.action.listen")}</span>
          </Button>

          {/* Overflow menu */}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" aria-label={t("songsets.aria.moreOptions")}>
                <MoreVertical className="size-5" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-48">
              {songset.renderState === "rendering" && onCancelRender ? (
                <DropdownMenuItem onClick={onCancelRender} disabled={isCancellingRender}>
                  <XCircle className="size-4 mr-2" />
                  {t("songsets.action.cancelRender")}
                </DropdownMenuItem>
              ) : (
                <DropdownMenuItem onClick={onRender} disabled={hasUnknownDuration}>
                  <RefreshCw className="size-4 mr-2" />
                  {t("songsets.action.render")}
                </DropdownMenuItem>
              )}
              <DropdownMenuItem
                onClick={onPlay}
                disabled={!canWorship}
                aria-label={
                  canWorship
                    ? t("songsets.action.worship")
                    : `${t("songsets.action.worship")} — ${t("control.songsetNotRendered")}`
                }
              >
                <Play className="size-4 mr-2" />
                {t("songsets.action.worship")}
              </DropdownMenuItem>
              <DropdownMenuItem onClick={onListen}>
                <Headphones className="size-4 mr-2" />
                {t("songsets.action.listen")}
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                onClick={() => {
                  setRenameValue(songset.name);
                  setIsRenameOpen(true);
                }}
              >
                <Edit className="size-4 mr-2" />
                {t("songsets.action.rename")}
              </DropdownMenuItem>
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

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 pt-3 text-xs text-muted-foreground">
        <span>
          {t("songsets.createdPrefix")}
          {formatShortDateTime(new Date(songset.createdAt), locale)}
        </span>
        <span>
          {t("songsets.updatedPrefix")}
          {formatShortDateTime(new Date(songset.updatedAt), locale)}
        </span>
        {songset.renderState === "rendering" && (
          <span className="inline-flex items-center gap-1" data-testid="render-progress">
            <Loader2 className="size-3 animate-spin" />
            {songset.latestRenderJobEstimatedTotalSeconds
              ? `${t("render.badge.rendering")} · ${t("render.status.estimatedPrefix")}${Math.ceil(
                  songset.latestRenderJobEstimatedTotalSeconds / 60
                )} ${t("render.status.estimatedMinutes")}`
              : t("render.badge.rendering")}
          </span>
        )}
        {isOfflineAvailable && (
          <Badge variant="secondary" className="text-xs gap-1">
            <WifiOff className="size-3" />
            {t("songsets.badge.offline")}
          </Badge>
        )}
      </div>

      {/* Offline download control (moved from the deleted Play screen,
          issue #211 follow-up Q16): download/refresh the offline copy. */}
      {offlineStatusSlot}

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
            <Button size="sm" variant="outline" onClick={onRender} disabled={hasUnknownDuration}>
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
              <Button size="sm" variant="outline" onClick={onRender} disabled={hasUnknownDuration}>
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

      {/* Arrangement analysis unavailable/broken: Render is blocked and the
          stored arrangement must be repaired or reset, never silently replaced
          by the original Recording. */}
      {hasUnknownDuration && (
        <Alert variant="destructive" className="rounded-none border-x-0">
          <AlertTriangle className="size-4" />
          <AlertTitle>{t("arrangement.error.unavailableTitle")}</AlertTitle>
          <AlertDescription>{t("arrangement.error.unavailableOrStale")}</AlertDescription>
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
        {/* Song list */}
        <SongList
          items={items}
          onReorder={handleReorder}
          onRemove={handleRemove}
          onEditTransition={handleEditTransition}
          onEditArrangement={onEditArrangement}
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

      {/* Rename Dialog */}
      <Dialog open={isRenameOpen} onOpenChange={setIsRenameOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("songsets.dialog.renameTitle")}</DialogTitle>
            <DialogDescription>
              {t("songsets.dialog.renameDescription")}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label htmlFor="rename">{t("songsets.label.name")}</Label>
              <Input
                id="rename"
                value={renameValue}
                onChange={(e) => setRenameValue(e.target.value)}
                placeholder={t("songsets.placeholder.songsetName")}
                disabled={isSavingName}
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setIsRenameOpen(false)}
              disabled={isSavingName}
            >
              {t("songsets.action.cancel")}
            </Button>
            <Button
              onClick={handleRename}
              disabled={isSavingName || !renameValue.trim()}
            >
              {isSavingName ? (
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
