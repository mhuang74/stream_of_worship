"use client";

import { useEffect, useState, useCallback, useMemo } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { SongsetRow, type SongsetRowProps } from "@/components/songset/SongsetRow";
import { RenderState } from "@/components/songset/RenderStatusBadge";
import { ChevronLeft, ChevronRight, Loader2 } from "lucide-react";
import { useLocale } from "@/hooks/useLocale";
import { useConnectivity } from "@/hooks/useConnectivity";
import {
  listOfflineRecords,
  removeOfflineSongset,
} from "@/lib/offline/offline-index";
import { matchCachedArtifact } from "@/lib/offline/artifact-cache";
import {
  downloadOfflineArtifacts,
  NoArtifactsError,
} from "@/lib/offline/download-offline";

/**
 * /worship list (issue #211 follow-up descope): lists every songset with a
 * rendered lyrics video. Boots purely from the IndexedDB offline index (the
 * SW pre-caches this document, so the offline boot has zero connectivity);
 * when online, all songset summaries are fetched page-by-page (no hard cap —
 * the server caps a single page at 100) and merged with the offline index.
 * Default filter is "Ready for Offline Worship" (downloaded sets); toggling
 * All shows every fetched set with a rendered video.
 */

type WorshipRow = SongsetRowProps & { latestRenderJobId: string | null };

interface ApiSongset {
  id: string;
  name: string;
  description: string | null;
  createdAt: string;
  updatedAt: string;
  renderState: RenderState;
  itemCount: number;
  durationSeconds: number | null;
  latestRenderJobId: string | null;
  lastFailedRenderJobId: string | null;
  lastCompletedRenderJobId: string | null;
  renderErrorMessage: string | null;
  failedAt: string | null;
  themes: string[];
}

interface ApiResponse {
  songsets: ApiSongset[];
  total: number;
}

const PAGE_SIZE = 20;

function transformSongsets(songsets: ApiSongset[]): WorshipRow[] {
  return songsets.map((songset) => ({
    id: songset.id,
    name: songset.name,
    description: songset.description,
    itemCount: songset.itemCount,
    durationSeconds: songset.durationSeconds ?? undefined,
    updatedAt: new Date(songset.updatedAt),
    renderState: songset.renderState,
    latestRenderJobId: songset.latestRenderJobId,
    lastCompletedRenderJobId: songset.lastCompletedRenderJobId,
    renderErrorMessage: songset.renderErrorMessage,
    failedAt: songset.failedAt ? new Date(songset.failedAt) : null,
    themes: songset.themes ?? [],
    isOfflineAvailable: false,
    isArtifactsStale: songset.renderState === "stale",
  }));
}

/** Rows with a rendered lyrics video; stale renders stay (still playable). */
function hasRenderedVideo(row: WorshipRow): boolean {
  return row.lastCompletedRenderJobId != null;
}

export function WorshipClient() {
  const router = useRouter();
  const { t } = useLocale();
  const connectivity = useConnectivity();
  const [rows, setRows] = useState<WorshipRow[] | null>(null);
  const [filter, setFilter] = useState<"ready" | "all">("ready");
  const [page, setPage] = useState(1);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);

  // Offline boot: base rows from the IndexedDB index, works with zero
  // connectivity (the SW pre-cached this document).
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const records = await listOfflineRecords();
      if (cancelled) return;
      setRows(
        records.map((record) => ({
          id: record.songsetId,
          name: record.songsetName,
          description: null,
          itemCount: record.chapterContentHashes.length,
          updatedAt: new Date(record.cachedAt),
          renderState: "fresh" as RenderState,
          latestRenderJobId: record.renderJobId,
          lastCompletedRenderJobId: record.renderJobId,
          renderErrorMessage: null,
          failedAt: null,
          themes: [],
          isOfflineAvailable: true,
          isArtifactsStale: false,
        }))
      );
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Online fetch-merge: page through all songset summaries and merge the
  // offline index in (record present ⇒ offline available; cached renderJobId
  // no longer matching the songset's latest render ⇒ stale). ONE staleness
  // pass — this merge both adds server-only rows and marks downloaded rows
  // stale, replacing the old per-record fetch loop. A page
  // failure mid-loop keeps whatever loaded: offline-first surfaces never
  // hard-fail on network.
  useEffect(() => {
    if (connectivity !== "online") return;
    let cancelled = false;
    void (async () => {
      const fetched: ApiSongset[] = [];
      const limit = 100;
      try {
        for (let offset = 0; ; offset += limit) {
          const response = await fetch(`/api/songsets?limit=${limit}&offset=${offset}`);
          if (!response.ok) break;
          const data: ApiResponse = await response.json();
          fetched.push(...data.songsets);
          if (offset + data.songsets.length >= data.total) break;
        }
      } catch {
        // keep whatever loaded
      }
      if (cancelled || fetched.length === 0) return;
      const records = await listOfflineRecords();
      if (cancelled) return;
      const fetchedRows = transformSongsets(fetched).map((row) => {
        const record = records.find((r) => r.songsetId === row.id);
        if (!record) return row;
        return {
          ...row,
          isOfflineAvailable: true,
          isArtifactsStale:
            row.isArtifactsStale || record.renderJobId !== row.latestRenderJobId,
        };
      });
      // Offline-boot rows absent from the fetch (e.g. deleted server-side)
      // keep their place: merge by id, fetched data wins.
      setRows((prev) => {
        const byId = new Map(fetchedRows.map((row) => [row.id, row]));
        for (const row of prev ?? []) {
          if (!byId.has(row.id)) byId.set(row.id, row);
        }
        return [...byId.values()];
      });
    })();
    return () => {
      cancelled = true;
    };
  }, [connectivity]);

  // Byte verification (Q6): a downloaded row whose cached media is gone
  // downgrades to stale — the re-download menu item appears via
  // SongsetRow's staleness logic; no third badge state. Runs post-render so
  // the list is never blocked on Cache Storage reads.
  useEffect(() => {
    if (!rows) return;
    let cancelled = false;
    for (const row of rows) {
      if (!row.isOfflineAvailable || row.isArtifactsStale) continue;
      void (async () => {
        const kind = row.lastCompletedRenderJobId ? "mp4" : "mp3";
        const cached = await matchCachedArtifact(
          row.lastCompletedRenderJobId ?? "",
          kind
        );
        if (!cancelled && !cached) {
          setRows((prev) =>
            prev
              ? prev.map((r) =>
                  r.id === row.id ? { ...r, isArtifactsStale: true } : r
                )
              : prev
          );
        }
      })();
    }
    return () => {
      cancelled = true;
    };
  }, [rows]);

  const handlePlay = useCallback(
    (id: string) => {
      // Cached ⇒ offline copy (cache-first controller boot): a full document
      // navigation is deterministic regardless of connectivity — the SW
      // document route serves the pre-cached controller. SPA navigation
      // needs an RSC fetch that cannot be pre-cached and dead-ends offline
      // (issue #206's trap, at list granularity).
      const row = rows?.find((r) => r.id === id);
      if (row?.isOfflineAvailable || connectivity !== "online") {
        window.location.assign(`/songsets/${id}/play/controller`);
        return;
      }
      router.push(`/songsets/${id}/play/controller`);
    },
    [rows, connectivity, router]
  );

  const handleDownloadOffline = useCallback(
    async (id: string) => {
      const row = rows?.find((r) => r.id === id);
      // Row not in the fetched list (offline Ready view): the offline
      // record's renderJobId is the render to (re)fetch artifacts for.
      const renderJobId = row?.latestRenderJobId ?? row?.lastCompletedRenderJobId;
      if (!row || !renderJobId) return;

      setDownloadingId(id);
      const toastId = toast.loading(`${t("songsets.toast.downloadingOffline")} 0%`);
      try {
        await downloadOfflineArtifacts(
          { songsetId: id, songsetName: row.name, renderJobId },
          (percent) =>
            toast.loading(`${t("songsets.toast.downloadingOffline")} ${percent}%`, {
              id: toastId,
            })
        );
        toast.success(t("songsets.toast.offlineReady"), { id: toastId });
        setRows((prev) =>
          prev?.map((r) =>
            r.id === id
              ? {
                  ...r,
                  isOfflineAvailable: true,
                  isArtifactsStale: renderJobId !== r.latestRenderJobId,
                }
              : r
          ) ?? null
        );
      } catch (err) {
        if (err instanceof NoArtifactsError) {
          toast.error(t("audio.offline.noArtifacts"), { id: toastId });
        } else {
          toast.error(t("audio.offline.downloadFailed"), { id: toastId });
        }
      } finally {
        setDownloadingId(null);
      }
    },
    [rows, t]
  );

  const handleRemoveOffline = useCallback(
    async (id: string) => {
      try {
        // removeOfflineSongset invalidates the cached artifacts, then deletes
        // the index record (issue #203 semantics).
        await removeOfflineSongset(id);
        setRows((prev) =>
          prev?.map((row) =>
            row.id === id
              ? {
                  ...row,
                  isOfflineAvailable: false,
                  // Back to the no-record default: the render's own staleness
                  // survives, offline staleness goes.
                  isArtifactsStale: row.renderState === "stale",
                }
              : row
          ) ?? null
        );
        toast.success(t("songsets.toast.offlineRemoved"));
      } catch (err) {
        toast.error(
          err instanceof Error ? err.message : t("songsets.error.removeOfflineFailed")
        );
      }
    },
    [t]
  );

  const filteredRows = useMemo(() => {
    if (!rows) return null;
    const ready = rows.filter(
      (row) => row.isOfflineAvailable
    );
    if (filter === "ready") return ready;
    return rows.filter(hasRenderedVideo);
  }, [rows, filter]);

  const totalPages = filteredRows ? Math.max(1, Math.ceil(filteredRows.length / PAGE_SIZE)) : 1;
  const safePage = Math.min(page, totalPages);
  const pageRows = filteredRows?.slice(
    (safePage - 1) * PAGE_SIZE,
    safePage * PAGE_SIZE
  );

  const pageNumbers = useMemo(() => {
    const maxVisible = 5;
    if (totalPages <= maxVisible) {
      return Array.from({ length: totalPages }, (_, i) => i + 1);
    }
    const half = Math.floor(maxVisible / 2);
    let start = Math.max(1, safePage - half);
    const end = Math.min(totalPages, start + maxVisible - 1);
    if (end - start + 1 < maxVisible) {
      start = Math.max(1, end - maxVisible + 1);
    }
    return Array.from({ length: end - start + 1 }, (_, i) => start + i);
  }, [totalPages, safePage]);

  if (rows === null) {
    return (
      <div className="flex justify-center py-16">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (filter === "all" && filteredRows?.length === 0) {
    return (
      <div className="mx-auto max-w-2xl px-4 py-6 space-y-3">
        <h1 className="text-2xl font-bold">{t("worship.page.title")}</h1>
        <FilterToggle filter={filter} onFilterChange={setFilter} />
        <p className="py-16 text-center text-muted-foreground">
          {t("worship.empty.all")}
        </p>
      </div>
    );
  }

  if (filter === "ready" && filteredRows?.length === 0) {
    return (
      <div className="mx-auto max-w-2xl px-4 py-6 space-y-3">
        <h1 className="text-2xl font-bold">{t("worship.page.title")}</h1>
        <FilterToggle filter={filter} onFilterChange={setFilter} />
        <div className="py-12 text-center space-y-4">
          <p className="text-muted-foreground">{t("offline.empty")}</p>
          <Link
            href="/songsets"
            className="text-primary underline underline-offset-4 hover:text-primary/80"
          >
            {t("offline.emptyLink")}
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl px-4 py-6 space-y-3">
      <h1 className="text-2xl font-bold">{t("worship.page.title")}</h1>
      <FilterToggle filter={filter} onFilterChange={setFilter} />
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {pageRows?.map((row) => (
          <SongsetRow
            key={row.id}
            {...row}
            onPlay={() => handlePlay(row.id)}
            onDownloadOffline={() => handleDownloadOffline(row.id)}
            isOfflineDownloadInProgress={downloadingId === row.id}
            onRemoveOffline={() => handleRemoveOffline(row.id)}
          />
        ))}
      </div>
      {filteredRows != null && totalPages > 1 && (
        <nav
          aria-label={t("songsets.aria.pagination")}
          className="flex items-center justify-center gap-2 mt-6"
        >
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPage(safePage - 1)}
            disabled={safePage <= 1}
            aria-label={t("songsets.aria.previousPage")}
            data-testid="pagination-prev"
          >
            <ChevronLeft className="size-4" />
            {t("songsets.action.prev")}
          </Button>
          {pageNumbers.map((pageNum) => (
            <Button
              key={pageNum}
              variant={pageNum === safePage ? "default" : "outline"}
              size="icon-sm"
              onClick={() => setPage(pageNum)}
              aria-current={pageNum === safePage ? "page" : undefined}
              aria-label={`${t("songsets.aria.page")} ${pageNum}`}
              data-testid={`pagination-page-${pageNum}`}
            >
              {pageNum}
            </Button>
          ))}
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPage(safePage + 1)}
            disabled={safePage >= totalPages}
            aria-label={t("songsets.aria.nextPage")}
            data-testid="pagination-next"
          >
            {t("songsets.action.next")}
            <ChevronRight className="size-4" />
          </Button>
        </nav>
      )}
    </div>
  );
}

function FilterToggle({
  filter,
  onFilterChange,
}: {
  filter: "ready" | "all";
  onFilterChange: (filter: "ready" | "all") => void;
}) {
  const { t } = useLocale();
  return (
    <div className="flex items-center gap-2">
      <Button
        variant={filter === "ready" ? "default" : "outline"}
        size="sm"
        onClick={() => onFilterChange("ready")}
      >
        {t("worship.filter.ready")}
      </Button>
      <Button
        variant={filter === "all" ? "default" : "outline"}
        size="sm"
        onClick={() => onFilterChange("all")}
      >
        {t("worship.filter.all")}
      </Button>
    </div>
  );
}
