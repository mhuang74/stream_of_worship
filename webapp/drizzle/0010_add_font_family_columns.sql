ALTER TABLE "render_jobs" ADD COLUMN "font_family" text NOT NULL DEFAULT 'noto_serif_tc';--> statement-breakpoint
ALTER TABLE "user_settings" ADD COLUMN "default_font_family" text NOT NULL DEFAULT 'noto_serif_tc';
