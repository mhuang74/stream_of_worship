import { db } from "@/db";
import { sql, and, eq, isNull } from "drizzle-orm";
import { recordings, songs } from "@/db/schema";
import { createR2ClientFromEnv } from "@/lib/r2/client";
import { parseLRC } from "@/lib/render/lrc-parser";

export interface ReviewCandidate {
  contentHash: string;
  title: string;
  album: string | null;
  reviewed: boolean;
}

export async function reviewQueue(userId: number, query: string, showReviewed: boolean, offset: number) {
  // Same priority cascade and per-song recording selection as the CLI. Filter
  // personal feedback after choosing the target, so rating it cannot switch the
  // song silently to a different recording.
  const result = await db.execute(sql`
    WITH pool AS (
      SELECT r.content_hash, r.song_id, s.title, s.album_name,
        s.album_series, fb.latest_sad_at,
        ROW_NUMBER() OVER (PARTITION BY r.song_id ORDER BY
          fb.latest_sad_at DESC NULLS LAST, r.created_at DESC NULLS LAST,
          r.content_hash ASC) AS rec_rank
      FROM recordings r JOIN songs s ON s.id = r.song_id
      LEFT JOIN LATERAL (
        SELECT MAX(updated_at) AS latest_sad_at FROM lyrics_feedback
        WHERE recording_content_hash = r.content_hash
          AND rating = 'sad' AND resolved_at IS NULL
      ) fb ON TRUE
      WHERE r.visibility_status = 'review' AND r.deleted_at IS NULL
        AND s.deleted_at IS NULL AND r.r2_audio_url IS NOT NULL
    )
    SELECT p.content_hash AS "contentHash", p.title, p.album_name AS album,
      (mine.id IS NOT NULL) AS reviewed
    FROM pool p
    LEFT JOIN lyrics_feedback mine ON mine.recording_content_hash = p.content_hash
      AND mine.user_id = ${userId} AND mine.resolved_at IS NULL
    LEFT JOIN (SELECT song_id, COUNT(*) n FROM songset_items GROUP BY song_id) si ON si.song_id = p.song_id
    LEFT JOIN (SELECT song_id, COUNT(*) n FROM user_favorite_songs GROUP BY song_id) fav ON fav.song_id = p.song_id
    LEFT JOIN (SELECT album_name, album_series, MAX(created_at) newest FROM songs
      GROUP BY album_name, album_series) al
      ON al.album_name IS NOT DISTINCT FROM p.album_name
      AND al.album_series IS NOT DISTINCT FROM p.album_series
    WHERE p.rec_rank = 1
      AND (${query} = '' OR strpos(lower(p.title), lower(${query})) > 0
        OR strpos(lower(coalesce(p.album_name, '')), lower(${query})) > 0)
      AND (${query !== '' || showReviewed} OR mine.id IS NULL)
    ORDER BY p.latest_sad_at DESC NULLS LAST, coalesce(si.n, 0) DESC,
      coalesce(fav.n, 0) DESC, al.newest DESC NULLS LAST, p.song_id ASC
    LIMIT 51 OFFSET ${offset}
  `);
  const rows = result.rows as unknown as ReviewCandidate[];
  return { recordings: rows.slice(0, 50), hasMore: rows.length > 50 };
}

export async function reviewRecording(contentHash: string) {
  const [row] = await db.select({
    contentHash: recordings.contentHash, hashPrefix: recordings.hashPrefix,
    title: songs.title, album: songs.albumName,
  }).from(recordings).innerJoin(songs, eq(songs.id, recordings.songId))
    .where(and(eq(recordings.contentHash, contentHash),
      eq(recordings.visibilityStatus, "review"), isNull(recordings.deletedAt),
      isNull(songs.deletedAt))).limit(1);
  return row;
}

export async function canonicalReviewLyrics(hashPrefix: string) {
  const r2 = createR2ClientFromEnv();
  const signed = await r2.getLrcSignedUrl(hashPrefix);
  const response = await fetch(signed.url, { cache: "no-store" });
  if (response.status === 404) return { lines: [], synced: false };
  if (!response.ok) throw new Error("Canonical Lyrics could not be loaded");
  const lines = parseLRC(await response.text());
  return { lines, synced: lines.some(line => line.text.length > 0) };
}
