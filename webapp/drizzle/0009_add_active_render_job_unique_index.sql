CREATE UNIQUE INDEX CONCURRENTLY uq_render_jobs_active_per_songset_user
  ON render_jobs (songset_id, user_id)
  WHERE status IN ('queued', 'running');--> statement-breakpoint
