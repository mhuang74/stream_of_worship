-- Migration: 0023_backfill_email_verified
-- Description: Mark all pre-existing users as email-verified
-- See specs/webapp-email-validation-password-reset-v1.md
--
-- Rationale: existing users already proved ownership by using the app;
-- enforcing email verification must not lock them out. drizzle-kit cannot
-- express "set all rows to true" cleanly, so this is hand-written (same
-- precedent as 0018_theme_anchors.sql).

UPDATE "user" SET "emailVerified" = true WHERE "emailVerified" = false;
