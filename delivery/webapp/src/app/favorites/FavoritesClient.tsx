"use client";

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { SongCard, SongCardData } from "@/components/songset/SongCard";
import { Button, buttonVariants } from "@/components/ui/button";
import { Heart, Loader2, ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import { useLocale } from "@/hooks/useLocale";
import { useFavoriteToggle } from "@/hooks/useFavoriteToggle";
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
  const [songs, setSongs] = useState<SongCardData[]>(initialSongs);
  const [total, setTotal] = useState(initialTotal);
  const [page, setPage] = useState(currentPage);
  const [isLoading, setIsLoading] = useState(false);
  const { toggleFavorite } = useFavoriteToggle(
    new Set(initialSongs.map((s) => s.id))
  );

  const handlePageChange = useCallback((newPage: number) => {
    setPage(newPage);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, []);

  const skipInitialFetchRef = useRef(true);

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
          toast.error(t("favorites.loadFailed"));
          setSongs(initialSongs); // fall back to SSR-provided data
          setTotal(initialTotal);
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }
    loadPage();
    return () => {
      cancelled = true;
    };
  }, [page, pageSize, currentPage, initialSongs.length, t]);

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
      <h1 className="text-2xl font-bold">{t("favorites.title")}</h1>
      <p className="text-sm text-muted-foreground mb-4">
        {total}{" "}
        {t(total === 1 ? "favorites.count.singular" : "favorites.count.plural")}
      </p>

      {isLoading ? (
        <div className="flex justify-center py-20">
          <Loader2 className="size-8 animate-spin text-muted-foreground" />
        </div>
      ) : (
        <div
          className="grid grid-cols-1 md:grid-cols-2 gap-2"
          data-testid="favorites-list"
        >
          {songs.map((song) => (
            <SongCard
              key={song.id}
              song={song}
              isFavorite
              onToggleFavorite={handleToggleFavorite}
            />
          ))}
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
    </div>
  );
}
