"use client";

import { useState, useCallback, useMemo } from "react";
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
import { Plus, Loader2, Search, X, ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { useLocale } from "@/hooks/useLocale";
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
  lastCompletedRenderJobId: string | null;
  renderErrorMessage?: string | null;
  failedAt?: Date | null;
  themes?: string[];
}

interface SongsetListProps {
  songsets: Songset[];
  isLoading?: boolean;
  error?: string | null;
  onCreateSongset?: (name: string, description?: string) => Promise<void>;
  onRender?: (id: string) => void;
  onPlay?: (id: string) => void;
  onRetry?: (id: string) => void;
  onRename?: (id: string, name: string) => Promise<void>;
  onDuplicate?: (id: string) => Promise<void>;
  onShare?: (id: string) => void;
  onDownloadAudio?: (id: string) => void;
  onDownloadVideo?: (id: string) => void;
  onDelete?: (id: string) => Promise<void>;
  currentPage?: number;
  totalPages?: number;
  onPageChange?: (page: number) => void;
  search?: string;
  onSearchChange?: (value: string) => void;
  onSearch?: () => void;
  isSearching?: boolean;
  className?: string;
}

export function SongsetList({
  songsets,
  isLoading = false,
  error = null,
  onCreateSongset,
  onRender,
  onPlay,
  onRetry,
  onRename,
  onDuplicate,
  onShare,
  onDownloadAudio,
  onDownloadVideo,
  onDelete,
  currentPage = 1,
  totalPages = 1,
  onPageChange,
  search = "",
  onSearchChange,
  onSearch,
  isSearching = false,
  className,
}: SongsetListProps) {
  const { t } = useLocale();
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

  const pageNumbers = useMemo(() => {
    const maxVisible = 5;
    if (totalPages <= maxVisible) {
      return Array.from({ length: totalPages }, (_, i) => i + 1);
    }

    const half = Math.floor(maxVisible / 2);
    let start = Math.max(1, currentPage - half);
    const end = Math.min(totalPages, start + maxVisible - 1);

    if (end - start + 1 < maxVisible) {
      start = Math.max(1, end - maxVisible + 1);
    }

    return Array.from({ length: end - start + 1 }, (_, i) => start + i);
  }, [currentPage, totalPages]);

  const renderSearchBar = (containerClassName?: string) => (
    <div className={cn("flex items-center gap-2", containerClassName)}>
      <div className="relative flex-1">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground pointer-events-none" />
        <Input
          type="text"
          value={search}
          onChange={(e) => onSearchChange?.(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              onSearch?.();
            }
          }}
          placeholder={t("songsets.searchPlaceholder")}
          className="pl-9 pr-10"
          aria-label={t("songsets.aria.searchSongsets")}
          data-testid="songset-search-input"
        />
        {search.length > 0 && (
          <Button
            variant="ghost"
            size="icon-sm"
            className="absolute right-2 top-1/2 -translate-y-1/2"
            onClick={() => {
              onSearchChange?.("");
              onSearch?.();
            }}
            aria-label={t("songsets.aria.clearSearch")}
            data-testid="songset-clear-search-button"
          >
            <X className="size-4" />
          </Button>
        )}
        {isSearching && (
          <Loader2
            className="absolute right-3 top-1/2 -translate-y-1/2 size-4 animate-spin text-muted-foreground"
            aria-hidden="true"
          />
        )}
        {isSearching && (
          <span className="sr-only" role="status" aria-live="polite">{t("songsets.loading.searching")}</span>
        )}
      </div>
      <Button
        variant="default"
        size="default"
        onClick={() => onSearch?.()}
        aria-label={t("songsets.aria.search")}
        data-testid="songset-search-button"
      >
        <Search className="size-4 mr-2" />
        {t("songsets.action.search")}
      </Button>
    </div>
  );

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
      setCreateError(err instanceof Error ? err.message : t("songsets.error.createFailed"));
    } finally {
      setIsCreating(false);
    }
  }, [newSongsetName, newSongsetDescription, onCreateSongset, t]);

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
          {t("songsets.action.retry")}
        </Button>
      </div>
    );
  }

  if (songsets.length === 0) {
    const isSearchActive = search.trim().length > 0;
    return (
      <div className={cn("text-center py-12", className)}>
        {renderSearchBar("mb-4")}
        <p className="text-muted-foreground mb-4">
          {isSearchActive
            ? t("songsets.empty.searchNoMatch")
            : t("songsets.empty.noSongsets")}
        </p>
        {!isSearchActive && (
          <Button onClick={() => setIsCreateDialogOpen(true)}>
            <Plus className="size-4 mr-2" />
            {t("songsets.action.createSongset")}
          </Button>
        )}

        {/* Create Dialog */}
        <Dialog open={isCreateDialogOpen} onOpenChange={setIsCreateDialogOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{t("songsets.dialog.createTitle")}</DialogTitle>
              <DialogDescription>
                {t("songsets.dialog.createDescription")}
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4 py-4">
              <div className="space-y-2">
                <Label htmlFor="name">{t("songsets.label.name")}</Label>
                <Input
                  id="name"
                  value={newSongsetName}
                  onChange={(e) => setNewSongsetName(e.target.value)}
                  placeholder={t("songsets.placeholder.name")}
                  disabled={isCreating}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="description">{t("songsets.label.descriptionOptional")}</Label>
                <Input
                  id="description"
                  value={newSongsetDescription}
                  onChange={(e) => setNewSongsetDescription(e.target.value)}
                  placeholder={t("songsets.placeholder.description")}
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
                {t("songsets.action.cancel")}
              </Button>
              <Button
                onClick={handleCreate}
                disabled={isCreating || !newSongsetName.trim()}
              >
                {isCreating ? (
                  <>
                    <Loader2 className="size-4 mr-2 animate-spin" />
                    {t("songsets.loading.creating")}
                  </>
                ) : (
                  t("songsets.action.create")
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
      {renderSearchBar("mb-4")}

      <div className={cn("grid grid-cols-1 md:grid-cols-2 gap-3", className)}>
        {songsets.map((songset) => (
          <SongsetRow
            key={songset.id}
            {...songset}
            onRender={() => onRender?.(songset.id)}
            onPlay={() => onPlay?.(songset.id)}
            onRetry={() => onRetry?.(songset.id)}
            onRename={() => openRenameDialog(songset.id, songset.name)}
            onDuplicate={() => onDuplicate?.(songset.id)}
            onShare={() => onShare?.(songset.id)}
            onDownloadAudio={() => onDownloadAudio?.(songset.id)}
            onDownloadVideo={() => onDownloadVideo?.(songset.id)}
            onDelete={() => openDeleteDialog(songset.id)}
          />
        ))}
      </div>

      {totalPages > 1 && (
        <nav
          aria-label={t("songsets.aria.pagination")}
          className="flex items-center justify-center gap-2 mt-6"
        >
          <Button
            variant="outline"
            size="sm"
            onClick={() => onPageChange?.(currentPage - 1)}
            disabled={currentPage <= 1}
            aria-label={t("songsets.aria.previousPage")}
            data-testid="pagination-prev"
          >
            <ChevronLeft className="size-4" />
            {t("songsets.action.prev")}
          </Button>

          {pageNumbers.map((pageNum) => (
            <Button
              key={pageNum}
              variant={pageNum === currentPage ? "default" : "outline"}
              size="icon-sm"
              onClick={() => onPageChange?.(pageNum)}
              aria-current={pageNum === currentPage ? "page" : undefined}
              aria-label={`${t("songsets.aria.page")} ${pageNum}`}
              data-testid={`pagination-page-${pageNum}`}
            >
              {pageNum}
            </Button>
          ))}

          <Button
            variant="outline"
            size="sm"
            onClick={() => onPageChange?.(currentPage + 1)}
            disabled={currentPage >= totalPages}
            aria-label={t("songsets.aria.nextPage")}
            data-testid="pagination-next"
          >
            {t("songsets.action.next")}
            <ChevronRight className="size-4" />
          </Button>
        </nav>
      )}

      {/* FAB for creating new songset */}
      <Button
        size="icon-lg"
        className="fixed bottom-20 right-4 lg:bottom-8 lg:right-8 shadow-lg"
        onClick={() => setIsCreateDialogOpen(true)}
        aria-label={t("songsets.aria.createNewSongset")}
      >
        <Plus className="size-6" />
      </Button>

      {/* Create Dialog */}
      <Dialog open={isCreateDialogOpen} onOpenChange={setIsCreateDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("songsets.dialog.createTitle")}</DialogTitle>
            <DialogDescription>
              {t("songsets.dialog.createDescription")}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label htmlFor="name">{t("songsets.label.name")}</Label>
              <Input
                id="name"
                value={newSongsetName}
                onChange={(e) => setNewSongsetName(e.target.value)}
                placeholder={t("songsets.placeholder.name")}
                disabled={isCreating}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="description">{t("songsets.label.descriptionOptional")}</Label>
              <Input
                id="description"
                value={newSongsetDescription}
                onChange={(e) => setNewSongsetDescription(e.target.value)}
                placeholder={t("songsets.placeholder.description")}
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
              {t("songsets.action.cancel")}
            </Button>
            <Button
              onClick={handleCreate}
              disabled={isCreating || !newSongsetName.trim()}
            >
              {isCreating ? (
                <>
                  <Loader2 className="size-4 mr-2 animate-spin" />
                  {t("songsets.loading.creating")}
                </>
              ) : (
                t("songsets.action.create")
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
              {t("songsets.dialog.deleteDescription")}
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

      {/* Rename Dialog */}
      <Dialog open={isRenameDialogOpen} onOpenChange={setIsRenameDialogOpen}>
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
              {t("songsets.action.cancel")}
            </Button>
            <Button
              onClick={handleRename}
              disabled={isRenaming || !renameValue.trim()}
            >
              {isRenaming ? (
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
    </>
  );
}
