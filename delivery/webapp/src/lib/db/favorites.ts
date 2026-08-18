import { and, eq, sql, type SQL } from "drizzle-orm";
import { db } from "@/db";
import { songs, userFavorites } from "@/db/schema";

/**
 * Per-user favorite songs (table: user_favorite_songs). Favorites are durable
 * account data, persisted in the DB for cross-device (see CONTEXT.md: Favorite).
 * The Completion gate (heard ≥90%) that unlocks favoriting is client-side per
 * ADR-0002 and never reaches this module.
 */

export async function getFavoriteSongIds(userId: number): Promise<string[]> {
  const rows = await db
    .select({ songId: userFavorites.songId })
    .from(userFavorites)
    .where(eq(userFavorites.userId, userId))
    .orderBy(sql`created_at DESC`);
  return rows.map((row) => row.songId);
}

/** Idempotent: re-favoriting is a no-op. */
export async function addFavorite(userId: number, songId: string): Promise<void> {
  await db
    .insert(userFavorites)
    .values({ userId, songId })
    .onConflictDoNothing({
      target: [userFavorites.userId, userFavorites.songId],
    });
}

export async function removeFavorite(userId: number, songId: string): Promise<void> {
  await db
    .delete(userFavorites)
    .where(and(eq(userFavorites.userId, userId), eq(userFavorites.songId, songId)));
}

/**
 * Ordering expression that pins favorite songs to the top of a result list
 * (0 before 1), keeping the caller's secondary ordering beneath. When no
 * favorites are supplied, returns undefined so ordering is unchanged.
 */
export function favoritesFirstOrder(
  favoriteSongIds: string[] | undefined
): SQL | undefined {
  if (!favoriteSongIds || favoriteSongIds.length === 0) return undefined;
  return sql`CASE WHEN ${songs.id} = ANY(${favoriteSongIds}) THEN 0 ELSE 1 END`;
}

/**
 * A favorites-only predicate for the dedicated Favorites list: restricts query
 * results to the user's favorite songs. Returns undefined when favorites are
 * absent so the filter is a no-op.
 */
export function favoritesOnlyPredicate(
  favoriteSongIds: string[] | undefined
): SQL | undefined {
  if (!favoriteSongIds || favoriteSongIds.length === 0) return undefined;
  return sql`${songs.id} = ANY(${favoriteSongIds})`;
}
