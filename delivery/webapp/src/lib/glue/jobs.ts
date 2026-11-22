import "server-only";

import { randomBytes, randomUUID } from "node:crypto";

import { and, asc, desc, eq, gte, gt, isNotNull, ne, sql } from "drizzle-orm";

import { db } from "@/db";
import { mutationDb } from "@/db/mutation-db";
import {
  glueJobs,
  gluePaidAttempts,
  glueTakes,
  recordings,
  songsetItems,
  songsets,
  users,
} from "@/db/schema";
import { parseRecordingBeats, parseRecordingSections } from "@/lib/arrangements";
import { isElevenLabsConfigured } from "@/lib/glue/elevenlabs-config";
import {
  GlueJobRequestError,
  sanitizeGlueError,
} from "@/lib/glue/errors";
import { logGlueError, logGlueEvent } from "@/lib/glue/log";
import { dispatchGlueMessage } from "@/lib/glue/dispatch";
import {
  buildGlueQuota,
  readGlueQuota,
  type GlueQuota,
} from "@/lib/glue/quota";
import {
  deriveGlueJunction,
  loadGlueItemRows,
  type GlueJunctionReady,
  type GlueResolverItem,
} from "@/lib/glue/quote";
import { loadResolvedGlueJunctions } from "@/lib/glue/resolve";
import { selectGlueTake } from "@/lib/db/songsets";

// Durable Glue job spine (spec: specs/inpaint-transition-elevenlabs-v2.md §2.3,
// §2.4, §2.5, §3.1–§3.3). The browser talks only to this module's callers; all
// quota, identity, and state transitions are decided server-side.
//
// The transactional seam is `GlueJobRepository`: every multi-row invariant
// (quota claim, one-active insert, release/cancel, recovery) is one serializable
// transaction there. Orchestration above it is pure decision-making, which is
// what the tests drive with an in-memory repository.

export const GLUE_ACTIVE_PHASES = [
  "queued",
  "preparing",
  "uploading",
  "composing",
  "storing-response",
  "staging",
] as const;

export const GLUE_TERMINAL_PHASES = [
  "ready",
  "failed",
  "cancelled",
  "dispatch-failed",
] as const;

export type GlueJobPhase = (typeof GLUE_ACTIVE_PHASES)[number] | (typeof GLUE_TERMINAL_PHASES)[number];
export type GlueTakeMode = "initial" | "new-take";
export type GlueRetryKind = "free" | "paid" | "none";

const ACTIVE_PHASE_LIST = [...GLUE_ACTIVE_PHASES] as string[];

export function isGlueJobTerminalPhase(phase: string): boolean {
  return (GLUE_TERMINAL_PHASES as readonly string[]).includes(phase);
}

/** The wire shape shared by `client.ts`, every route, and the editor. */
export interface GlueJobStatus {
  id: string;
  songsetId: string;
  fromItemId: string;
  toItemId: string;
  phase: string;
  active: boolean;
  takeMode: GlueTakeMode;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  elapsedSeconds: number;
  attemptId: string;
  providerContacted: boolean;
  selectedTakeId: string | null;
  resultingTakeId: string | null;
  errorCode: string | null;
  errorDetail: string | null;
  retryKind: GlueRetryKind | null;
  quota: GlueQuota;
}

export interface ActiveGlueJobsPayload {
  active: GlueJobStatus[];
  latestByFromItemId: Record<string, GlueJobStatus>;
}

export type GlueJobRecord = typeof glueJobs.$inferSelect;

function iso(value: Date | string | null): string | null {
  if (value === null) return null;
  return value instanceof Date ? value.toISOString() : value;
}

/** Integer elapsed seconds; never a fraction and never a percentage. */
export function glueElapsedSeconds(
  row: Pick<GlueJobRecord, "createdAt" | "startedAt" | "finishedAt">,
  now: Date
): number {
  const start = row.startedAt ?? row.createdAt;
  const end = row.finishedAt ?? now;
  return Math.max(0, Math.round((end.getTime() - start.getTime()) / 1000));
}

export function toGlueJobStatus(
  row: GlueJobRecord,
  quota: GlueQuota,
  now: Date = new Date()
): GlueJobStatus {
  return {
    id: row.id,
    songsetId: row.songsetId,
    fromItemId: row.fromItemId,
    toItemId: row.toItemId,
    phase: row.phase,
    active: row.active,
    takeMode: row.takeMode === "new-take" ? "new-take" : "initial",
    createdAt: iso(row.createdAt) as string,
    startedAt: iso(row.startedAt),
    finishedAt: iso(row.finishedAt),
    elapsedSeconds: glueElapsedSeconds(row, now),
    attemptId: row.attemptId,
    providerContacted: row.providerContacted,
    selectedTakeId: row.activeTakeId,
    resultingTakeId: row.resultingTakeId,
    errorCode: row.errorCode,
    errorDetail: sanitizeGlueError(row.errorDetail) ?? null,
    retryKind:
      row.retryKind === "free" || row.retryKind === "paid" || row.retryKind === "none"
        ? row.retryKind
        : null,
    quota: {
      limit: quota.limit,
      used: quota.used,
      remaining: quota.remaining,
      resetAt: quota.resetAt,
    },
  };
}

/** Latest job per Junction for discovery after a reload or reconnect. */
export function buildActiveGlueJobsPayload(
  rows: readonly GlueJobRecord[],
  quota: GlueQuota,
  now: Date = new Date()
): ActiveGlueJobsPayload {
  const latestByFromItemId: Record<string, GlueJobStatus> = {};
  for (const row of rows) {
    if (latestByFromItemId[row.fromItemId] !== undefined) continue;
    latestByFromItemId[row.fromItemId] = toGlueJobStatus(row, quota, now);
  }
  const active = rows
    .filter((row) => row.active)
    .map((row) => toGlueJobStatus(row, quota, now));
  return { active, latestByFromItemId };
}

// ---------------------------------------------------------------------------
// Transactional repository
// ---------------------------------------------------------------------------

export type CreateJobAtomicResult =
  | { kind: "created"; job: GlueJobRecord }
  | { kind: "duplicate"; job: GlueJobRecord }
  | { kind: "active-job"; jobId: string; fromItemId: string }
  | { kind: "quota"; resetAt: string | null }
  | { kind: "inputs-changed" };

export interface CreateJobAtomicInput {
  userId: number;
  songsetId: string;
  fromItemId: string;
  toItemId: string;
  clientRequestId: string;
  takeMode: GlueTakeMode;
  descriptorKey: string;
  descriptorJson: string;
  expectedFromUpdatedAt: Date;
  expectedToUpdatedAt: Date;
  /** Fresh identity allocated by the orchestrator. */
  jobId: string;
  attemptId: string;
  pendingTakeId: string;
  reservationId: string;
  seed: number;
  now: Date;
}

export type CancelJobResult =
  | { kind: "cancelled"; job: GlueJobRecord }
  | { kind: "already-cancelled" }
  | { kind: "provider-contacted" }
  | { kind: "terminal"; phase: string }
  | { kind: "not-found" };

export type RedispatchResult =
  | { kind: "prepared"; job: GlueJobRecord }
  | { kind: "not-found" }
  | { kind: "provider-contacted" }
  | { kind: "not-redispatchable"; phase: string };

export type RetryProcessingResult =
  | { kind: "prepared"; job: GlueJobRecord }
  | { kind: "already-ready"; job: GlueJobRecord }
  | { kind: "not-found" }
  | { kind: "no-raw-response" }
  | { kind: "not-retryable"; phase: string };

export type ResumeResult =
  | { kind: "prepared"; job: GlueJobRecord; phase: "queued" | "staging" }
  | { kind: "not-found" }
  | { kind: "not-resumable"; phase: string }
  | { kind: "requires-paid-retry" };

export interface GlueJobRepository {
  findJobByClientRequestId(userId: number, clientRequestId: string): Promise<GlueJobRecord | null>;
  findJob(jobId: string, userId: number): Promise<GlueJobRecord | null>;
  listJobsForSongset(songsetId: string, userId: number): Promise<GlueJobRecord[]>;
  selectNewestReadyTakeId(userId: number, descriptorKey: string): Promise<string | null>;
  createJobAtomically(input: CreateJobAtomicInput): Promise<CreateJobAtomicResult>;
  markDispatchFailed(jobId: string, userId: number, detail: string | undefined): Promise<GlueJobRecord | null>;
  cancelJob(jobId: string, userId: number): Promise<CancelJobResult>;
  prepareRedispatch(jobId: string, userId: number, attemptId: string, now: Date): Promise<RedispatchResult>;
  prepareRetryProcessing(jobId: string, userId: number, attemptId: string, now: Date): Promise<RetryProcessingResult>;
  prepareResume(
    jobId: string,
    userId: number,
    attemptId: string,
    now: Date
  ): Promise<ResumeResult>;
  markRetryDispatchFailed(jobId: string, userId: number, detail: string | undefined, now: Date): Promise<void>;
}

/** Constraint names the create insert may race with (contract §2.3 step 7). */
export function classifyCreateConstraintError(
  error: unknown
): "duplicate-request" | "active-job" | null {
  const fields: string[] = [];
  if (error instanceof Error) fields.push(error.message);
  if (typeof error === "object" && error !== null) {
    const record = error as Record<string, unknown>;
    for (const key of ["constraint", "detail", "message"]) {
      const value = record[key];
      if (typeof value === "string") fields.push(value);
    }
    const cause = record.cause;
    if (cause instanceof Error) fields.push(cause.message);
  }
  const text = fields.join(" ");
  if (text.includes("uq_glue_jobs_active_per_user")) return "active-job";
  if (
    text.includes("glue_jobs_user_id_client_request_id_unique") ||
    text.includes("glue_jobs_user_id_client_request_id_key")
  ) {
    return "duplicate-request";
  }
  return null;
}

/** Pure rolling-24h quota claim used inside the create transaction. */
export function evaluateQuotaClaim(
  consumedAt: readonly Date[],
  now: Date
): { allowed: true } | { allowed: false; resetAt: string | null } {
  const quota = buildGlueQuota(consumedAt, now);
  return quota.remaining > 0 ? { allowed: true } : { allowed: false, resetAt: quota.resetAt };
}

/** Next per-user/descriptor take ordinal: `max(glue_takes, glue_jobs) + 1`. */
export function nextTakeOrdinal(
  maxTakeOrdinal: number | null,
  maxJobOrdinal: number | null
): number {
  const highest = Math.max(maxTakeOrdinal ?? 0, maxJobOrdinal ?? 0);
  return highest + 1;
}

/** A durable raw response exists (key, checksum, and byte count together). */
export function hasGlueRawResponse(job: {
  rawResponseR2Key: string | null;
  rawResponseSha256: string | null;
  rawResponseBytes: number | null;
}): boolean {
  return (
    job.rawResponseR2Key !== null &&
    job.rawResponseSha256 !== null &&
    job.rawResponseBytes !== null
  );
}

export type GlueResumeEligibility =
  | { kind: "resumable"; phase: "queued" | "staging" }
  | { kind: "not-resumable"; phase: string }
  | { kind: "requires-paid-retry" };

/**
 * Resume is a free replay of the same job identity (spec §2.5). The durable
 * `retry_kind` is the authority: the worker persists `free` only for outcomes
 * where no provider request can have taken effect (pre-contact failures, a
 * refused-before-send call, exhausted definitely-unsent retries, or a compose
 * that was never sent after a persisted upload). Ambiguous outcomes persist
 * `paid`/`none` and must never be replayed without a new paid request — even
 * when a raw response exists. A durable raw response is always finished by
 * staging (`staging`); everything else re-enters generation (`queued`), where
 * the worker reuses excerpts whose provider song ids were already persisted.
 */
export function evaluateGlueResume(job: {
  phase: string;
  retryKind: string | null;
  rawResponseR2Key: string | null;
  rawResponseSha256: string | null;
  rawResponseBytes: number | null;
}): GlueResumeEligibility {
  if (job.phase !== "failed" && job.phase !== "dispatch-failed") {
    return { kind: "not-resumable", phase: job.phase };
  }
  if (job.retryKind !== "free") return { kind: "requires-paid-retry" };
  return {
    kind: "resumable",
    phase: hasGlueRawResponse(job) ? "staging" : "queued",
  };
}

/** Cryptographically random 32-bit seed (spec §2.2). */
export function randomGlueSeed(): number {
  return randomBytes(4).readUInt32BE(0);
}

function buildResolverItem(row: {
  itemId: string;
  position: number;
  userId: number;
  glueEnabled: boolean;
  glueBeats: number;
  glueActiveTakeId: string | null;
  updatedAt: Date;
  arrangementSteps: string | null;
  contentHash: string | null;
  durationSeconds: number | null;
  tempoBpm: number | null;
  sections: string | null;
  beats: string | null;
  recordingDeletedAt: Date | null;
}): GlueResolverItem {
  return {
    itemId: row.itemId,
    position: row.position,
    userId: row.userId,
    glueEnabled: row.glueEnabled,
    glueBeats: row.glueBeats === 2 || row.glueBeats === 4 || row.glueBeats === 8 || row.glueBeats === 16
      ? row.glueBeats
      : 4,
    glueActiveTakeId: row.glueActiveTakeId,
    updatedAt: row.updatedAt.toISOString(),
    arrangementSteps: row.arrangementSteps,
    recording:
      row.contentHash === null || row.recordingDeletedAt !== null
        ? null
        : {
            contentHash: row.contentHash,
            durationSeconds: row.durationSeconds,
            tempoBpm: row.tempoBpm,
            sections: parseRecordingSections(row.sections),
            beats: parseRecordingBeats(row.beats),
          },
  };
}

function releaseReservationIfReservedSql() {
  // Same-statement release so the terminal phase and the released reservation
  // can never disagree (DB active-phase CHECK).
  return sql`CASE WHEN ${glueJobs.paidReservationState} = 'reserved' THEN 'released' ELSE ${glueJobs.paidReservationState} END`;
}

export const glueJobRepository: GlueJobRepository = {
  async findJobByClientRequestId(userId, clientRequestId) {
    const [row] = await db
      .select()
      .from(glueJobs)
      .where(and(eq(glueJobs.userId, userId), eq(glueJobs.clientRequestId, clientRequestId)))
      .limit(1);
    return row ?? null;
  },

  async findJob(jobId, userId) {
    const [row] = await db
      .select()
      .from(glueJobs)
      .where(and(eq(glueJobs.id, jobId), eq(glueJobs.userId, userId)))
      .limit(1);
    return row ?? null;
  },

  async listJobsForSongset(songsetId, userId) {
    return db
      .select()
      .from(glueJobs)
      .where(and(eq(glueJobs.songsetId, songsetId), eq(glueJobs.userId, userId)))
      .orderBy(desc(glueJobs.createdAt));
  },

  async selectNewestReadyTakeId(userId, descriptorKey) {
    const [row] = await db
      .select({ id: glueTakes.id })
      .from(glueTakes)
      .where(
        and(
          eq(glueTakes.userId, userId),
          eq(glueTakes.descriptorKey, descriptorKey),
          eq(glueTakes.status, "ready"),
          isNotNull(glueTakes.r2Key),
          gt(glueTakes.seconds, 0)
        )
      )
      .orderBy(desc(glueTakes.createdAt))
      .limit(1);
    return row?.id ?? null;
  },

  async createJobAtomically(input) {
    try {
      return await mutationDb.transaction(
        async (tx) => {
          // One-active-job is the partial unique index; quota is the parent row
          // lock. Both happen before any insert.
          const [owner] = await tx
            .select({ id: users.id })
            .from(users)
            .where(eq(users.id, input.userId))
            .for("update");
          if (!owner) return { kind: "inputs-changed" } as const;

          const cutoff = new Date(input.now.getTime() - 24 * 60 * 60 * 1000);
          const consumed = await tx
            .select({ consumedAt: gluePaidAttempts.consumedAt })
            .from(gluePaidAttempts)
            .where(
              and(
                eq(gluePaidAttempts.userId, input.userId),
                eq(gluePaidAttempts.state, "consumed"),
                isNotNull(gluePaidAttempts.consumedAt),
                gte(gluePaidAttempts.consumedAt, cutoff)
              )
            );
          const claim = evaluateQuotaClaim(
            consumed
              .map((row) => row.consumedAt)
              .filter((value): value is Date => value instanceof Date),
            input.now
          );
          if (!claim.allowed) {
            return { kind: "quota", resetAt: claim.resetAt } as const;
          }

          // Re-read the item snapshot and recompute the descriptor inside the
          // transaction; a racing save must not slip between admission and
          // reservation.
          const snapshotRows = await tx
            .select({
              itemId: songsetItems.id,
              position: songsetItems.position,
              userId: songsets.userId,
              glueEnabled: songsetItems.glueEnabled,
              glueBeats: songsetItems.glueBeats,
              glueActiveTakeId: songsetItems.glueActiveTakeId,
              updatedAt: songsetItems.updatedAt,
              arrangementSteps: songsetItems.arrangementSteps,
              contentHash: recordings.contentHash,
              durationSeconds: recordings.durationSeconds,
              tempoBpm: recordings.tempoBpm,
              sections: recordings.sections,
              beats: recordings.beats,
              recordingDeletedAt: recordings.deletedAt,
            })
            .from(songsetItems)
            .innerJoin(songsets, eq(songsets.id, songsetItems.songsetId))
            .leftJoin(
              recordings,
              eq(recordings.hashPrefix, songsetItems.recordingHashPrefix)
            )
            .where(
              and(
                eq(songsetItems.songsetId, input.songsetId),
                eq(songsets.userId, input.userId)
              )
            )
            .orderBy(asc(songsetItems.position));
          const items = snapshotRows
            .filter((row) => row.recordingDeletedAt === null)
            .map((row) => buildResolverItem(row));
          const index = items.findIndex((item) => item.itemId === input.fromItemId);
          if (index < 0 || items[index + 1]?.itemId !== input.toItemId) {
            return { kind: "inputs-changed" } as const;
          }
          const from = items[index];
          const to = items[index + 1];
          if (
            from.updatedAt !== input.expectedFromUpdatedAt.toISOString() ||
            to.updatedAt !== input.expectedToUpdatedAt.toISOString()
          ) {
            return { kind: "inputs-changed" } as const;
          }
          const derivation = deriveGlueJunction(from, to);
          if (
            !derivation.ok ||
            derivation.descriptorKey !== input.descriptorKey ||
            derivation.descriptorJson !== input.descriptorJson
          ) {
            return { kind: "inputs-changed" } as const;
          }

          const [takeMax] = await tx
            .select({ value: sql<number | null>`max(${glueTakes.ordinal})` })
            .from(glueTakes)
            .where(
              and(
                eq(glueTakes.userId, input.userId),
                eq(glueTakes.descriptorKey, input.descriptorKey)
              )
            );
          const [jobMax] = await tx
            .select({ value: sql<number | null>`max(${glueJobs.takeOrdinal})` })
            .from(glueJobs)
            .where(
              and(
                eq(glueJobs.userId, input.userId),
                eq(glueJobs.descriptorKey, input.descriptorKey)
              )
            );
          const ordinal = nextTakeOrdinal(
            takeMax?.value == null ? null : Number(takeMax.value),
            jobMax?.value == null ? null : Number(jobMax.value)
          );

          const [job] = await tx
            .insert(glueJobs)
            .values({
              id: input.jobId,
              userId: input.userId,
              songsetId: input.songsetId,
              fromItemId: input.fromItemId,
              toItemId: input.toItemId,
              clientRequestId: input.clientRequestId,
              takeMode: input.takeMode,
              descriptorKey: input.descriptorKey,
              descriptorJson: input.descriptorJson,
              expectedFromUpdatedAt: input.expectedFromUpdatedAt,
              expectedToUpdatedAt: input.expectedToUpdatedAt,
              phase: "queued",
              active: true,
              dispatchState: "pending",
              dispatchAttempt: 0,
              attemptId: input.attemptId,
              pendingTakeId: input.pendingTakeId,
              takeOrdinal: ordinal,
              seed: input.seed,
              paidReservationId: input.reservationId,
              paidReservationState: "reserved",
              providerContacted: false,
              createdAt: input.now,
              updatedAt: input.now,
            })
            .returning();

          await tx.insert(gluePaidAttempts).values({
            id: input.reservationId,
            userId: input.userId,
            glueJobId: input.jobId,
            state: "reserved",
            createdAt: input.now,
          });

          return { kind: "created", job } as const;
        },
        { isolationLevel: "serializable" }
      );
    } catch (error) {
      const classification = classifyCreateConstraintError(error);
      if (classification === "duplicate-request") {
        const existing = await glueJobRepository.findJobByClientRequestId(
          input.userId,
          input.clientRequestId
        );
        if (existing) return { kind: "duplicate", job: existing };
      }
      if (classification === "active-job") {
        const [active] = await db
          .select({ id: glueJobs.id, fromItemId: glueJobs.fromItemId })
          .from(glueJobs)
          .where(and(eq(glueJobs.userId, input.userId), eq(glueJobs.active, true)))
          .limit(1);
        if (active) {
          return { kind: "active-job", jobId: active.id, fromItemId: active.fromItemId };
        }
      }
      throw error;
    }
  },

  async markDispatchFailed(jobId, userId, detail) {
    return mutationDb.transaction(async (tx) => {
      const now = new Date();
      const [job] = await tx
        .update(glueJobs)
        .set({
          phase: "dispatch-failed",
          active: false,
          dispatchState: "failed",
          paidReservationState: releaseReservationIfReservedSql(),
          errorCode: "GLUE_DISPATCH_FAILED",
          errorDetail: sanitizeGlueError(detail) ?? null,
          finishedAt: now,
          updatedAt: now,
        })
        .where(
          and(
            eq(glueJobs.id, jobId),
            eq(glueJobs.userId, userId),
            // A job a worker already claimed must never be reset by a late
            // HTTP-retry dispatch failure.
            eq(glueJobs.phase, "queued"),
            eq(glueJobs.dispatchState, "pending"),
            eq(glueJobs.providerContacted, false)
          )
        )
        .returning();
      if (!job) return null;
      await tx
        .update(gluePaidAttempts)
        .set({ state: "released", releasedAt: now })
        .where(
          and(
            eq(gluePaidAttempts.glueJobId, jobId),
            eq(gluePaidAttempts.userId, userId),
            eq(gluePaidAttempts.state, "reserved")
          )
        );
      return job;
    });
  },

  async cancelJob(jobId, userId) {
    return mutationDb.transaction(async (tx) => {
      const now = new Date();
      // One conditional statement: cancel loses to provider contact and to any
      // terminal phase, and releasing the reservation is part of the same
      // terminal write (DB active-phase CHECK).
      const [cancelled] = await tx
        .update(glueJobs)
        .set({
          phase: "cancelled",
          active: false,
          paidReservationState: releaseReservationIfReservedSql(),
          finishedAt: now,
          updatedAt: now,
        })
        .where(
          and(
            eq(glueJobs.id, jobId),
            eq(glueJobs.userId, userId),
            eq(glueJobs.active, true),
            eq(glueJobs.providerContacted, false),
            ne(glueJobs.phase, "cancelled")
          )
        )
        .returning();

      if (cancelled) {
        await tx
          .update(gluePaidAttempts)
          .set({ state: "released", releasedAt: now })
          .where(
            and(
              eq(gluePaidAttempts.glueJobId, jobId),
              eq(gluePaidAttempts.userId, userId),
              eq(gluePaidAttempts.state, "reserved")
            )
          );
        return { kind: "cancelled", job: cancelled };
      }

      const [current] = await tx
        .select()
        .from(glueJobs)
        .where(and(eq(glueJobs.id, jobId), eq(glueJobs.userId, userId)))
        .limit(1);
      if (!current) return { kind: "not-found" };
      if (current.providerContacted) return { kind: "provider-contacted" };
      if (current.phase === "cancelled") return { kind: "already-cancelled" };
      return { kind: "terminal", phase: current.phase };
    });
  },

  async prepareRedispatch(jobId, userId, attemptId, now) {
    return mutationDb.transaction(async (tx) => {
      const [job] = await tx
        .select()
        .from(glueJobs)
        .where(and(eq(glueJobs.id, jobId), eq(glueJobs.userId, userId)))
        .limit(1);
      if (!job) return { kind: "not-found" };
      if (job.providerContacted) return { kind: "provider-contacted" };
      if (job.phase !== "dispatch-failed") {
        return { kind: "not-redispatchable", phase: job.phase };
      }
      if (
        job.paidReservationState !== "reserved" &&
        job.paidReservationState !== "released"
      ) {
        return { kind: "not-redispatchable", phase: job.phase };
      }

      // A released reservation goes back to `reserved` without a new ledger
      // row: redispatch is free because the attempt was never consumed.
      const [updated] = await tx
        .update(glueJobs)
        .set({
          phase: "queued",
          active: true,
          attemptId,
          dispatchState: "pending",
          dispatchAttempt: sql`${glueJobs.dispatchAttempt} + 1`,
          paidReservationState: "reserved",
          errorCode: null,
          errorDetail: null,
          retryKind: null,
          finishedAt: null,
          updatedAt: now,
        })
        .where(
          and(
            eq(glueJobs.id, jobId),
            eq(glueJobs.userId, userId),
            eq(glueJobs.phase, "dispatch-failed")
          )
        )
        .returning();
      if (!updated) return { kind: "not-redispatchable", phase: job.phase };
      return { kind: "prepared", job: updated };
    });
  },

  async prepareRetryProcessing(jobId, userId, attemptId, now) {
    return mutationDb.transaction(async (tx) => {
      const [job] = await tx
        .select()
        .from(glueJobs)
        .where(and(eq(glueJobs.id, jobId), eq(glueJobs.userId, userId)))
        .limit(1);
      if (!job) return { kind: "not-found" };
      if (job.phase === "ready") return { kind: "already-ready", job };
      const hasRawResponse =
        job.rawResponseR2Key !== null &&
        job.rawResponseBytes !== null &&
        job.rawResponseSha256 !== null;
      if (!hasRawResponse) return { kind: "no-raw-response" };
      if (job.phase === "cancelled") return { kind: "not-retryable", phase: job.phase };

      const [updated] = await tx
        .update(glueJobs)
        .set({
          phase: "staging",
          active: true,
          attemptId,
          dispatchState: "pending",
          dispatchAttempt: sql`${glueJobs.dispatchAttempt} + 1`,
          errorCode: null,
          errorDetail: null,
          retryKind: "free",
          finishedAt: null,
          updatedAt: now,
        })
        .where(
          and(
            eq(glueJobs.id, jobId),
            eq(glueJobs.userId, userId),
            ne(glueJobs.phase, "ready"),
            ne(glueJobs.phase, "cancelled")
          )
        )
        .returning();
      if (!updated) return { kind: "not-retryable", phase: job.phase };
      return { kind: "prepared", job: updated };
    });
  },

  async prepareResume(jobId, userId, attemptId, now) {
    return mutationDb.transaction(async (tx) => {
      const [job] = await tx
        .select()
        .from(glueJobs)
        .where(and(eq(glueJobs.id, jobId), eq(glueJobs.userId, userId)))
        .limit(1);
      if (!job) return { kind: "not-found" };
      const eligibility = evaluateGlueResume(job);
      if (eligibility.kind === "not-resumable") {
        return { kind: "not-resumable", phase: eligibility.phase };
      }
      if (eligibility.kind === "requires-paid-retry") {
        return { kind: "requires-paid-retry" };
      }
      const phase = eligibility.phase;

      const [updated] = await tx
        .update(glueJobs)
        .set({
          phase,
          active: true,
          attemptId,
          dispatchState: "pending",
          dispatchAttempt: sql`${glueJobs.dispatchAttempt} + 1`,
          errorCode: null,
          errorDetail: null,
          retryKind: "free",
          finishedAt: null,
          updatedAt: now,
        })
        .where(
          and(
            eq(glueJobs.id, jobId),
            eq(glueJobs.userId, userId),
            eq(glueJobs.retryKind, "free")
          )
        )
        .returning();
      if (!updated) return { kind: "not-resumable", phase: job.phase };
      return { kind: "prepared", job: updated, phase };
    });
  },

  async markRetryDispatchFailed(jobId, userId, detail, now) {
    await mutationDb.transaction(async (tx) => {
      await tx
        .update(glueJobs)
        .set({
          phase: "failed",
          active: false,
          dispatchState: "failed",
          errorCode: "GLUE_DISPATCH_FAILED",
          errorDetail: sanitizeGlueError(detail) ?? null,
          finishedAt: now,
          updatedAt: now,
        })
        .where(and(eq(glueJobs.id, jobId), eq(glueJobs.userId, userId)));
    });
  },
};

// ---------------------------------------------------------------------------
// Orchestration
// ---------------------------------------------------------------------------

export type GlueDispatch = typeof dispatchGlueMessage;

export interface GlueJobDeps {
  repository: GlueJobRepository;
  loadItems: typeof loadGlueItemRows;
  loadJunctions: typeof loadResolvedGlueJunctions;
  selectTake: typeof selectGlueTake;
  isConfigured: typeof isElevenLabsConfigured;
  dispatch: GlueDispatch;
  readQuota: typeof readGlueQuota;
  now: () => Date;
  randomId: () => string;
  randomSeed: () => number;
}

export function createGlueJobDeps(overrides: Partial<GlueJobDeps> = {}): GlueJobDeps {
  return {
    repository: glueJobRepository,
    loadItems: loadGlueItemRows,
    loadJunctions: loadResolvedGlueJunctions,
    selectTake: selectGlueTake,
    isConfigured: isElevenLabsConfigured,
    dispatch: dispatchGlueMessage,
    readQuota: readGlueQuota,
    now: () => new Date(),
    randomId: () => randomUUID(),
    randomSeed: randomGlueSeed,
    ...overrides,
  };
}

export interface CreateGlueJobInput {
  songsetId: string;
  userId: number;
  fromItemId: string;
  clientRequestId: string;
  expectedFromUpdatedAt: string;
  expectedToUpdatedAt: string;
  takeMode: GlueTakeMode;
}

export type CreateGlueJobOutcome =
  | { kind: "ready"; takeId: string; cached: true }
  | { kind: "job"; job: GlueJobStatus };

const MAX_GENERATION_SECONDS = 120;
const MIN_EXCERPT_SECONDS = 1;

function requireJunctionForCreate(
  from: GlueResolverItem,
  to: GlueResolverItem | null
): GlueJunctionReady {
  if (to === null) {
    throw new GlueJobRequestError(
      "GLUE_NOT_READY",
      "The last Song has no next Song to transition into."
    );
  }
  if (!from.glueEnabled) {
    throw new GlueJobRequestError("GLUE_NOT_READY", "AI transition is not enabled.");
  }
  const derivation = deriveGlueJunction(from, to);
  if (!derivation.ok) {
    throw new GlueJobRequestError(
      "GLUE_NOT_READY",
      `The AI transition inputs are not ready (${derivation.reason}).`
    );
  }
  if (
    derivation.fromSide.excerptSeconds < MIN_EXCERPT_SECONDS ||
    derivation.toSide.excerptSeconds < MIN_EXCERPT_SECONDS
  ) {
    throw new GlueJobRequestError(
      "GLUE_EXCERPT_TOO_SHORT",
      "Both sides of the junction must be at least 1 second."
    );
  }
  if (derivation.generationSeconds > MAX_GENERATION_SECONDS) {
    throw new GlueJobRequestError(
      "GLUE_DURATION_TOO_LONG",
      "AI transition would be longer than 120 seconds."
    );
  }
  return derivation;
}

export async function createGlueJob(
  input: CreateGlueJobInput,
  deps: GlueJobDeps = createGlueJobDeps()
): Promise<CreateGlueJobOutcome> {
  // Step 1: idempotency before any free resolution, selection, or dispatch.
  const existing = await deps.repository.findJobByClientRequestId(
    input.userId,
    input.clientRequestId
  );
  if (existing) {
    if (existing.phase === "dispatch-failed") {
      const redispatched = await redispatchGlueJob({ jobId: existing.id, userId: input.userId }, deps);
      return { kind: "job", job: redispatched };
    }
    if (existing.phase === "ready" && existing.resultingTakeId !== null) {
      return { kind: "ready", takeId: existing.resultingTakeId, cached: true };
    }
    return { kind: "job", job: await statusOf(existing, deps) };
  }

  const items = (await deps.loadItems([input.songsetId], input.userId)).get(input.songsetId) ?? [];
  const index = items.findIndex((item) => item.itemId === input.fromItemId);
  if (index < 0) {
    throw new GlueJobRequestError("GLUE_NOT_FOUND", "Songset item not found.");
  }
  const from = items[index];
  const to = items[index + 1] ?? null;
  // Step 2: saved-input validation, in the contract's order.
  const derivation = requireJunctionForCreate(from, to);
  if (to === null) {
    // Unreachable (requireJunctionForCreate throws), but keeps `to` narrowed.
    throw new GlueJobRequestError("GLUE_NOT_READY", "No successor.");
  }
  if (
    from.glueBeats !== 2 &&
    from.glueBeats !== 4 &&
    from.glueBeats !== 8 &&
    from.glueBeats !== 16
  ) {
    throw new GlueJobRequestError("GLUE_INVALID_REQUEST", "Invalid beat count.");
  }

  // Step 3: the client's snapshot must still be the persisted one.
  if (
    from.updatedAt !== input.expectedFromUpdatedAt ||
    to.updatedAt !== input.expectedToUpdatedAt
  ) {
    throw new GlueJobRequestError(
      "GLUE_STALE_SNAPSHOT",
      "The Song sequence changed. Save and try again."
    );
  }

  // Step 5: free resolution for `initial` — no quota, no job.
  if (input.takeMode === "initial") {
    const junctions = await deps.loadJunctions(input.songsetId, input.userId);
    const junction = junctions.find((entry) => entry.fromItemId === input.fromItemId);
    if (junction?.state === "ready" && junction.selectedTake !== null) {
      return { kind: "ready", takeId: junction.selectedTake.id, cached: true };
    }
    const newestTakeId = await deps.repository.selectNewestReadyTakeId(
      input.userId,
      derivation.descriptorKey
    );
    if (newestTakeId !== null) {
      const selected = await deps.selectTake(
        input.fromItemId,
        input.songsetId,
        input.userId,
        newestTakeId
      );
      if (selected) {
        logGlueEvent("glue.take.selected", {
          glueJobId: null,
          songsetId: input.songsetId,
          fromItemId: input.fromItemId,
          takeId: newestTakeId,
          cached: true,
        });
        return { kind: "ready", takeId: newestTakeId, cached: true };
      }
    }
  }

  // Step 6: provider configuration is checked before reserving quota.
  if (!deps.isConfigured()) {
    throw new GlueJobRequestError(
      "GLUE_NOT_CONFIGURED",
      "AI transitions are not configured on this server."
    );
  }

  // Step 7: one serializable transaction (stale reads are re-checked there).
  const now = deps.now();
  const jobId = deps.randomId();
  const result = await deps.repository.createJobAtomically({
    userId: input.userId,
    songsetId: input.songsetId,
    fromItemId: input.fromItemId,
    toItemId: to.itemId,
    clientRequestId: input.clientRequestId,
    takeMode: input.takeMode,
    descriptorKey: derivation.descriptorKey,
    descriptorJson: derivation.descriptorJson,
    expectedFromUpdatedAt: new Date(input.expectedFromUpdatedAt),
    expectedToUpdatedAt: new Date(input.expectedToUpdatedAt),
    jobId,
    attemptId: deps.randomId(),
    pendingTakeId: deps.randomId(),
    reservationId: deps.randomId(),
    seed: deps.randomSeed(),
    now,
  });

  if (result.kind === "duplicate") {
    return result.job.phase === "ready" && result.job.resultingTakeId !== null
      ? { kind: "ready", takeId: result.job.resultingTakeId, cached: true }
      : { kind: "job", job: await statusOf(result.job, deps) };
  }
  if (result.kind === "active-job") {
    throw new GlueJobRequestError(
      "GLUE_ACTIVE_JOB",
      "An AI transition is already being made.",
      { jobId: result.jobId, detail: `active junction ${result.fromItemId}` }
    );
  }
  if (result.kind === "quota") {
    throw new GlueJobRequestError(
      "GLUE_DAILY_LIMIT",
      "No paid AI transitions are left today.",
      { detail: result.resetAt === null ? undefined : `resets at ${result.resetAt}` }
    );
  }
  if (result.kind === "inputs-changed") {
    throw new GlueJobRequestError(
      "GLUE_INPUTS_CHANGED",
      "The Song sequence changed while this AI transition was starting."
    );
  }

  // Step 8: commit succeeded; dispatch, and take the free-redispatch path on
  // failure. A worker that already claimed the job is never reset here.
  await dispatchOrFail(result.job, input.userId, deps);
  logGlueEvent("glue.job.created", {
    glueJobId: result.job.id,
    songsetId: result.job.songsetId,
    fromItemId: result.job.fromItemId,
    toItemId: result.job.toItemId,
    takeMode: result.job.takeMode,
    ordinal: result.job.takeOrdinal,
    quotaUsed: null,
  });
  return { kind: "job", job: await statusOf(result.job, deps) };
}

async function statusOf(row: GlueJobRecord, deps: GlueJobDeps): Promise<GlueJobStatus> {
  const quota = await deps.readQuota(row.userId, deps.now());
  return toGlueJobStatus(row, quota, deps.now());
}

async function dispatchOrFail(row: GlueJobRecord, userId: number, deps: GlueJobDeps): Promise<void> {
  const message = {
    kind: "glue.generate" as const,
    jobId: row.id,
    songsetId: row.songsetId,
    userId,
  };
  try {
    await deps.dispatch(message, {
      onAsyncFailure: (error) =>
        deps.repository
          .markDispatchFailed(row.id, userId, error instanceof Error ? error.message : String(error))
          .then(() => undefined)
          .catch(() => undefined),
    });
  } catch (error) {
    logGlueError("glue.dispatch.failed", error, { glueJobId: row.id, songsetId: row.songsetId });
    await deps.repository.markDispatchFailed(
      row.id,
      userId,
      error instanceof Error ? error.message : String(error)
    );
    throw new GlueJobRequestError(
      "GLUE_DISPATCH_FAILED",
      "Could not start the AI transition. Try again.",
      { jobId: row.id }
    );
  }
}

export async function getGlueJobStatus(
  jobId: string,
  userId: number,
  deps: GlueJobDeps = createGlueJobDeps()
): Promise<GlueJobStatus> {
  const row = await deps.repository.findJob(jobId, userId);
  if (!row) {
    throw new GlueJobRequestError("GLUE_NOT_FOUND", "AI transition not found.", { jobId });
  }
  return statusOf(row, deps);
}

export async function listGlueJobsForSongset(
  songsetId: string,
  userId: number,
  deps: GlueJobDeps = createGlueJobDeps()
): Promise<ActiveGlueJobsPayload> {
  const rows = await deps.repository.listJobsForSongset(songsetId, userId);
  const quota = await deps.readQuota(userId, deps.now());
  return buildActiveGlueJobsPayload(rows, quota, deps.now());
}

export async function cancelGlueJob(
  args: { jobId: string; userId: number },
  deps: GlueJobDeps = createGlueJobDeps()
): Promise<{ jobId: string; status: "cancelled" }> {
  const result = await deps.repository.cancelJob(args.jobId, args.userId);
  switch (result.kind) {
    case "cancelled":
      logGlueEvent("glue.job.cancelled", {
        glueJobId: args.jobId,
        songsetId: result.job.songsetId,
        fromItemId: result.job.fromItemId,
      });
      return { jobId: args.jobId, status: "cancelled" };
    case "already-cancelled":
      return { jobId: args.jobId, status: "cancelled" };
    case "provider-contacted":
      throw new GlueJobRequestError(
        "GLUE_PAID_NOT_CANCELLABLE",
        "This paid request can't be cancelled or refunded.",
        { jobId: args.jobId }
      );
    case "terminal":
      throw new GlueJobRequestError("GLUE_NOT_READY", "This AI transition already finished.", {
        jobId: args.jobId,
        detail: result.phase,
      });
    case "not-found":
      throw new GlueJobRequestError("GLUE_NOT_FOUND", "AI transition not found.", {
        jobId: args.jobId,
      });
  }
}

export async function redispatchGlueJob(
  args: { jobId: string; userId: number },
  deps: GlueJobDeps = createGlueJobDeps()
): Promise<GlueJobStatus> {
  const result = await deps.repository.prepareRedispatch(
    args.jobId,
    args.userId,
    deps.randomId(),
    deps.now()
  );
  if (result.kind === "not-found") {
    throw new GlueJobRequestError("GLUE_NOT_FOUND", "AI transition not found.", {
      jobId: args.jobId,
    });
  }
  if (result.kind === "provider-contacted") {
    throw new GlueJobRequestError("GLUE_RETRY_PAID", "This attempt already contacted the provider.", {
      jobId: args.jobId,
    });
  }
  if (result.kind === "not-redispatchable") {
    throw new GlueJobRequestError("GLUE_NOT_READY", "This AI transition is not waiting to be retried.", {
      jobId: args.jobId,
      detail: result.phase,
    });
  }

  await dispatchOrFail(result.job, args.userId, deps);
  return { ...(await statusOf(result.job, deps)), phase: "queued", active: true };
}

export async function retryGlueProcessing(
  args: { jobId: string; userId: number },
  deps: GlueJobDeps = createGlueJobDeps()
): Promise<GlueJobStatus> {
  const result = await deps.repository.prepareRetryProcessing(
    args.jobId,
    args.userId,
    deps.randomId(),
    deps.now()
  );
  if (result.kind === "not-found") {
    throw new GlueJobRequestError("GLUE_NOT_FOUND", "AI transition not found.", {
      jobId: args.jobId,
    });
  }
  if (result.kind === "no-raw-response") {
    throw new GlueJobRequestError(
      "GLUE_NOT_READY",
      "There is no saved AI response to finish.",
      { jobId: args.jobId }
    );
  }
  if (result.kind === "not-retryable") {
    throw new GlueJobRequestError("GLUE_NOT_READY", "This AI transition cannot be retried.", {
      jobId: args.jobId,
      detail: result.phase,
    });
  }
  if (result.kind === "already-ready") {
    return statusOf(result.job, deps);
  }

  try {
    await deps.dispatch(
      {
        kind: "glue.generate",
        jobId: result.job.id,
        songsetId: result.job.songsetId,
        userId: args.userId,
        mode: "staging",
      },
      {
        onAsyncFailure: (error) =>
          deps.repository
            .markRetryDispatchFailed(
              args.jobId,
              args.userId,
              error instanceof Error ? error.message : String(error),
              deps.now()
            )
            .catch(() => undefined),
      }
    );
  } catch (error) {
    logGlueError("glue.dispatch.failed", error, {
      glueJobId: args.jobId,
      mode: "staging",
    });
    await deps.repository.markRetryDispatchFailed(
      args.jobId,
      args.userId,
      error instanceof Error ? error.message : String(error),
      deps.now()
    );
    throw new GlueJobRequestError(
      "GLUE_DISPATCH_FAILED",
      "Could not finish the AI transition. Try again.",
      { jobId: args.jobId }
    );
  }
  return { ...(await statusOf(result.job, deps)), phase: "staging", active: true };
}

export async function resumeGlueJob(
  args: { jobId: string; userId: number },
  deps: GlueJobDeps = createGlueJobDeps()
): Promise<GlueJobStatus> {
  const current = await deps.repository.findJob(args.jobId, args.userId);
  if (!current) {
    throw new GlueJobRequestError("GLUE_NOT_FOUND", "AI transition not found.", {
      jobId: args.jobId,
    });
  }
  if (current.phase === "uploading" || current.phase === "composing") {
    throw new GlueJobRequestError(
      "GLUE_RETRY_PAID",
      "The provider may already be working on this AI transition.",
      { jobId: args.jobId }
    );
  }
  const eligibility = evaluateGlueResume(current);
  if (eligibility.kind === "requires-paid-retry") {
    throw new GlueJobRequestError(
      "GLUE_RETRY_PAID",
      "This attempt needs a new paid request.",
      { jobId: args.jobId }
    );
  }
  if (eligibility.kind === "not-resumable") {
    throw new GlueJobRequestError("GLUE_NOT_READY", "This AI transition cannot be resumed.", {
      jobId: args.jobId,
      detail: eligibility.phase,
    });
  }

  // Revalidate the saved inputs before replaying the same identity, including
  // the definitely-unsent post-contact case (persisted uploads are reused by
  // descriptor, so a changed Song would silently attach the wrong take).
  const items = (await deps.loadItems([current.songsetId], args.userId)).get(current.songsetId) ?? [];
  const index = items.findIndex((item) => item.itemId === current.fromItemId);
  const from = index >= 0 ? items[index] : null;
  const to = index >= 0 ? items[index + 1] ?? null : null;
  if (
    from === null ||
    to === null ||
    from.updatedAt !== current.expectedFromUpdatedAt.toISOString() ||
    to.updatedAt !== current.expectedToUpdatedAt.toISOString()
  ) {
    throw new GlueJobRequestError(
      "GLUE_INPUTS_CHANGED",
      "The Song sequence changed. Save and try again.",
      { jobId: args.jobId }
    );
  }
  const derivation = deriveGlueJunction(from, to);
  if (!derivation.ok || derivation.descriptorKey !== current.descriptorKey) {
    throw new GlueJobRequestError(
      "GLUE_INPUTS_CHANGED",
      "The Song sequence changed. Save and try again.",
      { jobId: args.jobId }
    );
  }

  const result = await deps.repository.prepareResume(
    args.jobId,
    args.userId,
    deps.randomId(),
    deps.now()
  );
  if (result.kind === "not-found") {
    throw new GlueJobRequestError("GLUE_NOT_FOUND", "AI transition not found.", {
      jobId: args.jobId,
    });
  }
  if (result.kind === "requires-paid-retry") {
    throw new GlueJobRequestError(
      "GLUE_RETRY_PAID",
      "This attempt needs a new paid request.",
      { jobId: args.jobId }
    );
  }
  if (result.kind === "not-resumable") {
    throw new GlueJobRequestError("GLUE_NOT_READY", "This AI transition cannot be resumed.", {
      jobId: args.jobId,
      detail: result.phase,
    });
  }

  await dispatchOrFail(result.job, args.userId, deps);
  return { ...(await statusOf(result.job, deps)), phase: result.phase, active: true };
}

/** One SSE snapshot (no percent field; the client renders phase + elapsed). */
export async function glueJobStreamSnapshot(
  jobId: string,
  userId: number,
  deps: GlueJobDeps = createGlueJobDeps()
): Promise<GlueJobStatus | null> {
  const row = await deps.repository.findJob(jobId, userId);
  if (!row) return null;
  return statusOf(row, deps);
}

export interface GlueErrorBody {
  code: string;
  message: string;
  jobId?: string;
  providerStatus?: number;
  detail?: string;
}

/**
 * Maps any Glue operation failure to the stable JSON contract. Unexpected
 * failures are logged with sanitized detail and answered with the generic
 * `GLUE_PROVIDER_UNKNOWN` code — a provider body, URL, or stack never reaches
 * the client.
 */
export function describeGlueError(error: unknown): { status: number; body: GlueErrorBody } {
  if (error instanceof GlueJobRequestError) {
    return {
      status: error.status,
      body: {
        code: error.code,
        message: error.message,
        ...(error.jobId === undefined ? {} : { jobId: error.jobId }),
        ...(error.providerStatus === undefined ? {} : { providerStatus: error.providerStatus }),
        ...(error.detail === undefined ? {} : { detail: error.detail }),
      },
    };
  }
  logGlueError("glue.request.failed", error);
  return {
    status: 500,
    body: {
      code: "GLUE_PROVIDER_UNKNOWN",
      message: "AI transitions are not available right now.",
    },
  };
}
