-- Migration: 0024_lyrics_feedback
-- Description: Per-user Lyrics Feedback on a Recording (issue #194).
-- See specs/collect_user_lyrics_feedback.md and docs/adr/0007-lyrics-feedback-is-advisory.md
--
-- Advisory signal for admins curating canonical Lyrics: nothing but an
-- explicit admin action writes resolved_at. One row per user per Recording;
-- rating is 'happy' | 'sad', reason is NULL for happy and one of
-- 'missing' | 'timing' | 'wrong_text' | 'other' for sad.
--
-- Hand-written (same precedent as 0018/0022/0023): mirrors the admin CLI's
-- user-data bootstrap DDL in
-- ops/admin-cli/src/stream_of_worship/db/app/user_data_schema.py
-- so both deploy paths create the identical table.

CREATE TABLE IF NOT EXISTS "lyrics_feedback" (
	"id" text PRIMARY KEY,
	"user_id" bigint NOT NULL,
	"recording_content_hash" text NOT NULL,
	"rating" text NOT NULL,
	"reason" text,
	"resolved_at" timestamp with time zone,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "lyrics_feedback_user_id_recording_content_hash_unique" UNIQUE("user_id","recording_content_hash")
);
--> statement-breakpoint
DO $$ BEGIN
	ALTER TABLE "lyrics_feedback" ADD CONSTRAINT "lyrics_feedback_user_id_user_id_fk" FOREIGN KEY ("user_id") REFERENCES "public"."user"("id") ON DELETE cascade ON UPDATE no action;
EXCEPTION
	WHEN duplicate_object THEN NULL;
END $$;
--> statement-breakpoint
DO $$ BEGIN
	ALTER TABLE "lyrics_feedback" ADD CONSTRAINT "lyrics_feedback_recording_content_hash_recordings_content_hash_fk" FOREIGN KEY ("recording_content_hash") REFERENCES "public"."recordings"("content_hash") ON DELETE cascade ON UPDATE no action;
EXCEPTION
	WHEN duplicate_object THEN NULL;
END $$;
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "idx_lyrics_feedback_recording_resolved" ON "lyrics_feedback" USING btree ("recording_content_hash","resolved_at");