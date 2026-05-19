-- Add duration-based progress estimation columns.
-- Note: percent_complete and estimated_seconds_left are deprecated;
-- they are no longer written by the pipeline but retained for historical data.
ALTER TABLE "render_jobs" ADD COLUMN "estimated_total_seconds" real;--> statement-breakpoint
ALTER TABLE "render_jobs" ADD COLUMN "total_duration_seconds" real;--> statement-breakpoint
ALTER TABLE "render_jobs" ADD COLUMN "started_at" timestamp with time zone;