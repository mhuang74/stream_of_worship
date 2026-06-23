ALTER TABLE "render_jobs" ALTER COLUMN "completed_at" SET NOT NULL;--> statement-breakpoint
ALTER TABLE "songset_items" ALTER COLUMN "created_at" SET NOT NULL;--> statement-breakpoint
ALTER TABLE "songsets" ALTER COLUMN "created_at" SET NOT NULL;--> statement-breakpoint
ALTER TABLE "songsets" ALTER COLUMN "updated_at" SET NOT NULL;