import "server-only";

import {
  getItemDurationSeconds,
  parseRecordingBeats,
  parseRecordingSections,
  parseSongArrangement,
  validateSongArrangement,
  type RecordingSection,
} from "@/lib/arrangements";
import { buildBoundaryDescriptor, buildGlueDescriptor, type GlueBeats } from "@/lib/glue/descriptor";
import { defaultBoundarySettings, type GlueTarget, type SongsetBoundary, type SongsetBoundarySettings } from "./boundary-types";
import { buildGlueExcerptKey } from "@/lib/glue/excerpt-key";
import { canonicalSixDp } from "@/lib/glue/canonical";
import { readGlueQuota, type GlueQuota } from "@/lib/glue/quota";
import { db } from "@/db";
import type { mutationDb } from "@/db/mutation-db";
import { glueExcerpts, recordings, songsetItems, songsets } from "@/db/schema";
import { and, asc, eq, inArray } from "drizzle-orm";

// Generation quote and the shared junction derivation used by both the quote
// endpoint and the resolved junction payload (spec:
// specs/inpaint-transition-elevenlabs-v2.md §2.3, §3.1, §5.1). Every value is
// computed on the server; clients display these numbers and never recompute
// credits.

/** The item facts a Junction needs. Loaded once per Songset by the resolver. */
export interface GlueResolverItem {
  itemId: string;
  position: number;
  /** Songset owner; a take must belong to this user to be effective. */
  userId: number;
  glueEnabled: boolean;
  glueBeats: GlueBeats;
  glueActiveTakeId: string | null;
  updatedAt: string;
  updatedAtPrecise?: string;
  boundarySettings?: SongsetBoundarySettings;
  arrangementSteps: string | null;
  recording: {
    contentHash: string | null;
    durationSeconds: number | null;
    tempoBpm: number | null;
    sections: RecordingSection[] | null;
    beats: number[] | null;
  } | null;
}

export type GlueJunctionUnavailableReason =
  | "no-next"
  | "no-tempo"
  | "no-analysis"
  | "invalid-arrangement";

export interface GlueJunctionSide {
  contentHash: string;
  steps: [number, number][] | null;
  arrangedDurationSeconds: number;
  /** `min(12, arrangedDurationSeconds)`, canonical 6 dp. */
  excerptSeconds: number;
  excerptTooShort: boolean;
}

export interface GlueJunctionReady {
  ok: true;
  descriptorKey: string;
  descriptorJson: string;
  fromSide: GlueJunctionSide;
  toSide: GlueJunctionSide;
  generationSeconds: number;
  target?: SongsetBoundary;
}

export type GlueJunctionDerivation =
  | GlueJunctionReady
  | { ok: false; reason: GlueJunctionUnavailableReason };

export interface GlueExcerptSideQuote {
  side: "A" | "B";
  seconds: number;
  /** @deprecated Compatibility for deployed bundles. New clients display attempts. */
  credits: number;
}

export interface GlueBlockedSide {
  side: "A" | "B";
  code: "GLUE_COPYRIGHT_REJECTED" | "GLUE_EXCERPT_TOO_SHORT";
}

export interface GlueQuoteCore {
  uncachedSides: GlueExcerptSideQuote[];
  generationSeconds: number;
  /** @deprecated Compatibility for deployed bundles. New clients display attempts. */
  uploadCredits: number;
  /** @deprecated Compatibility for deployed bundles. New clients display attempts. */
  generationCredits: number;
  /** @deprecated Compatibility for deployed bundles. New clients display attempts. */
  estimatedNewCredits: number;
  blockedSides: GlueBlockedSide[];
  gpuFallback?: boolean;
}

export interface GlueQuoteFields extends GlueQuoteCore {
  target?: SongsetBoundary;
  descriptorKey: string;
  fromItemId: string;
  toItemId: string;
  expectedFromUpdatedAt: string;
  expectedToUpdatedAt: string;
  quotaLimit: GlueQuota["limit"];
  quotaUsed: number;
  quotaRemaining: number;
  quotaResetAt: string | null;
}

export type GlueQuoteErrorCode =
  | "GLUE_NOT_ENABLED"
  | "GLUE_NO_SUCCESSOR"
  | "GLUE_TEMPO_UNAVAILABLE"
  | "GLUE_ANALYSIS_UNAVAILABLE"
  | "GLUE_ARRANGEMENT_INVALID"
  | "GLUE_EXCERPT_TOO_SHORT"
  | "GLUE_DURATION_TOO_LONG";

export class GlueQuoteError extends Error {
  readonly code: GlueQuoteErrorCode;
  readonly status: number;

  constructor(code: GlueQuoteErrorCode, status: number, message: string) {
    super(message);
    this.name = "GlueQuoteError";
    this.code = code;
    this.status = status;
  }
}

const MAX_GENERATION_SECONDS = 120;
const EXCERPT_SECONDS = 12;
const CREDITS_PER_SECOND = 15;

/** `Math.round` is half-up for the non-negative seconds this module handles. */
function roundCredits(seconds: number): number {
  return Math.round(CREDITS_PER_SECOND * seconds);
}

function deriveGlueSide(
  item: GlueResolverItem
):
  | { ok: true; side: GlueJunctionSide }
  | { ok: false; reason: Exclude<GlueJunctionUnavailableReason, "no-next"> } {
  const contentHash = item.recording?.contentHash ?? null;
  if (contentHash === null) return { ok: false, reason: "no-analysis" };

  const arrangedDurationSeconds = getItemDurationSeconds({
    arrangementSteps: item.arrangementSteps,
    recording: { durationSeconds: item.recording?.durationSeconds ?? null },
  });
  if (arrangedDurationSeconds === null) return { ok: false, reason: "invalid-arrangement" };

  const parsed = parseSongArrangement(item.arrangementSteps);
  if (parsed.present && "invalid" in parsed) {
    return { ok: false, reason: "invalid-arrangement" };
  }
  const steps = parsed.present
    ? parsed.arrangement.steps.map(
        (step) => [step.startSeconds, step.endSeconds] as [number, number]
      )
    : null;

  if (item.arrangementSteps !== null) {
    const validated = validateSongArrangement(
      item.arrangementSteps,
      item.recording?.sections ?? null,
      item.recording?.beats ?? [],
      item.recording?.durationSeconds ?? arrangedDurationSeconds
    );
    if (!validated.ok) {
      return {
        ok: false,
        reason:
          validated.code === "ANALYSIS_UNAVAILABLE" ? "no-analysis" : "invalid-arrangement",
      };
    }
  }

  const excerptSeconds = canonicalSixDp(Math.min(EXCERPT_SECONDS, arrangedDurationSeconds));
  return {
    ok: true,
    side: {
      contentHash,
      steps,
      arrangedDurationSeconds,
      excerptSeconds,
      excerptTooShort: excerptSeconds < 1,
    },
  };
}

/**
 * Derives the current descriptor and generation length for the Junction
 * `from -> to`. Pure; callers pass the visible successor (null = no next).
 */
export function deriveGlueJunction(
  from: GlueResolverItem,
  to: GlueResolverItem | null
): GlueJunctionDerivation {
  if (to === null) return { ok: false, reason: "no-next" };
  const fromSide = deriveGlueSide(from);
  if (!fromSide.ok) return fromSide;
  const toSide = deriveGlueSide(to);
  if (!toSide.ok) return toSide;

  const toTempoBpm = to.recording?.tempoBpm ?? null;
  if (toTempoBpm === null || !(toTempoBpm > 0)) {
    return { ok: false, reason: "no-tempo" };
  }

  const built = buildGlueDescriptor({
    beats: from.glueBeats,
    toTempoBpm,
    from: { contentHash: fromSide.side.contentHash, steps: fromSide.side.steps },
    to: { contentHash: toSide.side.contentHash, steps: toSide.side.steps },
  });

  return {
    ok: true,
    descriptorKey: built.key,
    descriptorJson: built.canonicalJson,
    fromSide: fromSide.side,
    toSide: toSide.side,
    generationSeconds: canonicalSixDp(Math.max(3, (from.glueBeats * 60) / toTempoBpm)),
  };
}

export function getGlueTargetItems(
  items: readonly GlueResolverItem[],
  target: GlueTarget,
  fromItemId?: string
): { from: GlueResolverItem; to: GlueResolverItem | null } | null {
  if (target === "junction") {
    const index = items.findIndex((item) => item.itemId === fromItemId);
    return index < 0 ? null : { from: items[index], to: items[index + 1] ?? null };
  }
  const item = target === "intro" ? items[0] : items.at(-1);
  if (!item || (fromItemId !== undefined && fromItemId !== item.itemId)) return null;
  const settings = item.boundarySettings?.[target] ?? defaultBoundarySettings;
  return {
    from: {
      ...item,
      glueEnabled: settings.enabled,
      glueBeats: settings.beats,
      glueActiveTakeId: settings.takeId,
    },
    to: item,
  };
}

export function isBoundaryEligible(item: GlueResolverItem): boolean {
  return item.arrangementSteps !== null && deriveGlueSide(item).ok;
}

export function deriveGlueTarget(
  target: GlueTarget,
  from: GlueResolverItem,
  to: GlueResolverItem | null
): GlueJunctionDerivation {
  if (target === "junction") return deriveGlueJunction(from, to);
  if (from.arrangementSteps === null) return { ok: false, reason: "invalid-arrangement" };
  const side = deriveGlueSide(from);
  if (!side.ok) return side;
  const bpm = from.recording?.tempoBpm;
  if (bpm == null || !Number.isFinite(bpm) || bpm <= 0) {
    return { ok: false, reason: "no-tempo" };
  }
  const descriptor = buildBoundaryDescriptor(target, from.glueBeats, bpm, side.side);
  return {
    ok: true,
    target,
    descriptorKey: descriptor.key,
    descriptorJson: descriptor.canonicalJson,
    fromSide: side.side,
    toSide: side.side,
    generationSeconds: Math.round((from.glueBeats * 60_000) / descriptor.descriptor.toTempoBpm) / 1000,
  };
}

/** Pure credits + blocked-side calculation from the current cache state. */
export function computeGlueQuote(
  derivation: GlueJunctionReady,
  cachedExcerptKeys: ReadonlySet<string>,
  rejectedExcerptKeys: ReadonlySet<string>,
  gpuFallback = false
): GlueQuoteCore {
  const uncachedSides: GlueExcerptSideQuote[] = [];
  const blockedSides: GlueBlockedSide[] = [];
  let gpuOnly = false;

  const sides: Array<{ side: "A" | "B"; value: GlueJunctionSide }> = [
    { side: "A", value: derivation.fromSide },
    { side: "B", value: derivation.toSide },
  ];
  for (const { side, value } of sides) {
    if (derivation.target === "intro" && side === "A") continue;
    if (derivation.target === "outro" && side === "B") continue;
    const excerptKey = buildGlueExcerptKey({
      side,
      seconds: value.excerptSeconds,
      contentHash: value.contentHash,
      steps: value.steps,
    }).key;
    if (rejectedExcerptKeys.has(excerptKey) && !gpuFallback) {
      blockedSides.push({ side, code: "GLUE_COPYRIGHT_REJECTED" });
      continue;
    }
    if (value.excerptTooShort) {
      blockedSides.push({ side, code: "GLUE_EXCERPT_TOO_SHORT" });
      continue;
    }
    if (rejectedExcerptKeys.has(excerptKey)) {
      gpuOnly = true;
      continue;
    }
    if (cachedExcerptKeys.has(excerptKey)) continue;
    uncachedSides.push({
      side,
      seconds: value.excerptSeconds,
      credits: roundCredits(value.excerptSeconds),
    });
  }

  const uploadCredits = gpuOnly ? 0 : uncachedSides.reduce((sum, side) => sum + side.credits, 0);
  const generationCredits = gpuOnly ? 0 : roundCredits(derivation.generationSeconds);
  return {
    uncachedSides: gpuOnly ? [] : uncachedSides,
    generationSeconds: derivation.generationSeconds,
    uploadCredits,
    generationCredits,
    estimatedNewCredits: uploadCredits + generationCredits,
    blockedSides,
  };
}

export async function loadExcerptKeySets(
  userId: number,
  contentHashes: readonly string[]
): Promise<{ cached: Set<string>; rejected: Set<string> }> {
  const cached = new Set<string>();
  const rejected = new Set<string>();
  const hashes = [...new Set(contentHashes.filter((hash) => hash.length > 0))];
  if (hashes.length === 0) return { cached, rejected };

  const rows = await db
    .select({
      logicalKey: glueExcerpts.logicalKey,
      phase: glueExcerpts.phase,
      elevenlabsSongId: glueExcerpts.elevenlabsSongId,
    })
    .from(glueExcerpts)
    .where(and(eq(glueExcerpts.userId, userId), inArray(glueExcerpts.contentHash, hashes)));

  for (const row of rows) {
    if (row.phase === "rejected") rejected.add(row.logicalKey);
    else if (row.phase === "uploaded" && row.elevenlabsSongId !== null) cached.add(row.logicalKey);
  }
  return { cached, rejected };
}

/** Loads the visible, position-ordered items for one or more owned Songsets. */
export async function loadGlueItemRows(
  songsetIds: readonly string[],
  userId: number,
  client: typeof db | Parameters<Parameters<typeof mutationDb.transaction>[0]>[0] = db
): Promise<Map<string, GlueResolverItem[]>> {
  const result = new Map<string, GlueResolverItem[]>();
  if (songsetIds.length === 0) return result;

  const rows = await client
    .select({
      songsetId: songsetItems.songsetId,
      itemId: songsetItems.id,
      position: songsetItems.position,
      userId: songsets.userId,
      boundarySettings: songsets.boundarySettings,
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
    .leftJoin(recordings, eq(recordings.hashPrefix, songsetItems.recordingHashPrefix))
    .where(and(inArray(songsetItems.songsetId, [...songsetIds]), eq(songsets.userId, userId)))
    .orderBy(asc(songsetItems.songsetId), asc(songsetItems.position));

  for (const row of rows) {
    if (row.recordingDeletedAt !== null) continue;
    // The query is scoped to these Songsets and owner-checked in SQL, so a row
    // whose projected join columns are unusable still belongs to the single
    // requested Songset. Losing it would leave a visible Item with no Junction.
    const songsetId = row.songsetId ?? (songsetIds.length === 1 ? songsetIds[0] : null);
    if (songsetId === null) continue;
    const items = result.get(songsetId) ?? [];
    const updatedAt = row.updatedAt instanceof Date ? row.updatedAt.toISOString() : row.updatedAt;
    items.push({
      itemId: row.itemId,
      position: row.position,
      userId: row.userId ?? userId,
      boundarySettings: row.boundarySettings,
      glueEnabled: row.glueEnabled,
      glueBeats: normalizeGlueBeats(row.glueBeats),
      glueActiveTakeId: row.glueActiveTakeId,
      updatedAt,
      arrangementSteps: row.arrangementSteps,
      recording: row.contentHash === null
        ? null
        : {
            contentHash: row.contentHash,
            durationSeconds: row.durationSeconds,
            tempoBpm: row.tempoBpm,
            sections: parseRecordingSections(row.sections ?? null),
            beats: parseRecordingBeats(row.beats ?? null),
          },
    });
    result.set(row.songsetId, items);
  }
  return result;
}

export function normalizeGlueBeats(value: number): GlueBeats {
  return value === 2 || value === 4 || value === 8 || value === 16 ? value : 4;
}

/**
 * Whether the browser may quote a junction whose cached excerpt is a copyright
 * rejection (the GPU providers synthesize from the incoming mix instead).
 * Must match the Glue Lambda's selector: a quote that claims GPU availability
 * while the worker serves ElevenLabs lets a user start a junction that cannot
 * be generated.
 */
export function isGpuFallbackEnabled(provider = process.env.SOW_GLUE_PROVIDER): boolean {
  return provider === "cascade";
}

/**
 * Owner-scoped generation quote. Returns `null` for an unknown Songset or
 * `fromItemId`; throws `GlueQuoteError` for saved-input validation failures.
 */
export async function getGlueQuote(args: {
  songsetId: string;
  userId: number;
  fromItemId?: string;
  target?: GlueTarget;
  takeMode: "initial" | "new-take";
}): Promise<GlueQuoteFields | null> {
  if (args.takeMode !== "initial" && args.takeMode !== "new-take") {
    throw new TypeError(`Unknown Glue take mode: ${String(args.takeMode)}`);
  }
  const itemsBySongset = await loadGlueItemRows([args.songsetId], args.userId);
  const items = itemsBySongset.get(args.songsetId) ?? [];
  const target = args.target ?? "junction";
  const inputs = getGlueTargetItems(items, target, args.fromItemId);
  if (!inputs) return null;
  const { from, to } = inputs;
  if (!from.glueEnabled) {
    throw new GlueQuoteError("GLUE_NOT_ENABLED", 409, "AI transition is not enabled.");
  }
  const derivation = deriveGlueTarget(target, from, to);
  if (!derivation.ok) {
    throw quoteErrorForReason(derivation.reason);
  }
  if (derivation.generationSeconds > MAX_GENERATION_SECONDS) {
    throw new GlueQuoteError(
      "GLUE_DURATION_TOO_LONG",
      422,
      "AI transition would be longer than 120 seconds."
    );
  }
  const { cached, rejected } = await loadExcerptKeySets(args.userId, [
    derivation.fromSide.contentHash,
    derivation.toSide.contentHash,
  ]);
  const gpuFallback = isGpuFallbackEnabled();
  const core = computeGlueQuote(derivation, cached, rejected, gpuFallback);
  const quota = await readGlueQuota(args.userId);
  return {
    ...(target === "junction" ? {} : { target }),
    descriptorKey: derivation.descriptorKey,
    fromItemId: from.itemId,
    toItemId: to!.itemId,
    expectedFromUpdatedAt: from.updatedAt,
    expectedToUpdatedAt: to!.updatedAt,
    ...core,
    ...(gpuFallback ? { gpuFallback } : {}),
    quotaLimit: quota.limit,
    quotaUsed: quota.used,
    quotaRemaining: quota.remaining,
    quotaResetAt: quota.resetAt,
  };
}

function quoteErrorForReason(reason: GlueJunctionUnavailableReason): GlueQuoteError {
  switch (reason) {
    case "no-next":
      return new GlueQuoteError(
        "GLUE_NO_SUCCESSOR",
        409,
        "Choose a later song for the AI transition."
      );
    case "no-tempo":
      return new GlueQuoteError(
        "GLUE_TEMPO_UNAVAILABLE",
        409,
        "The incoming song needs a tempo before an AI transition can be generated."
      );
    case "no-analysis":
      return new GlueQuoteError(
        "GLUE_ANALYSIS_UNAVAILABLE",
        409,
        "Song analysis is unavailable for this AI transition."
      );
    case "invalid-arrangement":
      return new GlueQuoteError(
        "GLUE_ARRANGEMENT_INVALID",
        409,
        "The saved arrangement is invalid for the current analysis."
      );
  }
}
