CREATE TABLE "theme_anchors" (
	"theme" text PRIMARY KEY NOT NULL,
	"embedding" vector(1536) NOT NULL,
	"model_version" text DEFAULT 'text-embedding-3-small' NOT NULL,
	"created_at" timestamp with time zone DEFAULT now()
);
--> statement-breakpoint
ALTER TABLE "recordings" ADD COLUMN "key_algorithm_version" text;--> statement-breakpoint
ALTER TABLE "recordings" ADD COLUMN "key_score_margin" real;--> statement-breakpoint
ALTER TABLE "recordings" ADD COLUMN "key_window_agreement" real;--> statement-breakpoint
ALTER TABLE "recordings" ADD COLUMN "key_candidates" text;--> statement-breakpoint
ALTER TABLE "recordings" ADD COLUMN "key_detected_at" timestamp with time zone;--> statement-breakpoint
ALTER TABLE "songs" ADD COLUMN "musical_key_root" text;--> statement-breakpoint
ALTER TABLE "songs" ADD COLUMN "musical_key_mode" text;--> statement-breakpoint
ALTER TABLE "songs" ADD COLUMN "musical_key_start_root" text;--> statement-breakpoint
ALTER TABLE "songs" ADD COLUMN "musical_key_end_root" text;--> statement-breakpoint
ALTER TABLE "songs" ADD COLUMN "musical_key_start_pitch_class" integer;--> statement-breakpoint
ALTER TABLE "songs" ADD COLUMN "musical_key_end_pitch_class" integer;--> statement-breakpoint
ALTER TABLE "songs" ADD COLUMN "musical_key_parse_status" text;--> statement-breakpoint
CREATE INDEX "idx_theme_anchors_embedding_cosine" ON "theme_anchors" USING btree ("embedding" vector_cosine_ops);--> statement-breakpoint
CREATE INDEX "idx_songset_items_song_id" ON "songset_items" USING btree ("song_id");