ALTER TABLE "glue_jobs" ALTER COLUMN "from_item_id" DROP NOT NULL;--> statement-breakpoint
ALTER TABLE "glue_jobs" ALTER COLUMN "to_item_id" DROP NOT NULL;--> statement-breakpoint
ALTER TABLE "glue_jobs" ADD COLUMN "target" text DEFAULT 'junction' NOT NULL;--> statement-breakpoint
ALTER TABLE "glue_jobs" ADD COLUMN "boundary_item_id" text;--> statement-breakpoint
ALTER TABLE "glue_jobs" ADD COLUMN "boundary_revision" text;--> statement-breakpoint
ALTER TABLE "render_jobs" ADD COLUMN "first_lyrics_hold" boolean DEFAULT false NOT NULL;--> statement-breakpoint
ALTER TABLE "songsets" ADD COLUMN "boundary_settings" jsonb DEFAULT '{}'::jsonb NOT NULL;--> statement-breakpoint
ALTER TABLE "songsets" ADD COLUMN "boundary_updated_at" timestamp with time zone;--> statement-breakpoint
ALTER TABLE "glue_jobs" ADD CONSTRAINT "glue_jobs_target_items_check" CHECK (
      ("glue_jobs"."target" = 'junction' AND "glue_jobs"."from_item_id" IS NOT NULL AND "glue_jobs"."to_item_id" IS NOT NULL AND "glue_jobs"."boundary_item_id" IS NULL)
      OR ("glue_jobs"."target" IN ('intro', 'outro') AND "glue_jobs"."boundary_item_id" IS NOT NULL AND "glue_jobs"."from_item_id" IS NULL AND "glue_jobs"."to_item_id" IS NULL)
    );