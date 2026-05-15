"use client";

import { useState, useEffect, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import { SongsetEditor } from "@/components/songset/SongsetEditor";
import { SongListItem } from "@/components/songset/SongList";
import { RenderState } from "@/components/songset/RenderStateButton";
import { TransitionSettings } from "@/components/songset/TransitionPanel";
import { toast } from "sonner";

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
  latestRenderJobId: string | null;
  lastFailedRenderJobId: string | null;
  isArtifactsStale?: boolean;
  items: ApiSongsetItem[];
}

export default function SongsetEditorPage() {
  const params = useParams();
  const router = useRouter();
  const songsetId = params.id as string;

  const [songset, setSongset] = useState<ApiSongset | null>(null);
  const [items, setItems] = useState<SongListItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Load songset data
  useEffect(() => {
    let cancelled = false;

    async function loadSongset() {
      try {
        setIsLoading(true);
        setError(null);

        const response = await fetch(`/api/songsets/${songsetId}`);

        if (!response.ok) {
          if (response.status === 401) {
            router.push("/login");
            return;
          }
          if (response.status === 404) {
            throw new Error("Songset not found");
          }
          throw new Error("Failed to load songset");
        }

        const data: ApiResponse = await response.json();

        if (cancelled) return;

        setSongset({
          id: data.id,
          name: data.name,
          description: data.description,
          createdAt: data.createdAt,
          updatedAt: data.updatedAt,
          renderState: data.renderState,
          itemCount: data.itemCount,
          latestRenderJobId: data.latestRenderJobId,
          lastFailedRenderJobId: data.lastFailedRenderJobId,
          isArtifactsStale: data.isArtifactsStale,
        });

        // Transform API items to SongListItem format
        setItems(
          data.items.map((item) => ({
            id: item.id,
            songId: item.songId,
            position: item.position,
            song: item.song,
            recording: item.recording,
            gapBeats: item.gapBeats,
            crossfadeEnabled: item.crossfadeEnabled,
            crossfadeDurationSeconds: item.crossfadeDurationSeconds,
            keyShiftSemitones: item.keyShiftSemitones,
            tempoRatio: item.tempoRatio,
            markedLineCount: item.markedLineCount,
          }))
        );
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load songset");
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    }

    if (songsetId) {
      loadSongset();
    }

    return () => {
      cancelled = true;
    };
  }, [songsetId, router]);

  // Handle item reorder
  const handleUpdateItems = useCallback(
    async (newItems: SongListItem[]) => {
      // Update positions for all items
      const updates = newItems.map((item, index) => ({
        itemId: item.id,
        position: index,
      }));

      const response = await fetch(`/api/songsets/${songsetId}/items/reorder`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ updates }),
      });

      if (!response.ok) {
        throw new Error("Failed to reorder items");
      }

      // Update local state
      setItems(newItems);
    },
    [songsetId]
  );

  // Handle item removal
  const handleRemoveItem = useCallback(
    async (itemId: string) => {
      const response = await fetch(
        `/api/songsets/${songsetId}/items?itemId=${itemId}`,
        {
          method: "DELETE",
        }
      );

      if (!response.ok) {
        throw new Error("Failed to remove item");
      }

      // Update local state
      setItems((prev) => prev.filter((item) => item.id !== itemId));
    },
    [songsetId]
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
    },
    [songsetId]
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
    // First get the songset details
    const response = await fetch(`/api/songsets/${songsetId}`);
    if (!response.ok) {
      throw new Error("Failed to fetch songset details");
    }

    const songset = await response.json();

    // Create a new songset with "Copy of" prefix
    const createResponse = await fetch("/api/songsets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: `Copy of ${songset.name}`,
        description: songset.description,
      }),
    });

    if (!createResponse.ok) {
      throw new Error("Failed to duplicate songset");
    }

    const newSongset = await createResponse.json();

    // Copy items if any
    if (songset.items && songset.items.length > 0) {
      for (const item of songset.items) {
        await fetch(`/api/songsets/${newSongset.id}/items`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            songId: item.songId,
            recordingHashPrefix: item.recordingHashPrefix,
            position: item.position,
            gapBeats: item.gapBeats,
            crossfadeEnabled: item.crossfadeEnabled,
            crossfadeDurationSeconds: item.crossfadeDurationSeconds,
            keyShiftSemitones: item.keyShiftSemitones,
            tempoRatio: item.tempoRatio,
          }),
        });
      }
    }

    // Navigate to the new songset
    router.push(`/songsets/${newSongset.id}`);
  }, [songsetId, router]);

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
    router.push(`/songsets/${songsetId}?share=true`);
  }, [songsetId, router]);

  // Handle add songs
  const handleAddSongs = useCallback(() => {
    // This will open the browse sheet (to be implemented in Task 3.3)
    toast.info("Browse sheet coming in Task 3.3");
  }, []);

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
      onAddSongs={handleAddSongs}
    />
  );
}
