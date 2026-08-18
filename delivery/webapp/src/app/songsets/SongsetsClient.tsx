"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import dynamic from "next/dynamic";
import { SongsetList, Songset } from "@/components/songset/SongsetList";
import { RenderState } from "@/components/songset/RenderStatusBadge";
import { toast } from "sonner";
import { useLocale } from "@/hooks/useLocale";
import { sanitizeFilename, fetchSignedUrlAndDownload } from "@/lib/download";

const ShareDialog = dynamic(
  () => import("@/components/share/ShareDialog").then((m) => ({ default: m.ShareDialog })),
  { ssr: false }
);

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
}

interface ApiResponse {
  songsets: ApiSongset[];
  total: number;
}

function transformSongsets(songsets: ApiSongset[]): Songset[] {
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
    isOfflineAvailable: false,
    isArtifactsStale: songset.renderState === "stale",
  }));
}

interface SongsetsClientProps {
  initialData: ApiResponse;
  currentPage: number;
  pageSize: number;
  initialSearch: string;
}

export function SongsetsClient({
  initialData,
  currentPage,
  pageSize,
  initialSearch,
}: SongsetsClientProps) {
  const router = useRouter();
  const { t } = useLocale();
  const [songsets, setSongsets] = useState<Songset[]>(() => transformSongsets(initialData.songsets));
  const [total, setTotal] = useState(initialData.total);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [page, setPage] = useState(currentPage);
  const [search, setSearch] = useState(initialSearch);
  const [committedSearch, setCommittedSearch] = useState(initialSearch);
  const [shareDialogOpen, setShareDialogOpen] = useState(false);
  const [shareTarget, setShareTarget] = useState<{
    id: string; name: string; durationSeconds: number | null;
  } | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadSongsets() {
      try {
        setIsLoading(true);
        setError(null);

        const offset = (page - 1) * pageSize;
        const params = new URLSearchParams({
          limit: String(pageSize),
          offset: String(offset),
        });
        if (committedSearch.trim()) {
          params.set("search", committedSearch.trim());
        }

        const response = await fetch(`/api/songsets?${params}`);

        if (!response.ok) {
          if (response.status === 401) {
            throw new Error(t("songsets.error.signIn"));
          }
          throw new Error(t("songsets.error.loadFailed"));
        }

        const data: ApiResponse = await response.json();

        if (cancelled) return;

        setSongsets(transformSongsets(data.songsets));
        setTotal(data.total);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : t("songsets.error.loadFailed"));
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    }

    loadSongsets();

    return () => {
      cancelled = true;
    };
  }, [page, committedSearch, pageSize, refreshKey, t]);

  useEffect(() => {
    const params = new URLSearchParams();
    if (page > 1) params.set("page", String(page));
    if (committedSearch.trim()) params.set("search", committedSearch.trim());
    const qs = params.toString();
    router.replace(qs ? `/songsets?${qs}` : "/songsets");
  }, [page, committedSearch, router]);

  const refreshSongsets = useCallback(() => {
    setRefreshKey((k) => k + 1);
  }, []);

  const handlePageChange = useCallback((newPage: number) => {
    setPage(newPage);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, []);

  const handleSearchChange = useCallback((value: string) => {
    setSearch(value);
  }, []);

  const handleSearch = useCallback(() => {
    setCommittedSearch(search);
    setPage(1);
  }, [search]);

  const handleCreateSongset = useCallback(
    async (name: string, description?: string) => {
      const response = await fetch("/api/songsets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, description }),
      });

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.error || t("songsets.error.createFailed"));
      }

      let songset: { id?: string };
      try {
        songset = await response.json();
      } catch {
        refreshSongsets();
        toast.error(t("songsets.toast.createdButEditorFailed"));
        router.push("/songsets");
        return;
      }

      if (!songset?.id) {
        refreshSongsets();
        toast.error(t("songsets.toast.createdButEditorFailed"));
        router.push("/songsets");
        return;
      }

      refreshSongsets();
      toast.success(t("songsets.toast.created"));
      router.push(`/songsets/${songset.id}?new=true`);
    },
    [refreshSongsets, router, t]
  );

  const handleRender = useCallback((id: string) => {
    router.push(`/songsets/${id}/render`);
  }, [router]);

  const handlePlay = useCallback((id: string) => {
    router.push(`/songsets/${id}/play`);
  }, [router]);

  const handleRetry = useCallback((id: string) => {
    router.push(`/songsets/${id}/render`);
  }, [router]);

  const handleRename = useCallback(
    async (id: string, name: string) => {
      const response = await fetch(`/api/songsets/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.error || t("songsets.error.renameFailed"));
      }

      refreshSongsets();
      toast.success(t("songsets.toast.renamed"));
    },
    [refreshSongsets, t]
  );

  const handleDuplicate = useCallback(
    async (id: string) => {
      const response = await fetch(`/api/songsets/${id}/duplicate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: t("songsets.defaultDuplicateName"),
          description: null,
        }),
      });

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.error || t("songsets.error.duplicateFailed"));
      }

      refreshSongsets();
      toast.success(t("songsets.toast.duplicated"));
    },
    [refreshSongsets, t]
  );

  const handleShare = useCallback((id: string) => {
    const songset = songsets.find(s => s.id === id);
    if (songset) {
      setShareTarget({ id, name: songset.name, durationSeconds: songset.durationSeconds ?? null });
      setShareDialogOpen(true);
    }
  }, [songsets]);

  const handleDownloadAudio = useCallback(async (id: string) => {
    const songset = songsets.find((s) => s.id === id);
    if (!songset?.lastCompletedRenderJobId) return;
    const toastId = toast.loading(t("songsets.toast.preparingDownload"));
    try {
      await fetchSignedUrlAndDownload(
        songset.lastCompletedRenderJobId,
        "audio",
        sanitizeFilename(songset.name),
        "mp3"
      );
      toast.success(t("songsets.toast.downloadStarted"), { id: toastId });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("songsets.error.downloadAudioFailed"), { id: toastId });
    }
  }, [songsets, t]);

  const handleDownloadVideo = useCallback(async (id: string) => {
    const songset = songsets.find((s) => s.id === id);
    if (!songset?.lastCompletedRenderJobId) return;
    const toastId = toast.loading(t("songsets.toast.preparingDownload"));
    try {
      await fetchSignedUrlAndDownload(
        songset.lastCompletedRenderJobId,
        "video",
        sanitizeFilename(songset.name),
        "mp4"
      );
      toast.success(t("songsets.toast.downloadStarted"), { id: toastId });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("songsets.error.downloadVideoFailed"), { id: toastId });
    }
  }, [songsets, t]);

  const handleDelete = useCallback(
    async (id: string) => {
      const response = await fetch(`/api/songsets/${id}`, {
        method: "DELETE",
      });

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.error || t("songsets.error.deleteFailed"));
      }

      refreshSongsets();
      toast.success(t("songsets.toast.deleted"));
    },
    [refreshSongsets, t]
  );

  return (
    <div className="px-4 py-6 pb-24 lg:pb-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold">{t("songsets.page.title")}</h1>
        <p className="text-muted-foreground mt-1">
          {t("songsets.page.subtitle")}
        </p>
      </div>

      <SongsetList
        songsets={songsets}
        isLoading={isLoading}
        error={error}
        onCreateSongset={handleCreateSongset}
        onRender={handleRender}
        onPlay={handlePlay}
        onRetry={handleRetry}
        onRename={handleRename}
        onDuplicate={handleDuplicate}
        onShare={handleShare}
        onDownloadAudio={handleDownloadAudio}
        onDownloadVideo={handleDownloadVideo}
        onDelete={handleDelete}
        currentPage={page}
        totalPages={Math.max(1, Math.ceil(total / pageSize))}
        onPageChange={handlePageChange}
        search={search}
        onSearchChange={handleSearchChange}
        onSearch={handleSearch}
        isSearching={isLoading && committedSearch.trim().length > 0}
      />

      {shareTarget && (
        <ShareDialog
          open={shareDialogOpen}
          onOpenChange={setShareDialogOpen}
          songsetId={shareTarget.id}
          songsetName={shareTarget.name}
          durationSeconds={shareTarget.durationSeconds}
        />
      )}
    </div>
  );
}
