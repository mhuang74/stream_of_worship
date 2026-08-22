ALTER TABLE "recordings" ADD COLUMN "theme" text;
ALTER TABLE "recordings" ADD COLUMN "vocal_posture" text;
--> statement-breakpoint
ALTER TABLE "recordings" ADD CONSTRAINT "recordings_theme_check"
    CHECK (theme IN ('讚美','感恩','敬拜','奉獻','認罪','差遣','信心','祈禱','復興','聖靈','十字架','跟隨') OR theme IS NULL);
ALTER TABLE "recordings" ADD CONSTRAINT "recordings_vocal_posture_check"
    CHECK (vocal_posture IN ('To God','About God','To Congregation') OR vocal_posture IS NULL);
