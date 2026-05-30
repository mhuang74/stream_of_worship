"use client";

import { useState, useEffect, useCallback } from "react";
import { SongsetList, Songset } from "@/components/songset/SongsetList";
import { RenderState } from "@/components/songset/RenderStatusBadge";
import { toast } from "sonner";
import { sanitizeFilename, fetchSignedUrlAndDownload } from "@/lib/download";

interface ApiSongset {
  id: string;
  name: string;
  description: string | null;
  createdAt: string;
  updatedAt: string;
  renderState: RenderState;
  itemCount: number;
  latestRenderJobId: string | null;
  lastFailedRenderJobId: string | null;
}

interface ApiResponse {
  songsets: ApiSongset[];
  total: number;
}

export default function SongsetsPage() {
  const [songsets, setSongsets] = useState<Songset[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let cancelled = false;

    async function loadSongsets() {
      try {
        setIsLoading(true);
        setError(null);

        const response = await fetch("/api/songsets");

        if (!response.ok) {
          if (response.status === 401) {
            throw new Error("Please sign in to view your songsets");
          }
          throw new Error("Failed to load songsets");
        }

        const data: ApiResponse = await response.json();

        if (cancelled) return;

        // Transform API response to component format
        const transformedSongsets: Songset[] = data.songsets.map((songset) => ({
          id: songset.id,
          name: songset.name,
          description: songset.description,
          itemCount: songset.itemCount,
          updatedAt: new Date(songset.updatedAt),
          renderState: songset.renderState,
          latestRenderJobId: songset.latestRenderJobId,
          isOfflineAvailable: false,
          isArtifactsStale: songset.renderState === "stale",
        }));

        setSongsets(transformedSongsets);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load songsets");
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
  }, [refreshKey]);

  const refreshSongsets = useCallback(() => {
    setRefreshKey((k) => k + 1);
  }, []);

  const handleCreateSongset = useCallback(
    async (name: string, description?: string) => {
      const response = await fetch("/api/songsets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, description }),
      });

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.error || "Failed to create songset");
      }

      refreshSongsets();
      toast.success("Songset created successfully");
    },
    [refreshSongsets]
  );

  const handleRender = useCallback((id: string) => {
    // Navigate to render page
    window.location.href = `/songsets/${id}/render`;
  }, []);

  const handlePlay = useCallback((id: string) => {
    window.location.href = `/songsets/${id}/play`;
  }, []);

  const handleRename = useCallback(
    async (id: string, name: string) => {
      const response = await fetch(`/api/songsets/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.error || "Failed to rename songset");
      }

      refreshSongsets();
      toast.success("Songset renamed successfully");
    },
    [refreshSongsets]
  );

  const handleDuplicate = useCallback(
    async (id: string) => {
      const response = await fetch(`/api/songsets/${id}/duplicate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: "Copy of Songset",
          description: null,
        }),
      });

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.error || "Failed to duplicate songset");
      }

      refreshSongsets();
      toast.success("Songset duplicated successfully");
    },
    [refreshSongsets]
  );

  const handleShare = useCallback((id: string) => {
    window.location.href = `/songsets/${id}?share=true`;
  }, []);

  const handleDownloadAudio = useCallback(async (id: string) => {
    const songset = songsets.find((s) => s.id === id);
    if (!songset?.latestRenderJobId) return;
    const toastId = toast.loading("Preparing download...");
    try {
      await fetchSignedUrlAndDownload(
        songset.latestRenderJobId,
        "audio",
        sanitizeFilename(songset.name),
        "mp3"
      );
      toast.success("Download started", { id: toastId });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to download audio", { id: toastId });
    }
  }, [songsets]);

  const handleDownloadVideo = useCallback(async (id: string) => {
    const songset = songsets.find((s) => s.id === id);
    if (!songset?.latestRenderJobId) return;
    const toastId = toast.loading("Preparing download...");
    try {
      await fetchSignedUrlAndDownload(
        songset.latestRenderJobId,
        "video",
        sanitizeFilename(songset.name),
        "mp4"
      );
      toast.success("Download started", { id: toastId });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to download video", { id: toastId });
    }
  }, [songsets]);

  const handleDelete = useCallback(
    async (id: string) => {
      const response = await fetch(`/api/songsets/${id}`, {
        method: "DELETE",
      });

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.error || "Failed to delete songset");
      }

      refreshSongsets();
      toast.success("Songset deleted successfully");
    },
    [refreshSongsets]
  );

  return (
    <div className="px-4 py-6 pb-24 lg:pb-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold">Songsets</h1>
        <p className="text-muted-foreground mt-1">
          Manage your worship song collections
        </p>
      </div>

      <SongsetList
        songsets={songsets}
        isLoading={isLoading}
        error={error}
        onCreateSongset={handleCreateSongset}
        onRender={handleRender}
        onPlay={handlePlay}
        onRename={handleRename}
        onDuplicate={handleDuplicate}
        onShare={handleShare}
        onDownloadAudio={handleDownloadAudio}
        onDownloadVideo={handleDownloadVideo}
        onDelete={handleDelete}
      />
    </div>
  );
}
