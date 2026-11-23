CREATE TABLE "glue_excerpts" (
	"user_id" bigint NOT NULL,
	"logical_key" text NOT NULL,
	"version" integer DEFAULT 2 NOT NULL,
	"side" text NOT NULL,
	"seconds" double precision NOT NULL,
	"content_hash" text NOT NULL,
	"canonical_steps_json" text,
	"audio_sha256" text,
	"elevenlabs_song_id" text,
	"phase" text NOT NULL,
	"rejection_code" text,
	"safe_failure_detail" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "glue_excerpts_user_id_logical_key_pk" PRIMARY KEY("user_id","logical_key")
);
--> statement-breakpoint
CREATE TABLE "glue_jobs" (
	"id" uuid PRIMARY KEY NOT NULL,
	"user_id" bigint NOT NULL,
	"songset_id" text NOT NULL,
	"from_item_id" text NOT NULL,
	"to_item_id" text NOT NULL,
	"client_request_id" uuid NOT NULL,
	"take_mode" text NOT NULL,
	"descriptor_key" text NOT NULL,
	"descriptor_json" text NOT NULL,
	"expected_from_updated_at" timestamp with time zone NOT NULL,
	"expected_to_updated_at" timestamp with time zone NOT NULL,
	"phase" text NOT NULL,
	"active" boolean NOT NULL,
	"dispatch_state" text NOT NULL,
	"dispatch_attempt" integer DEFAULT 0 NOT NULL,
	"attempt_id" uuid NOT NULL,
	"pending_take_id" uuid NOT NULL,
	"take_ordinal" integer NOT NULL,
	"seed" bigint NOT NULL,
	"paid_reservation_id" uuid,
	"paid_reservation_state" text,
	"provider_contacted" boolean DEFAULT false NOT NULL,
	"provider_phase" text,
	"provider_status" integer,
	"retry_kind" text,
	"resulting_take_id" uuid,
	"active_take_id" uuid,
	"raw_response_r2_key" text,
	"raw_response_sha256" text,
	"raw_response_bytes" bigint,
	"error_code" text,
	"error_detail" text,
	"heartbeat_at" timestamp with time zone,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"started_at" timestamp with time zone,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	"finished_at" timestamp with time zone,
	CONSTRAINT "glue_jobs_pending_take_id_unique" UNIQUE("pending_take_id"),
	CONSTRAINT "glue_jobs_user_id_client_request_id_unique" UNIQUE("user_id","client_request_id")
);
--> statement-breakpoint
CREATE TABLE "glue_paid_attempts" (
	"id" uuid PRIMARY KEY NOT NULL,
	"user_id" bigint NOT NULL,
	"glue_job_id" uuid NOT NULL,
	"state" text NOT NULL,
	"consumed_at" timestamp with time zone,
	"released_at" timestamp with time zone,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "glue_paid_attempts_glue_job_id_unique" UNIQUE("glue_job_id")
);
--> statement-breakpoint
CREATE TABLE "glue_takes" (
	"id" uuid PRIMARY KEY NOT NULL,
	"user_id" bigint NOT NULL,
	"descriptor_key" text NOT NULL,
	"descriptor_json" text NOT NULL,
	"ordinal" integer NOT NULL,
	"seed" bigint NOT NULL,
	"status" text NOT NULL,
	"r2_key" text,
	"seconds" double precision,
	"raw_response_r2_key" text,
	"raw_response_sha256" text,
	"raw_response_bytes" bigint,
	"metrics_json" text DEFAULT '{}' NOT NULL,
	"reasons_json" text DEFAULT '[]' NOT NULL,
	"error_code" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "glue_takes_user_id_descriptor_key_ordinal_unique" UNIQUE("user_id","descriptor_key","ordinal")
);
--> statement-breakpoint
ALTER TABLE "render_jobs" ADD COLUMN "glue_transitions" text;--> statement-breakpoint
ALTER TABLE "songset_items" ADD COLUMN "glue_enabled" boolean DEFAULT false NOT NULL;--> statement-breakpoint
ALTER TABLE "songset_items" ADD COLUMN "glue_beats" integer DEFAULT 4 NOT NULL;--> statement-breakpoint
ALTER TABLE "songset_items" ADD COLUMN "glue_active_take_id" uuid;--> statement-breakpoint
ALTER TABLE "glue_excerpts" ADD CONSTRAINT "glue_excerpts_user_id_user_id_fk" FOREIGN KEY ("user_id") REFERENCES "public"."user"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "glue_jobs" ADD CONSTRAINT "glue_jobs_user_id_user_id_fk" FOREIGN KEY ("user_id") REFERENCES "public"."user"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "glue_jobs" ADD CONSTRAINT "glue_jobs_songset_id_songsets_id_fk" FOREIGN KEY ("songset_id") REFERENCES "public"."songsets"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "glue_jobs" ADD CONSTRAINT "glue_jobs_from_item_id_songset_items_id_fk" FOREIGN KEY ("from_item_id") REFERENCES "public"."songset_items"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "glue_jobs" ADD CONSTRAINT "glue_jobs_to_item_id_songset_items_id_fk" FOREIGN KEY ("to_item_id") REFERENCES "public"."songset_items"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "glue_jobs" ADD CONSTRAINT "glue_jobs_resulting_take_id_glue_takes_id_fk" FOREIGN KEY ("resulting_take_id") REFERENCES "public"."glue_takes"("id") ON DELETE set null ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "glue_jobs" ADD CONSTRAINT "glue_jobs_active_take_id_glue_takes_id_fk" FOREIGN KEY ("active_take_id") REFERENCES "public"."glue_takes"("id") ON DELETE set null ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "glue_paid_attempts" ADD CONSTRAINT "glue_paid_attempts_user_id_user_id_fk" FOREIGN KEY ("user_id") REFERENCES "public"."user"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "glue_paid_attempts" ADD CONSTRAINT "glue_paid_attempts_glue_job_id_glue_jobs_id_fk" FOREIGN KEY ("glue_job_id") REFERENCES "public"."glue_jobs"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "glue_takes" ADD CONSTRAINT "glue_takes_user_id_user_id_fk" FOREIGN KEY ("user_id") REFERENCES "public"."user"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "idx_glue_jobs_user_active_created" ON "glue_jobs" USING btree ("user_id","active","created_at" DESC NULLS LAST);--> statement-breakpoint
CREATE INDEX "idx_glue_jobs_phase_heartbeat" ON "glue_jobs" USING btree ("phase","heartbeat_at");--> statement-breakpoint
CREATE INDEX "idx_glue_jobs_songset_from_created" ON "glue_jobs" USING btree ("songset_id","from_item_id","created_at" DESC NULLS LAST);--> statement-breakpoint
CREATE INDEX "idx_glue_paid_attempts_user_created" ON "glue_paid_attempts" USING btree ("user_id","created_at" DESC NULLS LAST);--> statement-breakpoint
CREATE INDEX "idx_glue_takes_user_descriptor_status_created" ON "glue_takes" USING btree ("user_id","descriptor_key","status","created_at" DESC NULLS LAST);--> statement-breakpoint
ALTER TABLE "songset_items" ADD CONSTRAINT "songset_items_glue_active_take_id_glue_takes_id_fk" FOREIGN KEY ("glue_active_take_id") REFERENCES "public"."glue_takes"("id") ON DELETE set null ON UPDATE no action;--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS uq_glue_jobs_active_per_user
  ON "glue_jobs" ("user_id") WHERE "active";--> statement-breakpoint
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'songset_items_glue_beats_check'
    ) THEN
        ALTER TABLE "songset_items" ADD CONSTRAINT "songset_items_glue_beats_check"
            CHECK (glue_beats IN (2,4,8,16));
    END IF;
END $$;--> statement-breakpoint
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'glue_jobs_active_phase_check'
    ) THEN
        ALTER TABLE "glue_jobs" ADD CONSTRAINT "glue_jobs_active_phase_check" CHECK (
            active = (phase IN ('queued','preparing','uploading','composing','storing-response','staging')));
    END IF;
END $$;--> statement-breakpoint
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'glue_jobs_phase_check'
    ) THEN
        ALTER TABLE "glue_jobs" ADD CONSTRAINT "glue_jobs_phase_check" CHECK (
            phase IN ('queued','preparing','uploading','composing','storing-response','staging',
                      'ready','failed','cancelled','dispatch-failed'));
    END IF;
END $$;--> statement-breakpoint
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'glue_jobs_take_mode_check'
    ) THEN
        ALTER TABLE "glue_jobs" ADD CONSTRAINT "glue_jobs_take_mode_check"
            CHECK (take_mode IN ('initial','new-take'));
    END IF;
END $$;--> statement-breakpoint
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'glue_jobs_dispatch_state_check'
    ) THEN
        ALTER TABLE "glue_jobs" ADD CONSTRAINT "glue_jobs_dispatch_state_check" CHECK (
            dispatch_state IN ('pending','dispatched','failed','local'));
    END IF;
END $$;--> statement-breakpoint
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'glue_jobs_reservation_state_check'
    ) THEN
        ALTER TABLE "glue_jobs" ADD CONSTRAINT "glue_jobs_reservation_state_check"
            CHECK (paid_reservation_state IS NULL OR paid_reservation_state IN ('reserved','consumed','released'));
    END IF;
END $$;--> statement-breakpoint
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'glue_jobs_retry_kind_check'
    ) THEN
        ALTER TABLE "glue_jobs" ADD CONSTRAINT "glue_jobs_retry_kind_check"
            CHECK (retry_kind IS NULL OR retry_kind IN ('free','paid','none'));
    END IF;
END $$;--> statement-breakpoint
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'glue_takes_status_check'
    ) THEN
        ALTER TABLE "glue_takes" ADD CONSTRAINT "glue_takes_status_check"
            CHECK (status IN ('ready','failed'));
    END IF;
END $$;--> statement-breakpoint
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'glue_takes_ordinal_check'
    ) THEN
        ALTER TABLE "glue_takes" ADD CONSTRAINT "glue_takes_ordinal_check" CHECK (ordinal >= 1);
    END IF;
END $$;--> statement-breakpoint
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'glue_takes_ready_seconds_check'
    ) THEN
        ALTER TABLE "glue_takes" ADD CONSTRAINT "glue_takes_ready_seconds_check"
            CHECK (status <> 'ready' OR seconds > 0);
    END IF;
END $$;--> statement-breakpoint
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'glue_excerpts_side_check'
    ) THEN
        ALTER TABLE "glue_excerpts" ADD CONSTRAINT "glue_excerpts_side_check"
            CHECK (side IN ('A','B'));
    END IF;
END $$;--> statement-breakpoint
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'glue_excerpts_seconds_check'
    ) THEN
        ALTER TABLE "glue_excerpts" ADD CONSTRAINT "glue_excerpts_seconds_check"
            CHECK (seconds >= 1 AND seconds <= 12);
    END IF;
END $$;--> statement-breakpoint
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'glue_excerpts_phase_check'
    ) THEN
        ALTER TABLE "glue_excerpts" ADD CONSTRAINT "glue_excerpts_phase_check" CHECK (
            phase IN ('constructed','uploaded','rejected','failed'));
    END IF;
END $$;--> statement-breakpoint
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'glue_paid_attempts_state_check'
    ) THEN
        ALTER TABLE "glue_paid_attempts" ADD CONSTRAINT "glue_paid_attempts_state_check"
            CHECK (state IN ('reserved','consumed','released'));
    END IF;
END $$;