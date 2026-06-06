"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import dynamic from "next/dynamic";
import { SongsetEditor } from "@/components/songset/SongsetEditor";
import { SongCardData } from "@/components/songset/SongCard";
import { SongListItem } from "@/components/songset/SongList";
import { RenderState } from "@/components/songset/RenderStatusBadge";
import { TransitionSettings } from "@/components/songset/TransitionPanel";
import { toast } from "sonner";
import { sanitizeFilename, fetchSignedUrlAndDownload } from "@/lib/download";

const BrowseSheet = dynamic(
  () => import("@/components/songset/BrowseSheet").then((m) => ({ default: m.BrowseSheet })),
  { ssr: false }
);

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
  isArtifactsStale?: boolean;
}

interface ApiSongsetItem {
  id: string;
  songId: string;
  recordingHashPrefix: string | null;
  position: number;
  gapBeats: number;
  crossfadeEnabled: number;
  crossfadeDurationSeconds: number | null;
  keyShiftSemitones: number;
  tempoRatio: number;
  song: {
    id: string;
    title: string;
    composer: string | null;
    lyricist: string | null;
    albumName: string | null;
    musicalKey: string | null;
  } | null;
  recording: {
    contentHash: string;
    durationSeconds: number | null;
    tempoBpm: number | null;
    musicalKey: string | null;
  } | null;
  markedLineCount?: number;
}

interface ApiResponse {
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
  isArtifactsStale?: boolean;
  items: ApiSongsetItem[];
}

interface SongsetEditorClientProps {
  songsetId: string;
  initialData: ApiResponse;
}

function transformItems(items: ApiSongsetItem[]): SongListItem[] {
  return items.map((item) => ({
    id: item.id,
    songId: item.songId,
    position: item.position,
    song: item.song,
    recording: item.recording
      ? {
          ...item.recording,
          hashPrefix: item.recordingHashPrefix ?? "",
        }
      : null,
    gapBeats: item.gapBeats,
    crossfadeEnabled: item.crossfadeEnabled,
    crossfadeDurationSeconds: item.crossfadeDurationSeconds,
    keyShiftSemitones: item.keyShiftSemitones,
    tempoRatio: item.tempoRatio,
    markedLineCount: item.markedLineCount,
  }));
}

export function SongsetEditorClient({ songsetId, initialData }: SongsetEditorClientProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const isNew = searchParams.get("new") === "true";

  const [songset, setSongset] = useState<ApiSongset | null>(() => ({
    id: initialData.id,
    name: initialData.name,
    description: initialData.description,
    createdAt: initialData.createdAt,
    updatedAt: initialData.updatedAt,
    renderState: initialData.renderState,
    itemCount: initialData.itemCount,
    durationSeconds: initialData.durationSeconds ?? null,
    latestRenderJobId: initialData.latestRenderJobId,
    lastFailedRenderJobId: initialData.lastFailedRenderJobId,
    lastCompletedRenderJobId: initialData.lastCompletedRenderJobId,
    isArtifactsStale: initialData.isArtifactsStale,
  }));
  const [items, setItems] = useState<SongListItem[]>(() => transformItems(initialData.items));
  const [isBrowseSheetOpen, setIsBrowseSheetOpen] = useState(false);
  const isLoading = false;
  const error: string | null = null;
  const [isRemoving, setIsRemoving] = useState(false);
  const [shareDialogOpen, setShareDialogOpen] = useState(searchParams.get("share") === "true");
  const autoOpenDoneRef = useRef(false);
  const isShare = searchParams.get("share") === "true";

  useEffect(() => {
    if (isNew && !autoOpenDoneRef.current && items.length === 0) {
      autoOpenDoneRef.current = true;
      setIsBrowseSheetOpen(true);
      router.replace(`/songsets/${songsetId}`);
    }
  }, [songsetId, router, isNew, items.length]);

  const markStale = useCallback(() => {
    setSongset((prev) =>
      prev
        ? { ...prev, renderState: "stale" as RenderState, isArtifactsStale: true }
        : prev
    );
  }, []);

  // Handle item reorder (optimistic)
  const handleUpdateItems = useCallback(
    (newItems: SongListItem[]) => {
      const previousItems = items;
      setItems(newItems);

      const updates = newItems.map((item, index) => ({
        itemId: item.id,
        position: index,
      }));

      fetch(`/api/songsets/${songsetId}/items/reorder`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ updates }),
      }).then((response) => {
        if (!response.ok) {
          setItems(previousItems);
          throw new Error("Failed to reorder items");
        }
        markStale();
      }).catch(() => {
        setItems(previousItems);
      });
    },
    [songsetId, items, markStale]
  );

  // Handle item removal
  const handleRemoveItem = useCallback(
    async (itemId: string) => {
      if (isRemoving) return;
      setIsRemoving(true);

      const removedItem = items.find((item) => item.id === itemId);
      const removedIndex = items.findIndex((item) => item.id === itemId);
      const prevRenderState = songset?.renderState;
      const prevIsArtifactsStale = songset?.isArtifactsStale;

      setItems((prev) => prev.filter((item) => item.id !== itemId));
      setSongset((prev) =>
        prev
          ? {
              ...prev,
              itemCount: Math.max(0, prev.itemCount - 1),
              renderState: "stale" as RenderState,
              isArtifactsStale: true,
            }
          : prev
      );

      try {
        const response = await fetch(
          `/api/songsets/${songsetId}/items?itemId=${itemId}`,
          {
            method: "DELETE",
          }
        );

        if (!response.ok) {
          throw new Error("Failed to remove item");
        }
      } catch {
        if (removedItem && removedIndex >= 0) {
          setItems((prev) => {
            const next = [...prev];
            next.splice(removedIndex, 0, removedItem);
            return next;
          });
          setSongset((prev) =>
            prev
              ? {
                  ...prev,
                  itemCount: prev.itemCount + 1,
                  renderState: prevRenderState ?? prev.renderState,
                  isArtifactsStale: prevIsArtifactsStale ?? prev.isArtifactsStale,
                }
              : prev
          );
        }
        throw new Error("Failed to remove item");
      } finally {
        setIsRemoving(false);
      }
    },
    [songsetId, items, isRemoving, songset?.isArtifactsStale, songset?.renderState]
  );

  // Handle transition update
  const handleUpdateTransition = useCallback(
    async (itemId: string, settings: TransitionSettings) => {
      const response = await fetch(`/api/songsets/${songsetId}/items`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          itemId,
          gapBeats: settings.gapBeats,
          crossfadeEnabled: settings.crossfadeEnabled ? 1 : 0,
          crossfadeDurationSeconds: settings.crossfadeDurationSeconds,
          keyShiftSemitones: settings.keyShiftSemitones,
          tempoRatio: settings.tempoRatio,
        }),
      });

      if (!response.ok) {
        throw new Error("Failed to update transition");
      }

      // Update local state
      setItems((prev) =>
        prev.map((item) =>
          item.id === itemId
            ? {
                ...item,
                gapBeats: settings.gapBeats,
                crossfadeEnabled: settings.crossfadeEnabled ? 1 : 0,
                crossfadeDurationSeconds: settings.crossfadeDurationSeconds,
                keyShiftSemitones: settings.keyShiftSemitones,
                tempoRatio: settings.tempoRatio,
              }
            : item
        )
      );
      markStale();
    },
    [songsetId, markStale]
  );

  // Handle render
  const handleRender = useCallback(() => {
    router.push(`/songsets/${songsetId}/render`);
  }, [songsetId, router]);

  // Handle play
  const handlePlay = useCallback(() => {
    router.push(`/songsets/${songsetId}/play`);
  }, [songsetId, router]);

  // Handle retry
  const handleRetry = useCallback(() => {
    router.push(`/songsets/${songsetId}/render`);
  }, [songsetId, router]);

  // Handle description update
  const handleUpdateDescription = useCallback(
    async (description: string) => {
      const response = await fetch(`/api/songsets/${songsetId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ description }),
      });

      if (!response.ok) {
        throw new Error("Failed to update description");
      }

      // Update local state
      setSongset((prev) =>
        prev ? { ...prev, description } : null
      );
    },
    [songsetId]
  );

  // Handle duplicate
  const handleDuplicate = useCallback(async () => {
    const response = await fetch(`/api/songsets/${songsetId}/duplicate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: `Copy of ${songset?.name ?? ""}`,
        description: songset?.description,
      }),
    });

    if (!response.ok) {
      const data = await response.json();
      throw new Error(data.error || "Failed to duplicate songset");
    }

    const newSongset = await response.json();

    // Navigate to the new songset
    router.push(`/songsets/${newSongset.id}`);
  }, [songsetId, router, songset]);

  // Handle delete
  const handleDelete = useCallback(async () => {
    const response = await fetch(`/api/songsets/${songsetId}`, {
      method: "DELETE",
    });

    if (!response.ok) {
      throw new Error("Failed to delete songset");
    }
  }, [songsetId]);

  // Handle share
  const handleShare = useCallback(() => {
    setShareDialogOpen(true);
  }, []);

  // Backward compat: ?share=true opens dialog then cleans URL
  useEffect(() => {
    if (isShare) {
      router.replace(`/songsets/${songsetId}`);
    }
  }, [isShare, songsetId, router]);

  // Handle download audio
  const handleDownloadAudio = useCallback(async () => {
    if (!songset?.lastCompletedRenderJobId) return;
    const toastId = toast.loading("Preparing download...");
    try {
      await fetchSignedUrlAndDownload(
        songset.lastCompletedRenderJobId,
        "audio",
        sanitizeFilename(songset.name),
        "mp3"
      );
      toast.success("Download started", { id: toastId });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to download audio", { id: toastId });
    }
  }, [songset]);

  // Handle download video
  const handleDownloadVideo = useCallback(async () => {
    if (!songset?.lastCompletedRenderJobId) return;
    const toastId = toast.loading("Preparing download...");
    try {
      await fetchSignedUrlAndDownload(
        songset.lastCompletedRenderJobId,
        "video",
        sanitizeFilename(songset.name),
        "mp4"
      );
      toast.success("Download started", { id: toastId });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to download video", { id: toastId });
    }
  }, [songset]);

  // Handle add songs
  const handleAddSongs = useCallback(() => {
    setIsBrowseSheetOpen(true);
  }, []);

  const handleAddSong = useCallback(
    async (song: SongCardData) => {
      const nextPosition = items.length;
      const primaryRecording = song.recordings[0];

      const response = await fetch(`/api/songsets/${songsetId}/items`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          songId: song.id,
          recordingHashPrefix: primaryRecording?.hashPrefix ?? null,
          position: nextPosition,
        }),
      });

      if (!response.ok) {
        throw new Error("Failed to add song to songset");
      }

      const item: ApiSongsetItem = await response.json();

      setItems((prev) => [
        ...prev,
        {
          id: item.id,
          songId: item.songId,
          position: item.position,
          song: item.song,
          recording: item.recording
            ? {
                ...item.recording,
                hashPrefix: item.recordingHashPrefix ?? "",
              }
            : null,
          gapBeats: item.gapBeats,
          crossfadeEnabled: item.crossfadeEnabled,
          crossfadeDurationSeconds: item.crossfadeDurationSeconds,
          keyShiftSemitones: item.keyShiftSemitones,
          tempoRatio: item.tempoRatio,
          markedLineCount: item.markedLineCount,
        },
      ]);

      setSongset((prev) =>
        prev
          ? {
              ...prev,
              itemCount: prev.itemCount + 1,
              renderState: "stale" as RenderState,
              isArtifactsStale: true,
            }
          : prev
      );
    },
    [items.length, songsetId]
  );

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
      </div>
    );
  }

  if (error || !songset) {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen p-4">
        <p className="text-destructive text-center">{error || "Songset not found"}</p>
        <button
          onClick={() => router.push("/songsets")}
          className="mt-4 text-primary hover:underline"
        >
          Back to songsets
        </button>
      </div>
    );
  }

  return (
    <>
      <SongsetEditor
        songset={songset}
        items={items}
        onUpdateItems={handleUpdateItems}
        onRemoveItem={handleRemoveItem}
        onUpdateTransition={handleUpdateTransition}
        onRender={handleRender}
        onPlay={handlePlay}
        onRetry={handleRetry}
        onUpdateDescription={handleUpdateDescription}
        onDuplicate={handleDuplicate}
        onDelete={handleDelete}
        onShare={handleShare}
        onDownloadAudio={handleDownloadAudio}
        onDownloadVideo={handleDownloadVideo}
        onAddSongs={handleAddSongs}
        isRemoving={isRemoving}
      />
      <BrowseSheet
        isOpen={isBrowseSheetOpen}
        onOpenChange={setIsBrowseSheetOpen}
        onAddSong={handleAddSong}
        existingSongIds={items.map((item) => item.songId)}
        itemCount={items.length}
      />
      <ShareDialog
        open={shareDialogOpen}
        onOpenChange={setShareDialogOpen}
        songsetId={songsetId}
        songsetName={songset.name}
        durationSeconds={songset.durationSeconds ?? null}
        renderJobId={songset.lastCompletedRenderJobId ?? undefined}
      />
    </>
  );
}
