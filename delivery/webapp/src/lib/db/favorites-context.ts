import { getFavoriteSongIds } from "@/lib/db/favorites";

export interface FavoriteContext {
  favoriteSongIds: string[];
  favoritesOnly: boolean;
}

/**
 * Loads the session user's favorite song ids and parses the favoritesOnly
 * flag from search params. Shared by /api/songs and /api/songs/search so the
 * flag threads through both routes consistently.
 */
export async function loadFavoriteContext(
  userId: number,
  searchParams: URLSearchParams
): Promise<FavoriteContext> {
  const favoriteSongIds = await getFavoriteSongIds(userId);
  const favoritesOnlyParam = searchParams.get("favoritesOnly");
  const favoritesOnly =
    favoritesOnlyParam === "1" || favoritesOnlyParam === "true";
  return { favoriteSongIds, favoritesOnly };
}
