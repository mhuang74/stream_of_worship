"use client";

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { SongCard, SongCardData } from "@/components/songset/SongCard";
import { Button, buttonVariants } from "@/components/ui/button";
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
import { Checkbox } from "@/components/ui/checkbox";
import { Heart, Loader2, ChevronLeft, ChevronRight, ListChecks } from "lucide-react";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import { useLocale } from "@/hooks/useLocale";
import { useOfflineRedirect } from "@/hooks/useOfflineRedirect";
import { useFavoriteToggle } from "@/hooks/useFavoriteToggle";
import { useSongPlayback } from "@/hooks/useSongPlayback";
import { toSongCardData } from "@/lib/song-card-data";
import { COMPLETION_THRESHOLD } from "@/lib/constants";

interface FavoritesClientProps {
  initialSongs: SongCardData[];
  initialTotal: number;
  currentPage: number;
  pageSize: number;
}

export function FavoritesClient({
  initialSongs,
  initialTotal,
  currentPage,
  pageSize,
}: FavoritesClientProps) {
  const router = useRouter();
  const { t } = useLocale();
  useOfflineRedirect();
  const [songs, setSongs] = useState<SongCardData[]>(initialSongs);
  const [total, setTotal] = useState(initialTotal);
  const [page, setPage] = useState(currentPage);
  const [isLoading, setIsLoading] = useState(false);
  const { toggleFavorite } = useFavoriteToggle(
    new Set(initialSongs.map((s) => s.id))
  );

  // --- Select mode: client state; survives pagination; session-only (component
  // unmount clears it); no cap. Selection order tracks the favorites-list
  // order it was picked in, which becomes the songset songIds order. ---
  const [isSelectMode, setIsSelectMode] = useState(false);
  const [selectedSongIds, setSelectedSongIds] = useState<string[]>([]);
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);
  const [newSongsetName, setNewSongsetName] = useState("");
  const [newSongsetDescription, setNewSongsetDescription] = useState("");
  const [isCreating, setIsCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const toggleSelected = useCallback((songId: string) => {
    setSelectedSongIds((prev) =>
      prev.includes(songId)
        ? prev.filter((id) => id !== songId)
        : [...prev, songId]
    );
  }, []);

  const isSelected = useCallback(
    (songId: string) => selectedSongIds.includes(songId),
    [selectedSongIds]
  );

  const handleCreateSongset = useCallback(async () => {
    const name = newSongsetName.trim();
    if (!name || selectedSongIds.length === 0) return;

    setIsCreating(true);
    setCreateError(null);

    try {
      const response = await fetch("/api/songsets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          description: newSongsetDescription.trim() || undefined,
          songIds: selectedSongIds,
        }),
      });

      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || t("songsets.error.createFailed"));
      }

      const songset: { id?: string } = await response.json();
      setIsCreateDialogOpen(false);
      setSelectedSongIds([]);
      setIsSelectMode(false);
      setNewSongsetName("");
      setNewSongsetDescription("");

      if (!songset?.id) {
        toast.error(t("songsets.toast.createdButEditorFailed"));
        router.push("/songsets");
        return;
      }
      router.push(`/songsets/${songset.id}?new=true`);
    } catch (err) {
      // Keep the selection so the user can retry without re-picking.
      setCreateError(
        err instanceof Error ? err.message : t("songsets.error.createFailed")
      );
      toast.error(t("songsets.error.createFailed"));
    } finally {
      setIsCreating(false);
    }
  }, [newSongsetName, newSongsetDescription, selectedSongIds, t, router]);

  const resolveSong = useCallback(
    (songId: string) => {
      const song = songs.find((s) => s.id === songId);
      if (!song) return null;
      const recording = song.recordings[0];
      return {
        id: song.id,
        title: song.title,
        artist: song.composer || song.lyricist || t("browse.unknownArtist"),
        recording: recording
          ? {
              hashPrefix: recording.hashPrefix,
              contentHash: recording.contentHash,
              durationSeconds: recording.durationSeconds,
            }
          : null,
      };
    },
    [songs, t]
  );

  const { playingSongId, previewLoadingSongId, handlePlay } = useSongPlayback({
    resolveSong,
    noAudioMessage: t("browse.noAudioAvailable"),
    failedToLoadMessage: t("browse.failedToLoadPreview"),
  });

  const handlePageChange = useCallback((newPage: number) => {
    setPage(newPage);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, []);

  const skipInitialFetchRef = useRef(true);

  const initialSongsRef = useRef(initialSongs);
  const initialTotalRef = useRef(initialTotal);
  const tRef = useRef(t);

  useEffect(() => {
    initialSongsRef.current = initialSongs;
    initialTotalRef.current = initialTotal;
    tRef.current = t;
  }, [initialSongs, initialTotal, t]);

  useEffect(() => {
    if (skipInitialFetchRef.current) {
      skipInitialFetchRef.current = false;
      return; // don't refetch SSR page 1 on mount
    }
    let cancelled = false;
    async function loadPage() {
      setIsLoading(true);
      try {
        const offset = (page - 1) * pageSize;
        const params = new URLSearchParams({
          limit: String(pageSize),
          offset: String(offset),
          favoritesOnly: "1",
          visibilityStatus: "published,review",
        });
        const res = await fetch(`/api/songs?${params.toString()}`);
        if (!res.ok) throw new Error("Failed to load favorites");
        const data = await res.json();
        if (cancelled) return;
        setSongs(toSongCardData(data.songs));
        setTotal(data.total);
      } catch {
        if (!cancelled) {
          toast.error(tRef.current("favorites.loadFailed"));
          setSongs(initialSongsRef.current); // fall back to SSR-provided data
          setTotal(initialTotalRef.current);
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }
    loadPage();
    return () => {
      cancelled = true;
    };
  }, [page, pageSize]);

  // Reconcile client page state with the RSC-provided currentPage after a
  // browser back/forward navigation (RSC restores currentPage, but client
  // page state may be stale).
  useEffect(() => {
    if (page !== currentPage) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setPage(currentPage);
    }
  }, [currentPage]);

  // Keep the URL in sync with the current page.
  useEffect(() => {
    const params = new URLSearchParams();
    if (page > 1) params.set("page", String(page));
    const qs = params.toString();
    router.replace(qs ? `/favorites?${qs}` : "/favorites");
  }, [page, router]);

  const handleToggleFavorite = useCallback(
    async (songId: string) => {
      const ok = await toggleFavorite(songId);
      if (ok) {
        setSongs((prev) => prev.filter((s) => s.id !== songId));
        setTotal((prev) => Math.max(0, prev - 1));
      }
    },
    [toggleFavorite]
  );

  // If the current page empties out after unfavoriting, fall back to page 1.
  useEffect(() => {
    if (!isLoading && songs.length === 0 && total > 0 && page > 1) {
      const timer = setTimeout(() => handlePageChange(1), 0);
      return () => clearTimeout(timer);
    }
  }, [isLoading, songs.length, total, page, handlePageChange]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const pageNumbers = useMemo(() => {
    const maxVisible = 5;
    if (totalPages <= maxVisible) {
      return Array.from({ length: totalPages }, (_, i) => i + 1);
    }
    const half = Math.floor(maxVisible / 2);
    let start = Math.max(1, page - half);
    const end = Math.min(totalPages, start + maxVisible - 1);
    if (end - start + 1 < maxVisible) {
      start = Math.max(1, end - maxVisible + 1);
    }
    return Array.from({ length: end - start + 1 }, (_, i) => start + i);
  }, [page, totalPages]);

  if (songs.length === 0 && !isLoading) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-center">
        <Heart className="size-8 text-muted-foreground mb-2" />
        <p className="font-medium">{t("favorites.empty.title")}</p>
        <p className="text-sm text-muted-foreground mt-1 max-w-md">
          {t("favorites.empty.description").replace(
            "${percent}",
            String(Math.round(COMPLETION_THRESHOLD * 100))
          )}
        </p>
        <Link href="/songsets" className={cn(buttonVariants(), "mt-6")}>
          {t("favorites.empty.action")}
        </Link>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl px-4 py-6">
      <div className="flex items-start justify-between mb-1">
        <h1 className="text-2xl font-bold">{t("favorites.title")}</h1>
        <Button
          variant={isSelectMode ? "default" : "outline"}
          size="sm"
          onClick={() => {
            setIsSelectMode((prev) => !prev);
            // Leaving select mode discards the session-only selection.
            setSelectedSongIds([]);
            setCreateError(null);
          }}
          data-testid="select-mode-toggle"
          aria-pressed={isSelectMode}
        >
          <ListChecks className="size-4 mr-1.5" />
          {isSelectMode
            ? t("favorites.select.exit")
            : t("favorites.select.enter")}
        </Button>
      </div>
      <p className="text-sm text-muted-foreground mb-4">
        {total}{" "}
        {t(total === 1 ? "favorites.count.singular" : "favorites.count.plural")}
      </p>

      {isSelectMode && (
        <div className="flex items-center justify-between mb-4" data-testid="select-mode-bar">
          <p className="text-sm text-muted-foreground" data-testid="selection-count">
            {t("favorites.select.selectedCount").replace(
              "${n}",
              String(selectedSongIds.length)
            )}
          </p>
          <Button
            onClick={() => {
              setNewSongsetName("");
              setNewSongsetDescription("");
              setCreateError(null);
              setIsCreateDialogOpen(true);
            }}
            disabled={selectedSongIds.length === 0 || isCreating}
            data-testid="create-songset-from-selection"
          >
            {t("favorites.select.createCta").replace(
              "${n}",
              String(selectedSongIds.length)
            )}
          </Button>
        </div>
      )}

      {isLoading ? (
        <div className="flex justify-center py-20">
          <Loader2 className="size-8 animate-spin text-muted-foreground" />
        </div>
      ) : (
        <div
          className="grid grid-cols-1 md:grid-cols-2 gap-2"
          data-testid="favorites-list"
        >
          {songs.map((song) =>
            isSelectMode ? (
              <label
                key={song.id}
                className="flex items-start gap-2 cursor-pointer"
                data-testid={`song-select-row-${song.id}`}
              >
                <Checkbox
                  checked={isSelected(song.id)}
                  onCheckedChange={() => toggleSelected(song.id)}
                  className="mt-2.5"
                  aria-label={song.title}
                  data-testid={`song-select-checkbox-${song.id}`}
                />
                <div className="flex-1 pointer-events-none">
                  <SongCard song={song} isFavorite />
                </div>
              </label>
            ) : (
              <SongCard
                key={song.id}
                song={song}
                isFavorite
                onToggleFavorite={handleToggleFavorite}
                onPlay={handlePlay}
                isPlaying={playingSongId === song.id}
                isPreviewLoading={previewLoadingSongId === song.id}
              />
            )
          )}
        </div>
      )}

      {totalPages > 1 && (
        <nav
          aria-label={t("favorites.pagination.ariaLabel")}
          className="flex items-center justify-center gap-2 mt-6"
        >
          <Button
            variant="outline"
            size="sm"
            onClick={() => handlePageChange(page - 1)}
            disabled={page <= 1 || isLoading}
            aria-label={t("favorites.pagination.previous")}
            data-testid="pagination-prev"
          >
            <ChevronLeft className="size-4" />
            {t("favorites.pagination.prevLabel")}
          </Button>

          {pageNumbers.map((pageNum) => (
            <Button
              key={pageNum}
              variant={pageNum === page ? "default" : "outline"}
              size="icon-sm"
              onClick={() => handlePageChange(pageNum)}
              disabled={isLoading}
              aria-current={pageNum === page ? "page" : undefined}
              aria-label={t("favorites.pagination.page").replace(
                "${n}",
                String(pageNum)
              )}
              data-testid={`pagination-page-${pageNum}`}
            >
              {pageNum}
            </Button>
          ))}

          <Button
            variant="outline"
            size="sm"
            onClick={() => handlePageChange(page + 1)}
            disabled={page >= totalPages || isLoading}
            aria-label={t("favorites.pagination.next")}
            data-testid="pagination-next"
          >
            {t("favorites.pagination.nextLabel")}
            <ChevronRight className="size-4" />
          </Button>
        </nav>
      )}

      {/* Create songset from selection (Select mode CTA). */}
      <Dialog open={isCreateDialogOpen} onOpenChange={setIsCreateDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("songsets.dialog.createTitle")}</DialogTitle>
            <DialogDescription>
              {t("favorites.select.createDialogDescription").replace(
                "${n}",
                String(selectedSongIds.length)
              )}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label htmlFor="favorites-songset-name">{t("songsets.label.name")}</Label>
              <Input
                id="favorites-songset-name"
                value={newSongsetName}
                onChange={(e) => setNewSongsetName(e.target.value)}
                placeholder={t("songsets.placeholder.name")}
                disabled={isCreating}
                data-testid="create-songset-name-input"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="favorites-songset-description">
                {t("songsets.label.descriptionOptional")}
              </Label>
              <Input
                id="favorites-songset-description"
                value={newSongsetDescription}
                onChange={(e) => setNewSongsetDescription(e.target.value)}
                placeholder={t("songsets.placeholder.description")}
                disabled={isCreating}
                data-testid="create-songset-description-input"
              />
            </div>
            {createError && (
              <p className="text-sm text-destructive" data-testid="create-songset-error">
                {createError}
              </p>
            )}
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setIsCreateDialogOpen(false)}
              disabled={isCreating}
              data-testid="create-songset-cancel"
            >
              {t("songsets.action.cancel")}
            </Button>
            <Button
              onClick={handleCreateSongset}
              disabled={isCreating || !newSongsetName.trim() || selectedSongIds.length === 0}
              data-testid="create-songset-submit"
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
