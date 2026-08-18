import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { auth } from "@/lib/auth";
import { getFavoriteSongIds } from "@/lib/db/favorites";
import { listSongs } from "@/lib/db/songs";
import type { SongCardData } from "@/components/songset/SongCard";
import { FavoritesClient } from "./FavoritesClient";

export default async function FavoritesPage() {
  const session = await auth.api.getSession({ headers: await headers() });
  if (!session?.user) {
    redirect("/login");
  }

  const userId = Number(session.user.id);
  const favoriteSongIds = await getFavoriteSongIds(userId);
  const { songs } = await listSongs(100, 0, {
    favoriteSongIds,
    favoritesOnly: true,
    visibilityStatus: ["published", "review"],
  });

  // Serialize to the SongCard data shape; avoid crossing Date objects.
  const initialSongs: SongCardData[] = songs.map((song) => ({
    id: song.id,
    title: song.title,
    composer: song.composer,
    lyricist: song.lyricist,
    albumName: song.albumName,
    musicalKey: song.musicalKey,
    effectiveKey: song.effectiveKey,
    effectiveKeyStartRoot: song.effectiveKeyStartRoot,
    effectiveKeyEndRoot: song.effectiveKeyEndRoot,
    recordings: song.recordings.map((r) => ({
      contentHash: r.contentHash,
      hashPrefix: r.hashPrefix,
      durationSeconds: r.durationSeconds,
      tempoBpm: r.tempoBpm,
      musicalKey: r.musicalKey,
      effectiveKey: r.effectiveKey,
      effectiveKeyStartRoot: r.effectiveKeyStartRoot,
      effectiveKeyEndRoot: r.effectiveKeyEndRoot,
      visibilityStatus: r.visibilityStatus,
    })),
  }));

  return <FavoritesClient initialSongs={initialSongs} />;
}
