CREATE TABLE "song_line_embedding" (
	"id" serial PRIMARY KEY NOT NULL,
	"song_id" text NOT NULL,
	"line_index" integer NOT NULL,
	"line_text" text NOT NULL,
	"embedding" vector(1536) NOT NULL,
	"model_version" text DEFAULT 'text-embedding-3-small' NOT NULL
);
--> statement-breakpoint
ALTER TABLE "song_embedding" DROP CONSTRAINT "song_embedding_recording_content_hash_model_version_unique";--> statement-breakpoint
ALTER TABLE "song_embedding" DROP CONSTRAINT "song_embedding_recording_content_hash_recordings_content_hash_fk";
--> statement-breakpoint
DROP INDEX "idx_song_embedding_hash";--> statement-breakpoint
ALTER TABLE "song_embedding" ALTER COLUMN "embedding" SET DATA TYPE vector(1536);--> statement-breakpoint
ALTER TABLE "song_embedding" ALTER COLUMN "model_version" SET DEFAULT 'text-embedding-3-small';--> statement-breakpoint
ALTER TABLE "render_jobs" ADD COLUMN "font_family" text DEFAULT 'noto_serif_tc' NOT NULL;--> statement-breakpoint
ALTER TABLE "song_embedding" ADD COLUMN "song_id" text PRIMARY KEY NOT NULL;--> statement-breakpoint
ALTER TABLE "song_embedding" ADD COLUMN "content_hash" text NOT NULL;--> statement-breakpoint
ALTER TABLE "user_settings" ADD COLUMN "default_font_family" text DEFAULT 'noto_serif_tc' NOT NULL;--> statement-breakpoint
ALTER TABLE "song_line_embedding" ADD CONSTRAINT "song_line_embedding_song_id_songs_id_fk" FOREIGN KEY ("song_id") REFERENCES "public"."songs"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "idx_song_line_embedding_song" ON "song_line_embedding" USING btree ("song_id");--> statement-breakpoint
CREATE INDEX "idx_song_line_embedding_cosine" ON "song_line_embedding" USING btree ("embedding" vector_cosine_ops);--> statement-breakpoint
ALTER TABLE "song_embedding" ADD CONSTRAINT "song_embedding_song_id_songs_id_fk" FOREIGN KEY ("song_id") REFERENCES "public"."songs"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "idx_song_embedding_cosine" ON "song_embedding" USING btree ("embedding" vector_cosine_ops);--> statement-breakpoint
ALTER TABLE "song_embedding" DROP COLUMN "id";--> statement-breakpoint
ALTER TABLE "song_embedding" DROP COLUMN "recording_content_hash";