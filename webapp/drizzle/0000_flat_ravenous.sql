CREATE EXTENSION IF NOT EXISTS vector;
--> statement-breakpoint
CREATE TABLE "account" (
	"id" bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY (sequence name "account_id_seq" INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START WITH 1 CACHE 1),
	"userId" bigint NOT NULL,
	"accountId" text NOT NULL,
	"providerId" text NOT NULL,
	"accessToken" text,
	"refreshToken" text,
	"idToken" text,
	"accessTokenExpiresAt" timestamp with time zone,
	"refreshTokenExpiresAt" timestamp with time zone,
	"scope" text,
	"password" text,
	"createdAt" timestamp with time zone DEFAULT now() NOT NULL,
	"updatedAt" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "account_providerId_accountId_unique" UNIQUE("providerId","accountId")
);
--> statement-breakpoint
CREATE TABLE "lyric_mark" (
	"id" text PRIMARY KEY NOT NULL,
	"user_id" bigint NOT NULL,
	"recording_content_hash" text NOT NULL,
	"timestamp_seconds" double precision NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "lyric_mark_user_id_recording_content_hash_timestamp_seconds_unique" UNIQUE("user_id","recording_content_hash","timestamp_seconds")
);
--> statement-breakpoint
CREATE TABLE "recordings" (
	"content_hash" text PRIMARY KEY NOT NULL,
	"hash_prefix" text NOT NULL,
	"song_id" text,
	"original_filename" text NOT NULL,
	"file_size_bytes" integer NOT NULL,
	"imported_at" text NOT NULL,
	"r2_audio_url" text,
	"r2_stems_url" text,
	"r2_lrc_url" text,
	"duration_seconds" real,
	"tempo_bpm" real,
	"musical_key" text,
	"musical_mode" text,
	"key_confidence" real,
	"loudness_db" real,
	"beats" text,
	"downbeats" text,
	"sections" text,
	"embeddings_shape" text,
	"analysis_status" text DEFAULT 'pending',
	"analysis_job_id" text,
	"lrc_status" text DEFAULT 'pending',
	"lrc_job_id" text,
	"created_at" timestamp with time zone DEFAULT now(),
	"updated_at" timestamp with time zone DEFAULT now(),
	"youtube_url" text,
	"visibility_status" text,
	"download_status" text DEFAULT 'pending',
	"deleted_at" timestamp with time zone,
	CONSTRAINT "recordings_hash_prefix_unique" UNIQUE("hash_prefix")
);
--> statement-breakpoint
CREATE TABLE "render_jobs" (
	"id" text PRIMARY KEY NOT NULL,
	"songset_id" text NOT NULL,
	"user_id" bigint NOT NULL,
	"status" text DEFAULT 'queued' NOT NULL,
	"phase" text,
	"phase_index" integer,
	"total_phases" integer,
	"percent_complete" real DEFAULT 0,
	"estimated_seconds_left" real,
	"elapsed_seconds" real,
	"error_message" text,
	"template" text DEFAULT 'dark' NOT NULL,
	"resolution" text DEFAULT '720p' NOT NULL,
	"audio_enabled" boolean DEFAULT true NOT NULL,
	"video_enabled" boolean DEFAULT true NOT NULL,
	"font_size_preset" text DEFAULT 'M' NOT NULL,
	"include_title_card" boolean DEFAULT false NOT NULL,
	"title_card_duration_seconds" real DEFAULT 10,
	"mp3_r2_key" text,
	"mp4_r2_key" text,
	"chapters_r2_key" text,
	"created_at" timestamp with time zone DEFAULT now(),
	"updated_at" timestamp with time zone DEFAULT now(),
	"completed_at" timestamp with time zone
);
--> statement-breakpoint
CREATE TABLE "session" (
	"id" bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY (sequence name "session_id_seq" INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START WITH 1 CACHE 1),
	"userId" bigint NOT NULL,
	"token" text NOT NULL,
	"expiresAt" timestamp with time zone NOT NULL,
	"ipAddress" text,
	"userAgent" text,
	"createdAt" timestamp with time zone DEFAULT now() NOT NULL,
	"updatedAt" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "session_token_unique" UNIQUE("token")
);
--> statement-breakpoint
CREATE TABLE "song_embedding" (
	"id" text PRIMARY KEY NOT NULL,
	"recording_content_hash" text NOT NULL,
	"embedding" vector(1024) NOT NULL,
	"model_version" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now(),
	CONSTRAINT "song_embedding_recording_content_hash_model_version_unique" UNIQUE("recording_content_hash","model_version")
);
--> statement-breakpoint
CREATE TABLE "songs" (
	"id" text PRIMARY KEY NOT NULL,
	"title" text NOT NULL,
	"title_pinyin" text,
	"composer" text,
	"lyricist" text,
	"album_name" text,
	"album_series" text,
	"musical_key" text,
	"lyrics_raw" text,
	"lyrics_lines" text,
	"sections" text,
	"source_url" text NOT NULL,
	"table_row_number" integer,
	"scraped_at" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now(),
	"updated_at" timestamp with time zone DEFAULT now(),
	"deleted_at" timestamp with time zone
);
--> statement-breakpoint
CREATE TABLE "songset_items" (
	"id" text PRIMARY KEY NOT NULL,
	"songset_id" text NOT NULL,
	"song_id" text NOT NULL,
	"recording_hash_prefix" text,
	"position" integer NOT NULL,
	"gap_beats" real DEFAULT 2,
	"crossfade_enabled" integer DEFAULT 0,
	"crossfade_duration_seconds" real,
	"key_shift_semitones" integer DEFAULT 0,
	"tempo_ratio" real DEFAULT 1,
	"created_at" timestamp with time zone DEFAULT now()
);
--> statement-breakpoint
CREATE TABLE "songset_share" (
	"token" text PRIMARY KEY NOT NULL,
	"songset_id" text NOT NULL,
	"render_job_id" text NOT NULL,
	"created_by_user_id" bigint NOT NULL,
	"allow_download" boolean DEFAULT false NOT NULL,
	"expires_at" timestamp with time zone,
	"revoked_at" timestamp with time zone,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "songsets" (
	"id" text PRIMARY KEY NOT NULL,
	"user_id" bigint NOT NULL,
	"name" text NOT NULL,
	"description" text,
	"created_at" timestamp with time zone DEFAULT now(),
	"updated_at" timestamp with time zone DEFAULT now(),
	"latest_render_job_id" text,
	"last_failed_render_job_id" text
);
--> statement-breakpoint
CREATE TABLE "user_lrc_override" (
	"id" text PRIMARY KEY NOT NULL,
	"user_id" bigint NOT NULL,
	"recording_content_hash" text NOT NULL,
	"lrc_content" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "user_lrc_override_user_id_recording_content_hash_unique" UNIQUE("user_id","recording_content_hash")
);
--> statement-breakpoint
CREATE TABLE "user_settings" (
	"user_id" bigint PRIMARY KEY NOT NULL,
	"offline_auto_cache" boolean DEFAULT true NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "user" (
	"id" bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY (sequence name "user_id_seq" INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START WITH 1 CACHE 1),
	"name" text NOT NULL,
	"email" text NOT NULL,
	"emailVerified" boolean DEFAULT false NOT NULL,
	"image" text,
	"createdAt" timestamp with time zone DEFAULT now() NOT NULL,
	"updatedAt" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "user_email_unique" UNIQUE("email")
);
--> statement-breakpoint
CREATE TABLE "verification" (
	"id" bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY (sequence name "verification_id_seq" INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START WITH 1 CACHE 1),
	"identifier" text NOT NULL,
	"value" text NOT NULL,
	"expiresAt" timestamp with time zone NOT NULL,
	"createdAt" timestamp with time zone DEFAULT now() NOT NULL,
	"updatedAt" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
ALTER TABLE "account" ADD CONSTRAINT "account_userId_user_id_fk" FOREIGN KEY ("userId") REFERENCES "public"."user"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "lyric_mark" ADD CONSTRAINT "lyric_mark_user_id_user_id_fk" FOREIGN KEY ("user_id") REFERENCES "public"."user"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "lyric_mark" ADD CONSTRAINT "lyric_mark_recording_content_hash_recordings_content_hash_fk" FOREIGN KEY ("recording_content_hash") REFERENCES "public"."recordings"("content_hash") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "recordings" ADD CONSTRAINT "recordings_song_id_songs_id_fk" FOREIGN KEY ("song_id") REFERENCES "public"."songs"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "render_jobs" ADD CONSTRAINT "render_jobs_songset_id_songsets_id_fk" FOREIGN KEY ("songset_id") REFERENCES "public"."songsets"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "render_jobs" ADD CONSTRAINT "render_jobs_user_id_user_id_fk" FOREIGN KEY ("user_id") REFERENCES "public"."user"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "session" ADD CONSTRAINT "session_userId_user_id_fk" FOREIGN KEY ("userId") REFERENCES "public"."user"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "song_embedding" ADD CONSTRAINT "song_embedding_recording_content_hash_recordings_content_hash_fk" FOREIGN KEY ("recording_content_hash") REFERENCES "public"."recordings"("content_hash") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "songset_items" ADD CONSTRAINT "songset_items_songset_id_songsets_id_fk" FOREIGN KEY ("songset_id") REFERENCES "public"."songsets"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "songset_share" ADD CONSTRAINT "songset_share_songset_id_songsets_id_fk" FOREIGN KEY ("songset_id") REFERENCES "public"."songsets"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "songset_share" ADD CONSTRAINT "songset_share_created_by_user_id_user_id_fk" FOREIGN KEY ("created_by_user_id") REFERENCES "public"."user"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "songsets" ADD CONSTRAINT "songsets_user_id_user_id_fk" FOREIGN KEY ("user_id") REFERENCES "public"."user"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "user_lrc_override" ADD CONSTRAINT "user_lrc_override_user_id_user_id_fk" FOREIGN KEY ("user_id") REFERENCES "public"."user"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "user_lrc_override" ADD CONSTRAINT "user_lrc_override_recording_content_hash_recordings_content_hash_fk" FOREIGN KEY ("recording_content_hash") REFERENCES "public"."recordings"("content_hash") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "user_settings" ADD CONSTRAINT "user_settings_user_id_user_id_fk" FOREIGN KEY ("user_id") REFERENCES "public"."user"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "idx_song_embedding_hash" ON "song_embedding" USING btree ("recording_content_hash");