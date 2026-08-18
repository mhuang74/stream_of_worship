"use client";

import { useCallback, useState } from "react";
import { toast } from "sonner";

/**
 * Shared favorite-toggle hook with optimistic updates + rollback safety.
 * Returns the current favorite-id set, a setter (for bulk-loading initial
 * favorites), and a toggle function. The toggle returns true on success and
 * false when the request failed and the optimistic update was rolled back.
 */
export function useFavoriteToggle(initial: Set<string> = new Set()) {
  const [favoriteIds, setFavoriteIds] = useState<Set<string>>(initial);

  const toggleFavorite = useCallback(
    async (songId: string): Promise<boolean> => {
      const wasFavorite = favoriteIds.has(songId);
      // Optimistic update
      setFavoriteIds((prev) => {
        const next = new Set(prev);
        if (next.has(songId)) next.delete(songId);
        else next.add(songId);
        return next;
      });

      try {
        const response = wasFavorite
          ? await fetch(`/api/favorites/${encodeURIComponent(songId)}`, {
              method: "DELETE",
            })
          : await fetch("/api/favorites", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ songId }),
            });
        if (!response.ok) throw new Error("Failed to update favorite");
        return true;
      } catch (err) {
        // Rollback
        setFavoriteIds((prev) => {
          const next = new Set(prev);
          if (wasFavorite) next.add(songId);
          else next.delete(songId);
          return next;
        });
        toast.error("Failed to update favorite");
        console.error("Error updating favorite:", err);
        return false;
      }
    },
    [favoriteIds]
  );

  return { favoriteIds, setFavoriteIds, toggleFavorite };
}
