"use client";

import { useState, useCallback } from "react";
import { SongsetRow } from "./SongsetRow";
import { RenderState } from "./RenderStatusBadge";
import { Button } from "@/components/ui/button";
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
import { Plus, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { SongsetListSkeleton } from "./SongsetListSkeleton";

export interface Songset {
  id: string;
  name: string;
  description?: string | null;
  itemCount: number;
  durationSeconds?: number;
  updatedAt: Date;
  renderState: RenderState;
  isOfflineAvailable?: boolean;
  isArtifactsStale?: boolean;
  latestRenderJobId: string | null;
}

interface SongsetListProps {
  songsets: Songset[];
  isLoading?: boolean;
  error?: string | null;
  onCreateSongset?: (name: string, description?: string) => Promise<void>;
  onRender?: (id: string) => void;
  onPlay?: (id: string) => void;
  onRename?: (id: string, name: string) => Promise<void>;
  onDuplicate?: (id: string) => Promise<void>;
  onShare?: (id: string) => void;
  onDownloadAudio?: (id: string) => void;
  onDownloadVideo?: (id: string) => void;
  onDelete?: (id: string) => Promise<void>;
  className?: string;
}

export function SongsetList({
  songsets,
  isLoading = false,
  error = null,
  onCreateSongset,
  onRender,
  onPlay,
  onRename,
  onDuplicate,
  onShare,
  onDownloadAudio,
  onDownloadVideo,
  onDelete,
  className,
}: SongsetListProps) {
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);
  const [newSongsetName, setNewSongsetName] = useState("");
  const [newSongsetDescription, setNewSongsetDescription] = useState("");
  const [isCreating, setIsCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);
  const [songsetToDelete, setSongsetToDelete] = useState<string | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  const [isRenameDialogOpen, setIsRenameDialogOpen] = useState(false);
  const [songsetToRename, setSongsetToRename] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [isRenaming, setIsRenaming] = useState(false);

  const handleCreate = useCallback(async () => {
    if (!newSongsetName.trim()) return;

    setIsCreating(true);
    setCreateError(null);

    try {
      await onCreateSongset?.(newSongsetName.trim(), newSongsetDescription.trim() || undefined);
      setIsCreateDialogOpen(false);
      setNewSongsetName("");
      setNewSongsetDescription("");
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "Failed to create songset");
    } finally {
      setIsCreating(false);
    }
  }, [newSongsetName, newSongsetDescription, onCreateSongset]);

  const handleDelete = useCallback(async () => {
    if (!songsetToDelete) return;

    setIsDeleting(true);
    try {
      await onDelete?.(songsetToDelete);
      setIsDeleteDialogOpen(false);
      setSongsetToDelete(null);
    } catch (err) {
      console.error("Failed to delete songset:", err);
    } finally {
      setIsDeleting(false);
    }
  }, [songsetToDelete, onDelete]);

  const handleRename = useCallback(async () => {
    if (!songsetToRename || !renameValue.trim()) return;

    setIsRenaming(true);
    try {
      await onRename?.(songsetToRename, renameValue.trim());
      setIsRenameDialogOpen(false);
      setSongsetToRename(null);
      setRenameValue("");
    } catch (err) {
      console.error("Failed to rename songset:", err);
    } finally {
      setIsRenaming(false);
    }
  }, [songsetToRename, renameValue, onRename]);

  const openDeleteDialog = useCallback((id: string) => {
    setSongsetToDelete(id);
    setIsDeleteDialogOpen(true);
  }, []);

  const openRenameDialog = useCallback((id: string, currentName: string) => {
    setSongsetToRename(id);
    setRenameValue(currentName);
    setIsRenameDialogOpen(true);
  }, []);

  if (isLoading) {
    return (
      <div className={cn("space-y-4", className)}>
        <SongsetListSkeleton />
      </div>
    );
  }

  if (error) {
    return (
      <div className={cn("text-center py-8", className)}>
        <p className="text-destructive">{error}</p>
        <Button
          variant="outline"
          className="mt-4"
          onClick={() => window.location.reload()}
        >
          Retry
        </Button>
      </div>
    );
  }

  if (songsets.length === 0) {
    return (
      <div className={cn("text-center py-12", className)}>
        <p className="text-muted-foreground mb-4">
          No songsets yet. Create one to get started.
        </p>
        <Button onClick={() => setIsCreateDialogOpen(true)}>
          <Plus className="size-4 mr-2" />
          Create Songset
        </Button>

        {/* Create Dialog */}
        <Dialog open={isCreateDialogOpen} onOpenChange={setIsCreateDialogOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Create New Songset</DialogTitle>
              <DialogDescription>
                Enter a name for your new songset.
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4 py-4">
              <div className="space-y-2">
                <Label htmlFor="name">Name</Label>
                <Input
                  id="name"
                  value={newSongsetName}
                  onChange={(e) => setNewSongsetName(e.target.value)}
                  placeholder="e.g., Sunday Worship"
                  disabled={isCreating}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="description">Description (optional)</Label>
                <Input
                  id="description"
                  value={newSongsetDescription}
                  onChange={(e) => setNewSongsetDescription(e.target.value)}
                  placeholder="e.g., Easter service songs"
                  disabled={isCreating}
                />
              </div>
              {createError && (
                <p className="text-sm text-destructive">{createError}</p>
              )}
            </div>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => setIsCreateDialogOpen(false)}
                disabled={isCreating}
              >
                Cancel
              </Button>
              <Button
                onClick={handleCreate}
                disabled={isCreating || !newSongsetName.trim()}
              >
                {isCreating ? (
                  <>
                    <Loader2 className="size-4 mr-2 animate-spin" />
                    Creating...
                  </>
                ) : (
                  "Create"
                )}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    );
  }

  return (
    <>
      <div className={cn("space-y-3", className)}>
        {songsets.map((songset) => (
          <SongsetRow
            key={songset.id}
            {...songset}
            onRender={() => onRender?.(songset.id)}
            onPlay={() => onPlay?.(songset.id)}
            onRename={() => openRenameDialog(songset.id, songset.name)}
            onDuplicate={() => onDuplicate?.(songset.id)}
            onShare={() => onShare?.(songset.id)}
            onDownloadAudio={() => onDownloadAudio?.(songset.id)}
            onDownloadVideo={() => onDownloadVideo?.(songset.id)}
            onDelete={() => openDeleteDialog(songset.id)}
          />
        ))}
      </div>

      {/* FAB for creating new songset */}
      <Button
        size="icon-lg"
        className="fixed bottom-20 right-4 lg:bottom-8 lg:right-8 shadow-lg"
        onClick={() => setIsCreateDialogOpen(true)}
        aria-label="Create new songset"
      >
        <Plus className="size-6" />
      </Button>

      {/* Create Dialog */}
      <Dialog open={isCreateDialogOpen} onOpenChange={setIsCreateDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create New Songset</DialogTitle>
            <DialogDescription>
              Enter a name for your new songset.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label htmlFor="name">Name</Label>
              <Input
                id="name"
                value={newSongsetName}
                onChange={(e) => setNewSongsetName(e.target.value)}
                placeholder="e.g., Sunday Worship"
                disabled={isCreating}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="description">Description (optional)</Label>
              <Input
                id="description"
                value={newSongsetDescription}
                onChange={(e) => setNewSongsetDescription(e.target.value)}
                placeholder="e.g., Easter service songs"
                disabled={isCreating}
              />
            </div>
            {createError && (
              <p className="text-sm text-destructive">{createError}</p>
            )}
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setIsCreateDialogOpen(false)}
              disabled={isCreating}
            >
              Cancel
            </Button>
            <Button
              onClick={handleCreate}
              disabled={isCreating || !newSongsetName.trim()}
            >
              {isCreating ? (
                <>
                  <Loader2 className="size-4 mr-2 animate-spin" />
                  Creating...
                </>
              ) : (
                "Create"
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
              Are you sure you want to delete this songset? This action cannot be undone.
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

      {/* Rename Dialog */}
      <Dialog open={isRenameDialogOpen} onOpenChange={setIsRenameDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Rename Songset</DialogTitle>
            <DialogDescription>
              Enter a new name for this songset.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label htmlFor="rename">Name</Label>
              <Input
                id="rename"
                value={renameValue}
                onChange={(e) => setRenameValue(e.target.value)}
                placeholder="Songset name"
                disabled={isRenaming}
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setIsRenameDialogOpen(false)}
              disabled={isRenaming}
            >
              Cancel
            </Button>
            <Button
              onClick={handleRename}
              disabled={isRenaming || !renameValue.trim()}
            >
              {isRenaming ? (
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
    </>
  );
}
