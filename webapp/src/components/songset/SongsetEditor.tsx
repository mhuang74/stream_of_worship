"use client";

import { useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { SongList, SongListItem } from "./SongList";
import { TransitionPanel, TransitionSettings } from "./TransitionPanel";
import { RenderStateButton, RenderState } from "./RenderStateButton";
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
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
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
  X,
  Monitor,
  Plus,
  Loader2,
} from "lucide-react";

export interface SongsetEditorProps {
  songset: {
    id: string;
    name: string;
    description: string | null;
    renderState: RenderState;
    renderProgress?: number;
    isArtifactsStale?: boolean;
    latestRenderJobId: string | null;
    lastFailedRenderJobId: string | null;
    updatedAt: string;
  };
  items: SongListItem[];
  onUpdateItems: (items: SongListItem[]) => Promise<void>;
  onRemoveItem: (itemId: string) => Promise<void>;
  onUpdateTransition: (itemId: string, settings: TransitionSettings) => Promise<void>;
  onRender: () => void;
  onPlay: () => void;
  onRetry: () => void;
  onUpdateDescription: (description: string) => Promise<void>;
  onDuplicate: () => Promise<void>;
  onDelete: () => Promise<void>;
  onShare: () => void;
  onAddSongs: () => void;
  className?: string;
}

export function SongsetEditor({
  songset,
  items,
  onUpdateItems,
  onRemoveItem,
  onUpdateTransition,
  onRender,
  onPlay,
  onRetry,
  onUpdateDescription,
  onDuplicate,
  onDelete,
  onShare,
  onAddSongs,
  className,
}: SongsetEditorProps) {
  const router = useRouter();
  const [isStaleBannerDismissed, setIsStaleBannerDismissed] = useState(false);
  const [isEditDescriptionOpen, setIsEditDescriptionOpen] = useState(false);
  const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);
  const [isTransitionSheetOpen, setIsTransitionSheetOpen] = useState(false);
  const [selectedTransitionItem, setSelectedTransitionItem] = useState<SongListItem | null>(null);
  const [descriptionValue, setDescriptionValue] = useState(songset.description || "");
  const [isSavingDescription, setIsSavingDescription] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [isDuplicating, setIsDuplicating] = useState(false);

  // Calculate total marked lines across all songs
  const totalMarkedLines = items.reduce((sum, item) => sum + (item.markedLineCount ?? 0), 0);

  // Handle back navigation
  const handleBack = () => {
    router.push("/songsets");
  };

  // Handle reorder
  const handleReorder = useCallback(
    async (newItems: SongListItem[]) => {
      try {
        await onUpdateItems(newItems);
        toast.success("Song order updated");
      } catch {
        toast.error("Failed to update song order");
      }
    },
    [onUpdateItems]
  );

  // Handle remove
  const handleRemove = useCallback(
    async (itemId: string) => {
      try {
        await onRemoveItem(itemId);
        toast.success("Song removed");
      } catch {
        toast.error("Failed to remove song");
      }
    },
    [onRemoveItem]
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
        toast.success("Transition updated");
        setIsTransitionSheetOpen(false);
        setSelectedTransitionItem(null);
      } catch {
        toast.error("Failed to update transition");
      }
    },
    [selectedTransitionItem, onUpdateTransition]
  );

  // Handle description save
  const handleSaveDescription = async () => {
    setIsSavingDescription(true);
    try {
      await onUpdateDescription(descriptionValue);
      setIsEditDescriptionOpen(false);
      toast.success("Description updated");
    } catch {
      toast.error("Failed to update description");
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
      toast.success("Songset deleted");
    } catch {
      toast.error("Failed to delete songset");
      setIsDeleting(false);
      setIsDeleteDialogOpen(false);
    }
  };

  // Handle duplicate
  const handleDuplicate = async () => {
    setIsDuplicating(true);
    try {
      await onDuplicate();
      toast.success("Songset duplicated");
    } catch {
      toast.error("Failed to duplicate songset");
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
        title: prevItem.song?.title || "Unknown",
        key: prevItem.song?.musicalKey,
        tempoBpm: prevItem.recording?.tempoBpm,
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
            aria-label="Go back"
          >
            <ArrowLeft className="size-5" />
          </Button>

          <div className="flex-1 min-w-0">
            <h1 className="font-semibold text-lg truncate">{songset.name}</h1>
            <p className="text-xs text-muted-foreground">
              {items.length} {items.length === 1 ? "song" : "songs"}
            </p>
          </div>

          {/* Render state button */}
          <RenderStateButton
            state={songset.renderState}
            progress={songset.renderProgress}
            onRender={onRender}
            onPlay={onPlay}
            onRetry={onRetry}
            size="sm"
          />

          {/* Overflow menu */}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" aria-label="More options">
                <MoreVertical className="size-5" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-48">
              <DropdownMenuItem onClick={onRender}>
                <RefreshCw className="size-4 mr-2" />
                Render
              </DropdownMenuItem>
              <DropdownMenuItem onClick={onPlay}>
                <Play className="size-4 mr-2" />
                Play
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={() => setIsEditDescriptionOpen(true)}>
                <Edit className="size-4 mr-2" />
                Edit description
              </DropdownMenuItem>
              <DropdownMenuItem onClick={handleDuplicate} disabled={isDuplicating}>
                {isDuplicating ? (
                  <Loader2 className="size-4 mr-2 animate-spin" />
                ) : (
                  <Copy className="size-4 mr-2" />
                )}
                Duplicate
              </DropdownMenuItem>
              <DropdownMenuItem onClick={onShare}>
                <Share2 className="size-4 mr-2" />
                Share
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                onClick={() => setIsDeleteDialogOpen(true)}
                className="text-destructive focus:text-destructive"
              >
                <Trash2 className="size-4 mr-2" />
                Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </header>

      {/* Stale banner */}
      {songset.isArtifactsStale && !isStaleBannerDismissed && (
        <Alert variant="warning" className="rounded-none border-x-0">
          <AlertTriangle className="size-4" />
          <AlertTitle>Artifacts out of date</AlertTitle>
          <AlertDescription className="flex items-center gap-2 flex-wrap">
            <span>Songs have been modified since the last render.</span>
            <div className="flex items-center gap-2">
              <Button size="sm" variant="outline" onClick={onRender}>
                Re-render
              </Button>
              <Button size="sm" variant="ghost" onClick={onPlay}>
                Play anyway
              </Button>
              <Button
                size="icon-sm"
                variant="ghost"
                onClick={() => setIsStaleBannerDismissed(true)}
                aria-label="Dismiss"
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
                {totalMarkedLines} marked lines
              </Badge>
              <span className="text-sm text-muted-foreground flex items-center gap-1">
                <Monitor className="size-3" />
                Open on desktop for text edit
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
          onSelectSong={() => {}}
        />
      </main>

      {/* FAB for adding songs */}
      <Button
        size="icon-lg"
        className="fixed bottom-20 right-4 lg:bottom-8 lg:right-8 shadow-lg"
        onClick={onAddSongs}
        aria-label="Add songs"
      >
        <Plus className="size-6" />
      </Button>

      {/* Transition Sheet */}
      {selectedTransitionItem && (
        <TransitionPanel
          isOpen={isTransitionSheetOpen}
          onOpenChange={setIsTransitionSheetOpen}
          fromSong={getPreviousSong(selectedTransitionItem)}
          toSong={{
            title: selectedTransitionItem.song?.title || "Unknown",
            key: selectedTransitionItem.song?.musicalKey,
            tempoBpm: selectedTransitionItem.recording?.tempoBpm,
          }}
          settings={getTransitionSettings(selectedTransitionItem)}
          onChange={handleTransitionSave}
        />
      )}

      {/* Edit Description Dialog */}
      <Dialog open={isEditDescriptionOpen} onOpenChange={setIsEditDescriptionOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit Description</DialogTitle>
            <DialogDescription>
              Update the description for this songset.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label htmlFor="description">Description</Label>
              <Textarea
                id="description"
                value={descriptionValue}
                onChange={(e) => setDescriptionValue(e.target.value)}
                placeholder="e.g., Easter service songs"
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
              Cancel
            </Button>
            <Button
              onClick={handleSaveDescription}
              disabled={isSavingDescription}
            >
              {isSavingDescription ? (
                <>
                  <Loader2 className="size-4 mr-2 animate-spin" />
                  Saving...
                </>
              ) : (
                "Save"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <Dialog open={isDeleteDialogOpen} onOpenChange={setIsDeleteDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Songset</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete &quot;{songset.name}&quot;? This action cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setIsDeleteDialogOpen(false)}
              disabled={isDeleting}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleDelete}
              disabled={isDeleting}
            >
              {isDeleting ? (
                <>
                  <Loader2 className="size-4 mr-2 animate-spin" />
                  Deleting...
                </>
              ) : (
                "Delete"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
