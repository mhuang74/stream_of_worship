import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { auth } from "@/lib/auth";
import { getFavoriteSongIds } from "@/lib/db/favorites";
import { listSongs } from "@/lib/db/songs";
import { toSongCardData } from "@/lib/song-card-data";
import { FavoritesClient } from "./FavoritesClient";

export default async function FavoritesPage({
  searchParams,
}: {
  searchParams: Promise<{ page?: string }>;
}) {
  const session = await auth.api.getSession({ headers: await headers() });
  if (!session?.user) {
    redirect("/login");
  }

  const params = await searchParams;
  const page = Math.max(1, parseInt(params.page ?? "1") || 1);
  const pageSize = 20;
  const offset = (page - 1) * pageSize;

  const userId = Number(session.user.id);
  const favoriteSongIds = await getFavoriteSongIds(userId);
  const { songs, total } = await listSongs(pageSize, offset, {
    favoriteSongIds,
    favoritesOnly: true,
    visibilityStatus: ["published", "review"],
  });

  const initialSongs = toSongCardData(songs);

  return (
    <FavoritesClient
      initialSongs={initialSongs}
      initialTotal={total}
      currentPage={page}
      pageSize={pageSize}
    />
  );
}
