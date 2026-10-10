import { db } from "@/db";
import { recordings, songs } from "@/db/schema";
import { and, inArray, isNull, sql } from "drizzle-orm";
import { mapSongWithRecordings } from "./songs";
import { toSongCardData } from "@/lib/song-card-data";
import type { SongCardData } from "@/components/songset/SongCard";

/** SongCardData plus discovery-only aggregate counts. */
export interface DiscoverySong extends SongCardData {
  favoriteCount: number;
  inclusionCount: number;
}

export interface DiscoveryPage {
  songs: DiscoverySong[];
  hasMore: boolean;
}

interface DiscoveryAggRow extends Record<string, unknown> {
  song_id: string;
  last_event_at: Date | null;
  favorite_count: number;
  inclusion_count: number;
  total_rows: number | string;
}

/**
 * Aggregate "events" feed for the discovery rail (issue #253): a user
 * favoriting a song or a song landing in another user's songset makes the song
 * discoverable to others. Anonymous by design: only song ids leave this
 * module — no user ids, usernames, or songset names.
 *
 * Recency = the event's timestamp: user_favorite_songs.created_at for
 * favorites; songsets.updated_at for inclusions (songset_items' own timestamps
 * don't reflect the containing songset's last touch, and a real per-item
 * cross-user recency column is a schema migration out of scope). Ordering:
 * most-recent-event DESC, tiebroken by higher total event count DESC.
 *
 * Only songs the viewer does NOT have (not favorited, not in the viewer's
 * songsets), not soft-deleted, and with ≥1 published/review recording
 * (recordings.deleted_at IS NULL). hasMore is derived from the windowed
 * total row count over the filtered aggregate.
 */
export async function listDiscoverySongs(
  userId: number,
  limit: number,
  offset: number
): Promise<DiscoveryPage> {
  const result = await db.execute<DiscoveryAggRow>(sql`
    with fav_events as (
      select f.song_id as song_id,
             max(f.created_at) as last_event_at,
             count(distinct f.user_id)::int as favorite_count
      from user_favorite_songs f
      where f.user_id <> ${userId}
      group by f.song_id
    ),
    inc_events as (
      select si.song_id as song_id,
             max(s.updated_at) as last_event_at,
             count(distinct s.id)::int as inclusion_count
      from songset_items si
      join songsets s on s.id = si.songset_id
      where s.user_id <> ${userId}
      group by si.song_id
    ),
    ranked as (
      select
        coalesce(f.song_id, i.song_id) as song_id,
        greatest(f.last_event_at, i.last_event_at) as last_event_at,
        coalesce(f.favorite_count, 0) as favorite_count,
        coalesce(i.inclusion_count, 0) as inclusion_count
      from fav_events f
      full join inc_events i on f.song_id = i.song_id
      where
        coalesce(f.song_id, i.song_id) not in (
          select uf.song_id from user_favorite_songs uf where uf.user_id = ${userId}
        )
        and coalesce(f.song_id, i.song_id) not in (
          select vi.song_id
          from songset_items vi
          join songsets vs on vs.id = vi.songset_id
          where vs.user_id = ${userId}
        )
        and exists (
          select 1 from recordings r
          where r.song_id = coalesce(f.song_id, i.song_id)
            and r.deleted_at is null
            and r.visibility_status in ('published', 'review')
        )
        and exists (
          select 1 from songs so
          where so.id = coalesce(f.song_id, i.song_id)
            and so.deleted_at is null
        )
    )
    select
      song_id,
      last_event_at,
      favorite_count,
      inclusion_count,
      count(*) over () as total_rows
    from ranked
    order by
      last_event_at desc nulls last,
      (favorite_count + inclusion_count) desc
    limit ${limit}
    offset ${offset}
  `);

  const rows = result.rows;
  const totalRows = rows.length > 0 ? Number(rows[0].total_rows) : 0;
  const hasMore = offset + rows.length < totalRows;

  if (rows.length === 0) {
    return { songs: [], hasMore: false };
  }

  const songIds = rows.map((row) => row.song_id);
  const songRows = await db.query.songs.findMany({
    where: and(inArray(songs.id, songIds), isNull(songs.deletedAt)),
    with: {
      recordings: {
        where: and(
          isNull(recordings.deletedAt),
          inArray(recordings.visibilityStatus, ["published", "review"])
        ),
      },
    },
  });

  const bySongId = new Map(songRows.map((row) => [row.id, row]));
  const hydrated: DiscoverySong[] = [];
  for (const row of rows) {
    const songRow = bySongId.get(row.song_id);
    if (!songRow) continue; // deleted between aggregate and hydrate; drops off
    hydrated.push({
      ...toSongCardData([mapSongWithRecordings(songRow)])[0],
      favoriteCount: row.favorite_count,
      inclusionCount: row.inclusion_count,
    });
  }

  return { songs: hydrated, hasMore };
}
