import { db } from "@/db";
import { sql, and, eq, isNull } from "drizzle-orm";
import { recordings, songs } from "@/db/schema";
import { createR2ClientFromEnv } from "@/lib/r2/client";
import { parseLRC } from "@/lib/render/lrc-parser";
import type { FeedbackRating, FeedbackReason } from "@/lib/lyrics/situation";

export interface ReviewFeedback {
  id: string;
  rating: FeedbackRating;
  reason: FeedbackReason | null;
  note: string | null;
}

export interface ReviewCandidate {
  contentHash: string;
  title: string;
  album: string | null;
  reviewed: boolean;
  feedback: ReviewFeedback[];
}

export async function reviewQueue(
  query: string,
  showReviewed: boolean,
  offset: number,
  setId: string | null = null
) {
  // Same priority cascade and per-song recording selection as the CLI. Filter
  // feedback at song level after choosing the target: any feedback on an active
  // recording counts, including resolved feedback and other users' ratings.
  // A set only subsets the pool; the cascade order is untouched.
  const setFilter = setId
    ? sql`AND EXISTS (SELECT 1 FROM lyrics_review_set_songs lrs
        WHERE lrs.set_id = ${setId} AND lrs.song_id = s.id)`
    : sql``;
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
        AND s.deleted_at IS NULL AND r.r2_audio_url IS NOT NULL ${setFilter}
    ), candidates AS (
      SELECT pool.*, EXISTS (
        SELECT 1 FROM recordings reviewed_recording
        JOIN lyrics_feedback feedback
          ON feedback.recording_content_hash = reviewed_recording.content_hash
        WHERE reviewed_recording.song_id = pool.song_id
          AND reviewed_recording.deleted_at IS NULL
      ) AS reviewed
      FROM pool WHERE rec_rank = 1
    )
    SELECT p.content_hash AS "contentHash", p.title, p.album_name AS album,
      p.reviewed, (
        SELECT coalesce(jsonb_agg(jsonb_build_object(
          'id', feedback.id, 'rating', feedback.rating,
          'reason', feedback.reason, 'note', feedback.note
        ) ORDER BY feedback.updated_at DESC, feedback.id DESC), '[]'::jsonb)
        FROM recordings reviewed_recording
        JOIN lyrics_feedback feedback
          ON feedback.recording_content_hash = reviewed_recording.content_hash
        WHERE reviewed_recording.song_id = p.song_id
          AND reviewed_recording.deleted_at IS NULL
      ) AS feedback
    FROM candidates p
    LEFT JOIN (SELECT song_id, COUNT(*) n FROM songset_items GROUP BY song_id) si ON si.song_id = p.song_id
    LEFT JOIN (SELECT song_id, COUNT(*) n FROM user_favorite_songs GROUP BY song_id) fav ON fav.song_id = p.song_id
    LEFT JOIN (SELECT album_name, album_series, MAX(created_at) newest FROM songs
      GROUP BY album_name, album_series) al
      ON al.album_name IS NOT DISTINCT FROM p.album_name
      AND al.album_series IS NOT DISTINCT FROM p.album_series
    WHERE (${query} = '' OR strpos(lower(p.title), lower(${query})) > 0
        OR strpos(lower(coalesce(p.album_name, '')), lower(${query})) > 0)
      AND (${query !== '' || showReviewed} OR NOT p.reviewed)
    ORDER BY p.latest_sad_at DESC NULLS LAST, coalesce(si.n, 0) DESC,
      coalesce(fav.n, 0) DESC, al.newest DESC NULLS LAST, p.song_id ASC
    LIMIT 51 OFFSET ${offset}
  `);
  // Neon HTTP returns { rows }; the TCP Postgres test driver returns an array.
  const rows = (Array.isArray(result) ? result : result.rows) as unknown as ReviewCandidate[];
  return { recordings: rows.slice(0, 50), hasMore: rows.length > 50 };
}

export interface ReviewSetSummary {
  id: string;
  name: string;
  total: number;
  done: number;
}

/**
 * Review sets with progress. `done` uses the queue's derived reviewed rule:
 * any Lyrics Feedback on any non-deleted recording of the Song. Completed
 * sets stay in the list — the client marks them, nothing prunes them.
 */
export async function reviewSets(): Promise<ReviewSetSummary[]> {
  const result = await db.execute(sql`
    SELECT s.id, s.name,
      COUNT(m.song_id)::int AS total,
      (COUNT(m.song_id) FILTER (WHERE EXISTS (
        SELECT 1 FROM recordings reviewed_recording
        JOIN lyrics_feedback feedback
          ON feedback.recording_content_hash = reviewed_recording.content_hash
        WHERE reviewed_recording.song_id = m.song_id
          AND reviewed_recording.deleted_at IS NULL
      )))::int AS done
    FROM lyrics_review_sets s
    LEFT JOIN lyrics_review_set_songs m ON m.set_id = s.id
    GROUP BY s.id
    ORDER BY s.created_at DESC, s.name ASC
  `);
  // Neon HTTP returns { rows }; the TCP Postgres test driver returns an array.
  return (Array.isArray(result) ? result : result.rows) as unknown as ReviewSetSummary[];
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
