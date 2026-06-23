ALTER TABLE "songset_items" ADD COLUMN "updated_at" timestamp with time zone DEFAULT now() NOT NULL;--> statement-breakpoint
ALTER TABLE "user_settings" ADD COLUMN "default_gap_beats" real DEFAULT 2 NOT NULL;--> statement-breakpoint
ALTER TABLE "user_settings" ADD COLUMN "default_video_template" text DEFAULT 'dark' NOT NULL;--> statement-breakpoint
ALTER TABLE "user_settings" ADD COLUMN "default_resolution" text DEFAULT '720p' NOT NULL;--> statement-breakpoint
ALTER TABLE "user_settings" ADD COLUMN "lyrics_loop_window_seconds" real DEFAULT 3 NOT NULL;--> statement-breakpoint
ALTER TABLE "user_settings" ADD COLUMN "default_font_size_preset" text DEFAULT 'M' NOT NULL;--> statement-breakpoint
ALTER TABLE "user_settings" ADD COLUMN "default_key_shift_semitones" integer DEFAULT 0 NOT NULL;--> statement-breakpoint
ALTER TABLE "user_settings" ADD COLUMN "timing_review_font" text DEFAULT 'sans' NOT NULL;