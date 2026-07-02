import { db } from "@/db";
import { songs } from "@/db/schema";
import { sql, and, isNull, or, ilike } from "drizzle-orm";
import type { SongWithRecordings } from "./songs";

export async function fullTextSearchSongs(
  query: string,
  limit: number = 50,
  offset: number = 0,
  visibilityStatus?: string | string[]
): Promise<{ songs: SongWithRecordings[]; total: number }> {
  const tsQuery = sql`plainto_tsquery('simple', ${query})`;
  const escapedQuery = query.replace(/[%_\\]/g, "\\$&");
  const searchTerm = `%${escapedQuery}%`;

  const whereConditions = [
    or(
      sql`${songs.searchVector} @@ ${tsQuery}`,
      ilike(songs.title, searchTerm),
      ilike(songs.titlePinyin, searchTerm),
      ilike(songs.composer, searchTerm),
      ilike(songs.lyricist, searchTerm),
      ilike(songs.albumName, searchTerm)
    ),
    isNull(songs.deletedAt),
  ];

  if (visibilityStatus && visibilityStatus !== "all") {
    if (Array.isArray(visibilityStatus)) {
      if (visibilityStatus.length > 0) {
        whereConditions.push(
          sql`exists (
            select 1
            from recordings
            where recordings.song_id = ${songs.id}
              and recordings.visibility_status = ANY(${sql`ARRAY[${sql.join(visibilityStatus.map(s => sql`${s}`), sql`, `)}]::text[]`})
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
    } else {
      whereConditions.push(
        sql`exists (
          select 1
          from recordings
          where recordings.song_id = ${songs.id}
            and recordings.visibility_status = ${visibilityStatus}
            and recordings.deleted_at IS NULL
        )`
      );
    }
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

  const total = Number(countResult[0]?.count ?? 0);

  const result = await db.query.songs.findMany({
    where: whereClause,
    orderBy: [
      sql`ts_rank_cd(${songs.searchVector}, ${tsQuery}) DESC`,
    ],
    limit,
    offset,
    with: {
      recordings: {
        where: (recordings, { and, eq, isNull, inArray }) => {
          const conditions = [isNull(recordings.deletedAt)];
          if (visibilityStatus && visibilityStatus !== "all") {
            if (Array.isArray(visibilityStatus)) {
              if (visibilityStatus.length > 0) {
                conditions.push(inArray(recordings.visibilityStatus, visibilityStatus));
              }
            } else {
              conditions.push(eq(recordings.visibilityStatus, visibilityStatus));
            }
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
