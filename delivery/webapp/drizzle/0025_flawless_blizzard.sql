-- Migration: 0025_lyrics_feedback_note
-- Description: Optional free-text note on sad Lyrics Feedback (issue #194).
-- The note is user-supplied detail about what is wrong with the Lyrics;
-- only meaningful for sad rows (forced NULL for happy).
--
-- Hand-written idempotent ALTER (same precedent as 0018/0022/0023): the
-- table itself was created by 0024/Drizzle, so this only adds the column
-- and mirrors the admin CLI's user-data bootstrap ALTER in
-- ops/admin-cli/src/stream_of_worship/db/app/user_data_schema.py.
-- The drizzle snapshot chain was previously missing entries for
-- 0022-0024; regenerating from schema.ts at this point also reconciles
-- the snapshot (see meta/0025_snapshot.json).

DO $$ BEGIN
    ALTER TABLE "lyrics_feedback" ADD COLUMN IF NOT EXISTS "note" text;
END $$;
