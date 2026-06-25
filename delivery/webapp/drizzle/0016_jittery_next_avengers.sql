-- Client error telemetry table (best-effort, anonymized). Rows are appended
-- by /api/log-client-error from Cast/Presentation transport failures. PII is
-- redacted before persistence (hashed IP, no user IDs, signed URLs reduced
-- to host+path+expiry age). See src/db/schema.ts `clientErrorLog`.
CREATE TABLE IF NOT EXISTS "client_error_log" (
	"id" serial PRIMARY KEY NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"ip_hash" text NOT NULL,
	"message" text NOT NULL,
	"kind" text NOT NULL,
	"meta_json" text
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "idx_client_error_log_created" ON "client_error_log" USING btree ("created_at");
