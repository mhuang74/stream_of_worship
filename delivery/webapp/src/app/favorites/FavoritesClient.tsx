"use client";

import { useState } from "react";
import Link from "next/link";
import { SongCard, SongCardData } from "@/components/songset/SongCard";
import { Heart } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

export function FavoritesClient({ initialSongs }: { initialSongs: SongCardData[] }) {
  const [songs, setSongs] = useState<SongCardData[]>(initialSongs);

  const handleToggleFavorite = async (songId: string) => {
    try {
      const response = await fetch(
        `/api/favorites/${encodeURIComponent(songId)}`,
        { method: "DELETE" }
      );
      if (!response.ok) throw new Error("Failed to remove favorite");
      setSongs((prev) => prev.filter((song) => song.id !== songId));
    } catch (err) {
      toast.error("Failed to remove favorite");
      console.error("Error removing favorite:", err);
    }
  };

  if (songs.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-center">
        <Heart className="size-8 text-muted-foreground mb-2" />
        <p className="font-medium">No favorites yet</p>
        <p className="text-sm text-muted-foreground mt-1 max-w-md">
          Listen to at least 90% of a song in the songset builder, then tap the
          heart to favorite it. Your favorites are pinned to the top.
        </p>
        <Link href="/songsets" className={cn(buttonVariants(), "mt-6")}>
          Go to Songsets
        </Link>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl px-4 py-6">
      <h1 className="text-2xl font-bold">Favorites</h1>
      <p className="text-sm text-muted-foreground mb-4">
        {songs.length} favorite {songs.length === 1 ? "song" : "songs"}
      </p>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2" data-testid="favorites-list">
        {songs.map((song) => (
          <SongCard
            key={song.id}
            song={song}
            isFavorite
            onToggleFavorite={handleToggleFavorite}
          />
        ))}
      </div>
    </div>
  );
}
