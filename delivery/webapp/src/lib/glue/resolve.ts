import "server-only";

import { and, asc, eq, gt, inArray, isNotNull, or } from "drizzle-orm";

import { db } from "@/db";
import { glueJobs, glueTakes, songsetItems } from "@/db/schema";
import { canonicalSixDp } from "@/lib/glue/canonical";
import type { GlueBeats } from "@/lib/glue/descriptor";
import {
  computeGlueQuote,
  deriveGlueJunction,
  loadExcerptKeySets,
  loadGlueItemRows,
  type GlueJunctionReady,
  type GlueJunctionDerivation,
  type GlueQuoteCore,
  type GlueResolverItem,
} from "@/lib/glue/quote";
import { readGlueQuota, type GlueQuota } from "@/lib/glue/quota";
import type { SongsetItemDetail } from "@/lib/db/songsets";

// Single readiness source for a Songset's outgoing Junction (spec:
// specs/inpaint-transition-elevenlabs-v2.md §5.1). `resolveGlueJunctions` is
// pure; `loadResolvedGlueJunctions` is the only DB reader for this payload.

export type ResolvedGlueState =
  | "disabled"
  | "needs-generation"
  | "queued"
  | "preparing"
  | "uploading"
  | "composing"
  | "storing-response"
  | "staging"
  | "ready"
  | "failed"
  | "stale";

export type GlueUnavailableReason =
  | "unsaved-draft"
  | "no-next"
  | "no-tempo"
  | "no-analysis"
  | "invalid-arrangement"
  | "quota"
  | "active-job";

export interface ResolvedGlueTake {
  id: string;
  ordinal: number;
  seconds: number;
  r2Key: string;
  descriptorKey: string;
  status: "ok" | "check";
  reasons: string[];
}

/** Menu entry: the resolved take plus when this take finished staging. */
export interface ResolvedGlueTakeOption extends ResolvedGlueTake {
  createdAt: string;
}

export interface ResolvedGlueActiveJob {
  id: string;
  phase: string;
  createdAt: string;
  startedAt: string | null;
}

export interface ResolvedGlueJunctionQuote {
  uncachedSides: GlueQuoteCore["uncachedSides"];
  generationSeconds: number;
  uploadCredits: number;
  generationCredits: number;
  estimatedNewCredits: number;
}

export interface ResolvedGlueJunction {
  fromItemId: string;
  toItemId: string | null;
  glueEnabled: boolean;
  glueBeats: GlueBeats;
  descriptorKey: string | null;
  state: ResolvedGlueState;
  selectedTake: ResolvedGlueTake | null;
  /** Ready takes for this descriptor, oldest ordinal first (free restore). */
  availableTakes: ResolvedGlueTakeOption[];
  activeJob: ResolvedGlueActiveJob | null;
  quote: ResolvedGlueJunctionQuote | null;
  quotaLimit: number;
  quotaUsed: number;
  quotaRemaining: number;
  quotaResetAt: string | null;
  expectedFromUpdatedAt: string | null;
  expectedToUpdatedAt: string | null;
  unavailableReason: GlueUnavailableReason | null;
}

/** Take row shape accepted by the pure resolver (spec §5.1). */
export interface GlueTakeRow {
  id: string;
  userId: number;
  descriptorKey: string;
  descriptorJson: string;
  ordinal: number;
  status: string;
  r2Key: string | null;
  seconds: number | null;
  /** Staged WAV checksum (`metrics_json.audioSha256`); null until ready. */
  audioSha256: string | null;
  metricsJson: string;
  reasonsJson: string;
  createdAt: string;
}

/** Job row shape accepted by the pure resolver (spec §5.1). */
export interface GlueJobRow {
  id: string;
  userId: number;
  fromItemId: string;
  toItemId: string;
  takeMode: string;
  descriptorKey: string;
  phase: string;
  active: boolean;
  attemptId: string;
  providerContacted: boolean;
  resultingTakeId: string | null;
  activeTakeId: string | null;
  errorCode: string | null;
  errorDetail: string | null;
  retryKind: string | null;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  heartbeatAt: string | null;
}

export interface ResolveGlueJunctionsArgs {
  items: GlueResolverItem[];
  takes: GlueTakeRow[];
  jobs: GlueJobRow[];
  quota: GlueQuota;
  cachedExcerptKeys: ReadonlySet<string>;
  rejectedExcerptKeys: ReadonlySet<string>;
}

const ACTIVE_JOB_STATES = new Set<ResolvedGlueState>([
  "queued",
  "preparing",
  "uploading",
  "composing",
  "storing-response",
  "staging",
]);

function parseReasons(serialized: string): string[] {
  try {
    const parsed: unknown = JSON.parse(serialized);
    return Array.isArray(parsed)
      ? parsed.filter((value): value is string => typeof value === "string")
      : [];
  } catch {
    return [];
  }
}

function parseTakeQa(take: GlueTakeRow): { status: "ok" | "check"; reasons: string[] } {
  let status: "ok" | "check" = "ok";
  try {
    const metrics: unknown = JSON.parse(take.metricsJson);
    if (
      typeof metrics === "object" &&
      metrics !== null &&
      "status" in metrics &&
      metrics.status === "check"
    ) {
      status = "check";
    }
  } catch {
    status = "ok";
  }
  return { status, reasons: parseReasons(take.reasonsJson) };
}

function toResolvedTake(take: GlueTakeRow): ResolvedGlueTake {
  const qa = parseTakeQa(take);
  return {
    id: take.id,
    ordinal: take.ordinal,
    seconds: canonicalSixDp(take.seconds ?? 0),
    r2Key: take.r2Key as string,
    descriptorKey: take.descriptorKey,
    status: qa.status,
    reasons: qa.reasons,
  };
}

function toAvailableTake(take: GlueTakeRow): ResolvedGlueTakeOption {
  return { ...toResolvedTake(take), createdAt: take.createdAt };
}

function latestJobFor(jobs: readonly GlueJobRow[], fromItemId: string): GlueJobRow | null {
  let latest: GlueJobRow | null = null;
  for (const job of jobs) {
    if (job.fromItemId !== fromItemId) continue;
    if (latest === null || job.createdAt > latest.createdAt) latest = job;
  }
  return latest;
}

function activeJobFor(jobs: readonly GlueJobRow[], fromItemId: string): GlueJobRow | null {
  const active = jobs
    .filter((job) => job.fromItemId === fromItemId && job.active)
    .sort((left, right) => (left.createdAt < right.createdAt ? 1 : -1));
  return active[0] ?? null;
}

function toActiveJob(job: GlueJobRow | null): ResolvedGlueActiveJob | null {
  if (job === null || !ACTIVE_JOB_STATES.has(job.phase as ResolvedGlueState)) return null;
  return {
    id: job.id,
    phase: job.phase,
    createdAt: job.createdAt,
    startedAt: job.startedAt,
  };
}

export type GlueSelectionErrorCode =
  | "GLUE_NOT_ENABLED"
  | "GLUE_NO_SUCCESSOR"
  | "GLUE_INPUTS_CHANGED"
  | "GLUE_NOT_READY"
  | "GLUE_STALE_SNAPSHOT";

/**
 * Pure validation for a PATCH selection. `null` means the take is an effective
 * selection for the saved Junction. Cross-user, non-ready, missing, and
 * wrong-junction takes are `GLUE_NOT_READY`; a ready owner take whose stored
 * descriptor no longer matches is `GLUE_STALE_SNAPSHOT`.
 */
export function glueTakeSelectionError(args: {
  glueEnabled: boolean;
  hasSuccessor: boolean;
  derivation: GlueJunctionDerivation;
  take: GlueTakeRow | null;
  ownerUserId: number;
}): GlueSelectionErrorCode | null {
  if (!args.glueEnabled) return "GLUE_NOT_ENABLED";
  if (!args.hasSuccessor) return "GLUE_NO_SUCCESSOR";
  if (!args.derivation.ok) return "GLUE_INPUTS_CHANGED";
  if (args.take === null) return "GLUE_NOT_READY";
  if (
    args.take.userId !== args.ownerUserId ||
    args.take.status !== "ready" ||
    args.take.r2Key === null ||
    !((args.take.seconds ?? 0) > 0)
  ) {
    return "GLUE_NOT_READY";
  }
  if (
    args.take.descriptorKey !== args.derivation.descriptorKey ||
    args.take.descriptorJson !== args.derivation.descriptorJson
  ) {
    return "GLUE_STALE_SNAPSHOT";
  }
  return null;
}

function toJunctionQuote(
  derivation: GlueJunctionReady,
  cachedExcerptKeys: ReadonlySet<string>,
  rejectedExcerptKeys: ReadonlySet<string>
): ResolvedGlueJunctionQuote {
  const core = computeGlueQuote(derivation, cachedExcerptKeys, rejectedExcerptKeys);
  return {
    uncachedSides: core.uncachedSides,
    generationSeconds: core.generationSeconds,
    uploadCredits: core.uploadCredits,
    generationCredits: core.generationCredits,
    estimatedNewCredits: core.estimatedNewCredits,
  };
}

/**
 * Pure resolver: one Junction per outgoing item, in position order. It never
 * invents take or job rows. `ready` requires an effective selected take:
 * enabled + ready status + same owner + stored descriptor exactly equal to the
 * freshly derived descriptor.
 */
export function resolveGlueJunctions(args: ResolveGlueJunctionsArgs): ResolvedGlueJunction[] {
  const { items, takes, jobs, quota, cachedExcerptKeys, rejectedExcerptKeys } = args;
  const anyActiveJob = jobs.some((job) => job.active);

  return items.map((item, index) => {
    const to = index + 1 < items.length ? items[index + 1] : null;
    const base = {
      fromItemId: item.itemId,
      toItemId: to?.itemId ?? null,
      glueEnabled: item.glueEnabled,
      glueBeats: item.glueBeats,
      quotaLimit: quota.limit,
      quotaUsed: quota.used,
      quotaRemaining: quota.remaining,
      quotaResetAt: quota.resetAt,
      expectedFromUpdatedAt: item.updatedAt,
      expectedToUpdatedAt: to?.updatedAt ?? null,
    } satisfies Partial<ResolvedGlueJunction>;

    if (to === null) {
      return {
        ...base,
        descriptorKey: null,
        state: "disabled",
        selectedTake: null,
        availableTakes: [],
        activeJob: null,
        quote: null,
        unavailableReason: "no-next",
      };
    }

    const derivation = deriveGlueJunction(item, to);
    const descriptorKey = derivation.ok ? derivation.descriptorKey : null;
    const activeJobRow = activeJobFor(jobs, item.itemId);
    const activeJob = toActiveJob(activeJobRow);
    const latestJob = latestJobFor(jobs, item.itemId);
    const selectedRow =
      item.glueActiveTakeId === null
        ? null
        : takes.find((take) => take.id === item.glueActiveTakeId) ?? null;
    // The selected-take menu restores any ready take of this descriptor for
    // free, so the resolved payload carries the full list (oldest first).
    const availableTakes = derivation.ok
      ? takes
          .filter(
            (take) =>
              take.userId === item.userId &&
              take.descriptorKey === derivation.descriptorKey &&
              take.status === "ready" &&
              take.r2Key !== null &&
              (take.seconds ?? 0) > 0
          )
          .sort((left, right) => left.ordinal - right.ordinal)
          .map(toAvailableTake)
      : [];

    if (!item.glueEnabled) {
      return {
        ...base,
        descriptorKey,
        state: "disabled",
        selectedTake: null,
        availableTakes,
        activeJob,
        quote: null,
        unavailableReason: null,
      };
    }

    if (!derivation.ok) {
      return {
        ...base,
        descriptorKey: null,
        state: activeJob
          ? (activeJob.phase as ResolvedGlueState)
          : selectedRow
            ? "stale"
            : "needs-generation",
        selectedTake: null,
        availableTakes: [],
        activeJob,
        quote: null,
        unavailableReason: activeJob ? "active-job" : derivation.reason,
      };
    }

    const selectionError = glueTakeSelectionError({
      glueEnabled: item.glueEnabled,
      hasSuccessor: true,
      derivation,
      take: selectedRow,
      ownerUserId: item.userId,
    });
    const selectedTake: ResolvedGlueTake | null =
      selectionError === null && selectedRow !== null ? toResolvedTake(selectedRow) : null;

    let state: ResolvedGlueState;
    let unavailableReason: GlueUnavailableReason | null = null;
    if (activeJob) {
      state = activeJob.phase as ResolvedGlueState;
      unavailableReason = "active-job";
    } else if (selectedTake !== null) {
      state = "ready";
    } else if (selectedRow !== null) {
      state = "stale";
    } else if (
      latestJob !== null &&
      latestJob.phase === "failed" &&
      latestJob.descriptorKey === derivation.descriptorKey
    ) {
      state = "failed";
    } else {
      state = "needs-generation";
      if (quota.remaining <= 0) unavailableReason = "quota";
      else if (anyActiveJob) unavailableReason = "active-job";
    }

    return {
      ...base,
      descriptorKey,
      state,
      selectedTake,
      availableTakes,
      activeJob,
      quote: toJunctionQuote(derivation, cachedExcerptKeys, rejectedExcerptKeys),
      unavailableReason,
    };
  });
}

function toIso(value: Date | string | null): string | null {
  if (value === null) return null;
  return value instanceof Date ? value.toISOString() : value;
}

export function takeRowFromDb(row: typeof glueTakes.$inferSelect): GlueTakeRow {
  let audioSha256: string | null = null;
  try {
    const metrics: unknown = JSON.parse(row.metricsJson);
    if (
      typeof metrics === "object" &&
      metrics !== null &&
      "audioSha256" in metrics &&
      typeof metrics.audioSha256 === "string"
    ) {
      audioSha256 = metrics.audioSha256;
    }
  } catch {
    audioSha256 = null;
  }
  return {
    id: row.id,
    userId: row.userId,
    descriptorKey: row.descriptorKey,
    descriptorJson: row.descriptorJson,
    ordinal: row.ordinal,
    status: row.status,
    r2Key: row.r2Key,
    seconds: row.seconds,
    audioSha256,
    metricsJson: row.metricsJson,
    reasonsJson: row.reasonsJson,
    createdAt: row.createdAt.toISOString(),
  };
}

function jobRowFromDb(row: typeof glueJobs.$inferSelect): GlueJobRow {
  return {
    id: row.id,
    userId: row.userId,
    fromItemId: row.fromItemId,
    toItemId: row.toItemId,
    takeMode: row.takeMode,
    descriptorKey: row.descriptorKey,
    phase: row.phase,
    active: row.active,
    attemptId: row.attemptId,
    providerContacted: row.providerContacted,
    resultingTakeId: row.resultingTakeId,
    activeTakeId: row.activeTakeId,
    errorCode: row.errorCode,
    errorDetail: row.errorDetail,
    retryKind: row.retryKind,
    createdAt: row.createdAt.toISOString(),
    startedAt: toIso(row.startedAt),
    finishedAt: toIso(row.finishedAt),
    heartbeatAt: toIso(row.heartbeatAt),
  };
}

/**
 * Loads everything `resolveGlueJunctions` needs for one or more owned
 * Songsets. Kept private to this module so every caller shares one readiness
 * definition; `loadResolvedGlueJunctions` is the public single-Songset reader.
 */
async function loadResolvedGlueJunctionsForSongsets(
  songsetIds: readonly string[],
  userId: number
): Promise<Map<string, ResolvedGlueJunction[]>> {
  const resolved = new Map<string, ResolvedGlueJunction[]>();
  if (songsetIds.length === 0) return resolved;

  const itemsBySongset = await loadGlueItemRows(songsetIds, userId);
  const allItems = [...itemsBySongset.values()].flat();
  const activeTakeIds = allItems
    .map((item) => item.glueActiveTakeId)
    .filter((id): id is string => id !== null);
  // Ready takes for every Junction descriptor feed the free restore menu.
  const descriptorKeys = new Set<string>();
  for (const items of itemsBySongset.values()) {
    for (let index = 0; index + 1 < items.length; index += 1) {
      const derivation = deriveGlueJunction(items[index], items[index + 1]);
      if (derivation.ok) descriptorKeys.add(derivation.descriptorKey);
    }
  }
  const activeTakeClause =
    activeTakeIds.length > 0
      ? inArray(glueTakes.id, [...new Set(activeTakeIds)])
      : undefined;
  const descriptorTakeClause =
    descriptorKeys.size > 0
      ? and(
          eq(glueTakes.userId, userId),
          eq(glueTakes.status, "ready"),
          inArray(glueTakes.descriptorKey, [...descriptorKeys]),
          isNotNull(glueTakes.r2Key),
          gt(glueTakes.seconds, 0)
        )
      : undefined;
  const takeWhere =
    activeTakeClause && descriptorTakeClause
      ? or(activeTakeClause, descriptorTakeClause)
      : activeTakeClause ?? descriptorTakeClause;

  const [takeRows, jobRows, quota] = await Promise.all([
    takeWhere
      ? db.select().from(glueTakes).where(takeWhere)
      : Promise.resolve([]),
    db
      .select()
      .from(glueJobs)
      .where(
        and(
          inArray(glueJobs.songsetId, [...songsetIds]),
          eq(glueJobs.userId, userId)
        )
      )
      .orderBy(asc(glueJobs.createdAt)),
    readGlueQuota(userId),
  ]);

  const takes = takeRows.map(takeRowFromDb);
  const jobs = jobRows.map(jobRowFromDb);
  const contentHashes = allItems
    .map((item) => item.recording?.contentHash)
    .filter((hash): hash is string => typeof hash === "string");
  const { cached, rejected } = await loadExcerptKeySets(userId, contentHashes);

  for (const [songsetId, items] of itemsBySongset) {
    resolved.set(
      songsetId,
      resolveGlueJunctions({
        items,
        takes,
        jobs: jobs.filter((job) => items.some((item) => item.itemId === job.fromItemId)),
        quota,
        cachedExcerptKeys: cached,
        rejectedExcerptKeys: rejected,
      })
    );
  }
  return resolved;
}

/** The only DB reader for the resolved Junction payload. */
export async function loadResolvedGlueJunctions(
  songsetId: string,
  userId: number
): Promise<ResolvedGlueJunction[]> {
  const resolved = await loadResolvedGlueJunctionsForSongsets([songsetId], userId);
  return resolved.get(songsetId) ?? [];
}

/** Batch reader used by Songset list durations; same readiness definition. */
export async function loadResolvedGlueJunctionsForList(
  songsetIds: readonly string[],
  userId: number
): Promise<Map<string, Map<string, ResolvedGlueJunction>>> {
  const resolved = await loadResolvedGlueJunctionsForSongsets(songsetIds, userId);
  return new Map(
    [...resolved.entries()].map(([songsetId, junctions]) => [
      songsetId,
      new Map(junctions.map((junction) => [junction.fromItemId, junction] as const)),
    ])
  );
}

/**
 * Validates and stores a take selection. The detailed implementation lives in
 * `songsets.ts` (it owns the relational item mapping); this lazy import keeps
 * the module graph acyclic.
 */
export async function selectGlueTake(
  itemId: string,
  songsetId: string,
  userId: number,
  takeId: string
): Promise<{ item: SongsetItemDetail; glue: ResolvedGlueJunction } | null> {
  // Static import cannot work: songsets.ts imports the resolver loaders above,
  // and this function is the only reverse edge. The literal specifier is
  // loaded at call time, after both modules have initialized.
  const { selectGlueTake: select } = await import("@/lib/db/songsets");
  return select(itemId, songsetId, userId, takeId);
}
