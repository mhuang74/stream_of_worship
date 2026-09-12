/**
 * Lyrics-situation resolution + Lyrics Feedback validation (issue #194).
 *
 * `resolveLyricsSituation` mirrors the resolution order of
 * /api/lyrics/[recordingContentHash]: R2 canonical LRC (unless
 * lrcStatus === "missing"), then scraped unsynced text (lyricsLines JSON
 * array, then lyricsRaw), else none. Keeping it in one server-side place
 * keeps the feedback API's validation matrix in agreement with what the
 * user actually sees in the lyrics panel.
 */

import { db } from "@/db";
import { recordings, songs } from "@/db/schema";
import { eq } from "drizzle-orm";
import { createR2ClientFromEnv } from "@/lib/r2/client";
import { isValidLRC } from "@/lib/render/lrc-parser";

/** What lyrics content a Recording actually presents to a user right now. */
export type LyricsSituation =
  | { kind: "synced" } // parseable timestamped Lyrics (the happy case)
  | { kind: "unsynced" } // text fallback exists but no parseable timestamps
  | { kind: "none" }; // nothing to show

export type FeedbackRating = "happy" | "sad";
export type FeedbackReason = "missing" | "timing" | "wrong_text" | "other";

const FEEDBACK_RATINGS: readonly string[] = ["happy", "sad"];
const FEEDBACK_REASONS: readonly string[] = ["missing", "timing", "wrong_text", "other"];

export interface ValidationResult {
  valid: boolean;
  error?: string;
}

/**
 * Resolve a Recording's actual lyrics situation, ignoring any user override
 * (feedback is about the canonical Lyrics admins curate — ADR 0007).
 */
export async function resolveLyricsSituation(
  recordingContentHash: string
): Promise<LyricsSituation> {
  const [recording] = await db
    .select({
      hashPrefix: recordings.hashPrefix,
      lrcStatus: recordings.lrcStatus,
      songId: recordings.songId,
    })
    .from(recordings)
    .where(eq(recordings.contentHash, recordingContentHash))
    .limit(1);

  if (!recording) {
    return { kind: "none" };
  }

  // Step 1: canonical synced Lyrics from R2, unless the pipeline gave up.
  if (recording.lrcStatus !== "missing") {
    try {
      const r2Client = createR2ClientFromEnv();
      const signedUrlResult = await r2Client.getLrcSignedUrl(recording.hashPrefix);
      const r2Response = await fetch(signedUrlResult.url);
      if (r2Response.ok) {
        const lrcContent = await r2Response.text();
        if (lrcContent && isValidLRC(lrcContent)) {
          return { kind: "synced" };
        }
        // Text without parseable timestamps still counts as unsynced text.
        if (lrcContent) {
          return { kind: "unsynced" };
        }
      }
      // 404 or any non-ok response: fall through silently
    } catch {
      // Network error, DNS failure, R2 not configured, etc.: fall through silently
    }
  }

  // Step 2: scraped unsynced text via the recording's song.
  if (recording.songId) {
    const [song] = await db
      .select({
        lyricsLines: songs.lyricsLines,
        lyricsRaw: songs.lyricsRaw,
      })
      .from(songs)
      .where(eq(songs.id, recording.songId))
      .limit(1);

    if (song) {
      if (song.lyricsLines) {
        try {
          const parsed = JSON.parse(song.lyricsLines);
          if (Array.isArray(parsed)) {
            const lines = parsed.filter((l): l is string => typeof l === "string");
            if (lines.length > 0) {
              return { kind: "unsynced" };
            }
          }
        } catch {
          // invalid JSON: fall through to lyricsRaw
        }
      }
      if (song.lyricsRaw) {
        return { kind: "unsynced" };
      }
    }
  }

  return { kind: "none" };
}

/**
 * State-aware validation matrix (spec issue #194):
 * - happy accepted only when Lyrics exist (synced or unsynced);
 * - missing accepted only when no parseable synced Lyrics;
 * - timing accepted only when parseable synced Lyrics exist;
 * - wrong_text / other accepted in any state;
 * - reason must be null for happy and non-null for sad.
 */
export function validateFeedbackSubmission(
  rating: unknown,
  reason: unknown,
  situation: LyricsSituation
): ValidationResult {
  if (typeof rating !== "string" || !FEEDBACK_RATINGS.includes(rating)) {
    return { valid: false, error: `rating must be one of: ${FEEDBACK_RATINGS.join(", ")}` };
  }

  if (rating === "happy") {
    if (reason !== undefined && reason !== null) {
      return { valid: false, error: "reason must be null when rating is happy" };
    }
    if (situation.kind === "none") {
      return { valid: false, error: "happy requires lyrics, but this Recording has none" };
    }
    return { valid: true };
  }

  // rating === "sad"
  if (typeof reason !== "string" || !FEEDBACK_REASONS.includes(reason)) {
    return { valid: false, error: `sad requires reason to be one of: ${FEEDBACK_REASONS.join(", ")}` };
  }

  if (reason === "missing" && situation.kind === "synced") {
    return { valid: false, error: "missing is invalid when synced lyrics exist" };
  }
  if (reason === "timing" && situation.kind !== "synced") {
    return { valid: false, error: "timing requires synced lyrics to exist" };
  }
  return { valid: true };
}