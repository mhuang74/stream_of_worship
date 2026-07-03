import { db } from "@/db";
import { songs } from "@/db/schema";
import { sql, and, isNull, or, ilike, inArray } from "drizzle-orm";
import { mapSongWithRecordings, type SongWithRecordings } from "./songs";
import {
  buildEffectiveKeyPredicate,
  buildBpmPredicate,
  buildVisibilityCondition,
} from "./search-helpers";
import type { BpmBandKey } from "@/lib/constants";

export interface FullTextSearchOptions {
  albums?: string[];
  keys?: string[];
  bpmRange?: BpmBandKey;
}

export async function fullTextSearchSongs(
  query: string,
  limit: number = 50,
  offset: number = 0,
  visibilityStatus?: string | string[],
  options?: FullTextSearchOptions
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

  if (options?.albums?.length) {
    whereConditions.push(inArray(songs.albumName, options.albums));
  }

  if (options?.keys && (options.keys?.length ?? 0) > 0) {
    const visCond = buildVisibilityCondition(visibilityStatus, "r2");
    whereConditions.push(
      sql`exists (
        select 1 from recordings r2
        where r2.song_id = ${songs.id}
          and r2.deleted_at IS NULL
          ${visCond ? sql`and ${visCond}` : sql``}
          and ${buildEffectiveKeyPredicate(options.keys, "songs", "r2")}
      )`
    );
  }

  if (options?.bpmRange) {
    const bpmPredicate = buildBpmPredicate(options.bpmRange, "r3");
    const visCond = buildVisibilityCondition(visibilityStatus, "r3");
    whereConditions.push(
      sql`exists (
        select 1 from recordings r3
        where r3.song_id = ${songs.id}
          and r3.deleted_at IS NULL
          and r3.tempo_bpm IS NOT NULL
          ${visCond ? sql`and ${visCond}` : sql``}
          and ${bpmPredicate}
      )`
    );
  }

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
    orderBy: query.trim()
      ? [sql`ts_rank_cd(${songs.searchVector}, ${tsQuery}) DESC`]
      : [sql`lower(${songs.title}) ASC`],
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

  const songsWithRecordings: SongWithRecordings[] = result.map(mapSongWithRecordings);

  return { songs: songsWithRecordings, total };
}
