import "server-only";

import { and, asc, eq, gte, isNotNull } from "drizzle-orm";

import { db } from "@/db";
import { gluePaidAttempts } from "@/db/schema";

// Rolling 24-hour paid-attempt quota (spec:
// specs/inpaint-transition-elevenlabs-v2.md §3.1). This module is the
// read-only view used by the quote, the resolved junction payload, and later
// the transactional job claim (#316 adds the claim on top).

export const GLUE_DAILY_LIMIT = 5 as const;
const QUOTA_WINDOW_MS = 24 * 60 * 60 * 1000;

/**
 * QA account exempt from the rolling paid-transition quota so end-to-end
 * testing is never blocked. `tester@streamofworship.com` is the only QA user
 * (Better Auth id 15). This is deliberately a hardcoded server-side
 * allowlist, not a deployment knob.
 */
export const GLUE_QUOTA_EXEMPT_USER_IDS: ReadonlySet<number> = new Set([15]);

export function isGlueQuotaExempt(userId: number): boolean {
  return GLUE_QUOTA_EXEMPT_USER_IDS.has(userId);
}

export interface GlueQuota {
  limit: typeof GLUE_DAILY_LIMIT;
  used: number;
  remaining: number;
  /** Non-null only when the window is full (`used >= limit`). */
  resetAt: string | null;
}

/** Pure quota summary from the consumed-attempt timestamps in the window. */
export function buildGlueQuota(consumedAt: readonly Date[], now: Date = new Date()): GlueQuota {
  const cutoff = now.getTime() - QUOTA_WINDOW_MS;
  const relevant = consumedAt
    .filter((value) => value.getTime() > cutoff && value.getTime() <= now.getTime())
    .sort((left, right) => left.getTime() - right.getTime());
  const used = relevant.length;
  return {
    limit: GLUE_DAILY_LIMIT,
    used,
    remaining: Math.max(0, GLUE_DAILY_LIMIT - used),
    // Only a full window has a meaningful "paid jobs return" time.
    resetAt: used >= GLUE_DAILY_LIMIT
      ? new Date(relevant[0].getTime() + QUOTA_WINDOW_MS).toISOString()
      : null,
  };
}

/** Reads the consumed `glue_paid_attempts` ledger for the rolling 24 hours. */
export async function readGlueQuota(userId: number, now: Date = new Date()): Promise<GlueQuota> {
  // The exempt account never reads the ledger, so its window is always open.
  if (isGlueQuotaExempt(userId)) return buildGlueQuota([], now);
  const cutoff = new Date(now.getTime() - QUOTA_WINDOW_MS);
  const rows = await db
    .select({ consumedAt: gluePaidAttempts.consumedAt })
    .from(gluePaidAttempts)
    .where(
      and(
        eq(gluePaidAttempts.userId, userId),
        eq(gluePaidAttempts.state, "consumed"),
        isNotNull(gluePaidAttempts.consumedAt),
        gte(gluePaidAttempts.consumedAt, cutoff)
      )
    )
    .orderBy(asc(gluePaidAttempts.consumedAt));

  const consumedAt = rows
    .map((row) => row.consumedAt)
    .filter((value): value is Date => value instanceof Date);
  return buildGlueQuota(consumedAt, now);
}
