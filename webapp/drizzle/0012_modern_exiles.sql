ALTER TABLE "songsets" ADD COLUMN "last_completed_render_job_id" text;--> statement-breakpoint

UPDATE "songsets" SET "last_completed_render_job_id" = "latest_render_job_id"
  WHERE "latest_render_job_id" IN (SELECT "id" FROM "render_jobs" WHERE "status" = 'completed');