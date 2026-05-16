-- Add columns missing from the manually-bootstrapped songsets table.
-- Run once against Neon if drizzle-kit migrate did not apply migration 0000.
ALTER TABLE "songsets" ADD COLUMN IF NOT EXISTS "latest_render_job_id" text;
ALTER TABLE "songsets" ADD COLUMN IF NOT EXISTS "last_failed_render_job_id" text;
