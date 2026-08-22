DO $$ BEGIN
    ALTER TABLE "recordings" ADD COLUMN IF NOT EXISTS "theme" text;
    ALTER TABLE "recordings" ADD COLUMN IF NOT EXISTS "vocal_posture" text;
END $$;
--> statement-breakpoint
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'recordings_theme_check'
    ) THEN
        ALTER TABLE "recordings" ADD CONSTRAINT "recordings_theme_check"
            CHECK (theme IN ('讚美','感恩','敬拜','奉獻','認罪','差遣','信心','祈禱','復興','聖靈','十字架','跟隨') OR theme IS NULL);
    END IF;
END $$;
--> statement-breakpoint
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'recordings_vocal_posture_check'
    ) THEN
        ALTER TABLE "recordings" ADD CONSTRAINT "recordings_vocal_posture_check"
            CHECK (vocal_posture IN ('To God','About God','To Congregation') OR vocal_posture IS NULL);
    END IF;
END $$;
