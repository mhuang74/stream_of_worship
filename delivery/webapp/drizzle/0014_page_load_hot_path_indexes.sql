CREATE INDEX IF NOT EXISTS "idx_songsets_user_updated" ON "songsets" ("user_id", "updated_at");--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "idx_songset_items_songset_position" ON "songset_items" ("songset_id", "position");--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "idx_songset_items_songset_updated" ON "songset_items" ("songset_id", "updated_at");--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "idx_render_jobs_songset_created" ON "render_jobs" ("songset_id", "created_at");--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "idx_render_jobs_status_updated" ON "render_jobs" ("status", "updated_at");
