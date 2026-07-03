ALTER TABLE "songs" ADD COLUMN IF NOT EXISTS "musical_key_root" text;
--> statement-breakpoint
ALTER TABLE "songs" ADD COLUMN IF NOT EXISTS "musical_key_mode" text;
--> statement-breakpoint
ALTER TABLE "songs" ADD COLUMN IF NOT EXISTS "musical_key_start_root" text;
--> statement-breakpoint
ALTER TABLE "songs" ADD COLUMN IF NOT EXISTS "musical_key_end_root" text;
--> statement-breakpoint
ALTER TABLE "songs" ADD COLUMN IF NOT EXISTS "musical_key_start_pitch_class" integer;
--> statement-breakpoint
ALTER TABLE "songs" ADD COLUMN IF NOT EXISTS "musical_key_end_pitch_class" integer;
--> statement-breakpoint
ALTER TABLE "songs" ADD COLUMN IF NOT EXISTS "musical_key_parse_status" text;
--> statement-breakpoint
ALTER TABLE "recordings" ADD COLUMN IF NOT EXISTS "key_algorithm_version" text;
--> statement-breakpoint
ALTER TABLE "recordings" ADD COLUMN IF NOT EXISTS "key_score_margin" real;
--> statement-breakpoint
ALTER TABLE "recordings" ADD COLUMN IF NOT EXISTS "key_window_agreement" real;
--> statement-breakpoint
ALTER TABLE "recordings" ADD COLUMN IF NOT EXISTS "key_candidates" text;
--> statement-breakpoint
ALTER TABLE "recordings" ADD COLUMN IF NOT EXISTS "key_detected_at" timestamp with time zone;
