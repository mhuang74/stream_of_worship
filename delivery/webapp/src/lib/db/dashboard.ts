import { and, eq, gt, isNotNull, isNull, or, sql } from "drizzle-orm";
import { db } from "@/db";
import { songs, songsets, songsetShares, userFavorites } from "@/db/schema";
import { listSongsetSummaries } from "./songsets";
import { getFavoriteSongIds } from "./favorites";
import { listSongs } from "./songs";
import { toSongCardData } from "@/lib/song-card-data";
import type { SongCardData } from "@/components/songset/SongCard";

export interface DashboardStats {
  songsetsCreated: number;
  songsetsRendered: number;
  songsetsShared: number;
  favoriteSongs: number;
  catalogSongs: number;
}

/**
 * Aggregate counts for the signed-in dashboard. "Rendered" = songsets with at
 * least one successful render (snapshot column, uses idx_songsets_user_updated).
 * "Shared" = active (non-revoked, non-expired) shares created by the user.
 */
export async function getDashboardStats(userId: number): Promise<DashboardStats> {
  const [created] = await db
    .select({ n: sql<number>`count(*)::int` })
    .from(songsets)
    .where(eq(songsets.userId, userId));

  const [rendered] = await db
    .select({ n: sql<number>`count(*)::int` })
    .from(songsets)
    .where(
      and(eq(songsets.userId, userId), isNotNull(songsets.lastCompletedRenderJobId))
    );

  const [shared] = await db
    .select({ n: sql<number>`count(*)::int` })
    .from(songsetShares)
    .where(
      and(
        eq(songsetShares.createdByUserId, userId),
        isNull(songsetShares.revokedAt),
        or(isNull(songsetShares.expiresAt), gt(songsetShares.expiresAt, sql`now()`))
      )
    );

  const [favs] = await db
    .select({ n: sql<number>`count(*)::int` })
    .from(userFavorites)
    .where(eq(userFavorites.userId, userId));

  const [catalog] = await db
    .select({ n: sql<number>`count(*)::int` })
    .from(songs)
    .where(isNull(songs.deletedAt));

  return {
    songsetsCreated: created?.n ?? 0,
    songsetsRendered: rendered?.n ?? 0,
    songsetsShared: shared?.n ?? 0,
    favoriteSongs: favs?.n ?? 0,
    catalogSongs: catalog?.n ?? 0,
  };
}

/** Most recently touched songsets (updatedAt DESC) — reuse of listSongsetSummaries. */
export async function getRecentSongsets(userId: number, limit = 3) {
  const { songsets: rows } = await listSongsetSummaries(userId, limit, 0);
  return rows;
}

/** The user's favorite songs, newest favorite first. */
export async function getRecentFavoriteSongs(userId: number, limit = 4): Promise<SongCardData[]> {
  const favoriteSongIds = await getFavoriteSongIds(userId);
  const { songs: rows } = await listSongs(limit, 0, {
    favoriteSongIds,
    favoritesOnly: true,
    visibilityStatus: ["published", "review"],
  });
  return toSongCardData(rows);
}

export interface CommunityFavoriteSong extends SongCardData {
  favoriteCount: number;
}

/**
 * Random sample of distinct songs favorited by users other than `userId`,
 * each annotated with its global favorite count. Excludes songs the current
 * user already favorited. No user names/ids/avatars are returned — only song
 * data + an aggregate count.
 *
 * Performance note: `ORDER BY random()` over grouped favorites is acceptable
 * at expected scale (small worship community). A TABLESAMPLE or pre-aggregated
 * cache would be the upgrade path if the table grows.
 */
export async function getCommunityFavoriteSample(
  userId: number,
  limit = 4
): Promise<CommunityFavoriteSong[]> {
  const sampled = await db
    .select({ songId: userFavorites.songId })
    .from(userFavorites)
    .where(
      sql`${userFavorites.songId} NOT IN (
        SELECT song_id FROM user_favorite_songs WHERE user_id = ${userId}
      )`
    )
    .groupBy(userFavorites.songId)
    .orderBy(sql`random()`)
    .limit(limit);

  if (sampled.length === 0) return [];

  const songIds = sampled.map((row) => row.songId);

  const rows = await db
    .select({
      songId: songs.id,
      favoriteCount: sql<number>`count(${userFavorites.songId})::int`,
    })
    .from(songs)
    .innerJoin(userFavorites, eq(userFavorites.songId, songs.id))
    .where(
      sql`${songs.id} IN (${sql.join(songIds.map((id) => sql`${id}`), sql`, `)})`
    )
    .groupBy(songs.id);

  const counts = new Map(rows.map((row) => [row.songId, row.favoriteCount]));

  const { songs: songRows } = await listSongs(songIds.length, 0, {
    visibilityStatus: ["published", "review"],
  });

  return toSongCardData(songRows)
    .filter((song) => counts.has(song.id))
    .map((song) => ({ ...song, favoriteCount: counts.get(song.id) ?? 0 }));
}
