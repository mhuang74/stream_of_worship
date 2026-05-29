import { db } from "@/db";
import { songs } from "@/db/schema";
import { sql, and, isNull } from "drizzle-orm";
import type { SongWithRecordings } from "./songs";

export async function fullTextSearchSongs(
  query: string,
  limit: number = 50,
  offset: number = 0,
  visibilityStatus?: string
): Promise<{ songs: SongWithRecordings[]; total: number }> {
  const tsQuery = sql`plainto_tsquery('simple', ${query})`;

  const whereConditions = [
    sql`${songs.searchVector} @@ ${tsQuery}`,
    isNull(songs.deletedAt),
  ];

  if (visibilityStatus && visibilityStatus !== "all") {
    whereConditions.push(
      sql`exists (
        select 1
        from recordings
        where recordings.song_id = ${songs.id}
          and recordings.visibility_status = ${visibilityStatus}
          and recordings.deleted_at IS NULL
      )`
    );
  } else {
    whereConditions.push(
      sql`exists (
        select 1
        from recordings
        where recordings.song_id = ${songs.id}
          and recordings.deleted_at IS NULL
      )`
    );
  }

  const whereClause = and(...whereConditions);

  const countResult = await db
    .select({ count: sql<number>`count(*)` })
    .from(songs)
    .where(whereClause);

  const total = countResult[0]?.count ?? 0;

  const result = await db.query.songs.findMany({
    where: whereClause,
    orderBy: [sql`ts_rank_cd(${songs.searchVector}, ${tsQuery}) DESC`],
    limit,
    offset,
    with: {
      recordings: {
        where: (recordings, { and, eq, isNull }) => {
          const conditions = [isNull(recordings.deletedAt)];
          if (visibilityStatus && visibilityStatus !== "all") {
            conditions.push(eq(recordings.visibilityStatus, visibilityStatus));
          }
          return and(...conditions);
        },
      },
    },
  });

  const songsWithRecordings: SongWithRecordings[] = result.map((song) => ({
    id: song.id,
    title: song.title,
    titlePinyin: song.titlePinyin,
    composer: song.composer,
    lyricist: song.lyricist,
    albumName: song.albumName,
    albumSeries: song.albumSeries,
    musicalKey: song.musicalKey,
    createdAt: song.createdAt,
    updatedAt: song.updatedAt,
    recordings: song.recordings.map((r) => ({
      contentHash: r.contentHash,
      hashPrefix: r.hashPrefix,
      originalFilename: r.originalFilename,
      durationSeconds: r.durationSeconds,
      tempoBpm: r.tempoBpm,
      musicalKey: r.musicalKey,
      musicalMode: r.musicalMode,
      loudnessDb: r.loudnessDb,
      r2AudioUrl: r.r2AudioUrl,
      r2LrcUrl: r.r2LrcUrl,
      visibilityStatus: r.visibilityStatus,
      analysisStatus: r.analysisStatus,
    })),
  }));

  return { songs: songsWithRecordings, total };
}
