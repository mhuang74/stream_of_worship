ALTER TABLE "glue_paid_attempts" DROP CONSTRAINT "glue_paid_attempts_glue_job_id_glue_jobs_id_fk";
--> statement-breakpoint
ALTER TABLE "glue_paid_attempts" ALTER COLUMN "glue_job_id" DROP NOT NULL;--> statement-breakpoint
ALTER TABLE "glue_paid_attempts" ADD CONSTRAINT "glue_paid_attempts_glue_job_id_glue_jobs_id_fk" FOREIGN KEY ("glue_job_id") REFERENCES "public"."glue_jobs"("id") ON DELETE set null ON UPDATE no action;