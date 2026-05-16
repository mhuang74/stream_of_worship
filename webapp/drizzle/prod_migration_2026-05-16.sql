-- Production Migration: 2026-05-16
-- Run this against PROD database to sync schema with Drizzle definitions

-- 1. Enable pgvector extension (required for song_embedding table)
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Create render_jobs table (if not exists)
CREATE TABLE IF NOT EXISTS "render_jobs" (
    "id" text PRIMARY KEY NOT NULL,
    "songset_id" text NOT NULL,
    "user_id" bigint NOT NULL,
    "status" text DEFAULT 'queued' NOT NULL,
    "phase" text,
    "phase_index" integer,
    "total_phases" integer,
    "percent_complete" real DEFAULT 0,
    "estimated_seconds_left" real,
    "elapsed_seconds" real,
    "error_message" text,
    "template" text DEFAULT 'dark' NOT NULL,
    "resolution" text DEFAULT '720p' NOT NULL,
    "audio_enabled" boolean DEFAULT true NOT NULL,
    "video_enabled" boolean DEFAULT true NOT NULL,
    "font_size_preset" text DEFAULT 'M' NOT NULL,
    "include_title_card" boolean DEFAULT false NOT NULL,
    "title_card_duration_seconds" real DEFAULT 10,
    "mp3_r2_key" text,
    "mp4_r2_key" text,
    "chapters_r2_key" text,
    "created_at" timestamp with time zone DEFAULT now(),
    "updated_at" timestamp with time zone DEFAULT now(),
    "completed_at" timestamp with time zone
);

-- Add foreign keys for render_jobs (if not exist)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints 
        WHERE constraint_name = 'render_jobs_songset_id_songsets_id_fk'
    ) THEN
        ALTER TABLE "render_jobs" ADD CONSTRAINT "render_jobs_songset_id_songsets_id_fk" 
            FOREIGN KEY ("songset_id") REFERENCES "public"."songsets"("id") ON DELETE cascade ON UPDATE no action;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints 
        WHERE constraint_name = 'render_jobs_user_id_user_id_fk'
    ) THEN
        ALTER TABLE "render_jobs" ADD CONSTRAINT "render_jobs_user_id_user_id_fk" 
            FOREIGN KEY ("user_id") REFERENCES "public"."user"("id") ON DELETE cascade ON UPDATE no action;
    END IF;
END $$;

-- 3. Create song_embedding table (if not exists)
CREATE TABLE IF NOT EXISTS "song_embedding" (
    "id" text PRIMARY KEY NOT NULL,
    "recording_content_hash" text NOT NULL,
    "embedding" vector(1024) NOT NULL,
    "model_version" text NOT NULL,
    "created_at" timestamp with time zone DEFAULT now(),
    CONSTRAINT "song_embedding_recording_content_hash_model_version_unique" UNIQUE("recording_content_hash","model_version")
);

-- Add foreign key for song_embedding (if not exist)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints 
        WHERE constraint_name = 'song_embedding_recording_content_hash_recordings_content_hash_fk'
    ) THEN
        ALTER TABLE "song_embedding" ADD CONSTRAINT "song_embedding_recording_content_hash_recordings_content_hash_fk" 
            FOREIGN KEY ("recording_content_hash") REFERENCES "public"."recordings"("content_hash") ON DELETE cascade ON UPDATE no action;
    END IF;
END $$;

-- Create index for song_embedding
CREATE INDEX IF NOT EXISTS "idx_song_embedding_hash" ON "song_embedding" USING btree ("recording_content_hash");

-- 4. Add missing columns to songsets (if not exist)
ALTER TABLE "songsets" ADD COLUMN IF NOT EXISTS "latest_render_job_id" text;
ALTER TABLE "songsets" ADD COLUMN IF NOT EXISTS "last_failed_render_job_id" text;

-- 5. Rename constraint indexes to match Drizzle naming convention
-- (These are safe no-ops if already renamed or if old names don't exist)

-- recordings: recordings_hash_prefix_key -> recordings_hash_prefix_unique
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'recordings_hash_prefix_key') THEN
        ALTER INDEX "recordings_hash_prefix_key" RENAME TO "recordings_hash_prefix_unique";
    END IF;
END $$;

-- user: user_email_key -> user_email_unique
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'user_email_key') THEN
        ALTER INDEX "user_email_key" RENAME TO "user_email_unique";
    END IF;
END $$;

-- account: account_providerId_accountId_key -> account_providerId_accountId_unique
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'account_providerId_accountId_key') THEN
        ALTER INDEX "account_providerId_accountId_key" RENAME TO "account_providerId_accountId_unique";
    END IF;
END $$;

-- session: session_token_key -> session_token_unique
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'session_token_key') THEN
        ALTER INDEX "session_token_key" RENAME TO "session_token_unique";
    END IF;
END $$;
