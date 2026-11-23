import { db } from "@/db";
import { mutationDb } from "@/db/mutation-db";
import {
  lyricMarks,
  songsets,
  songsetItems,
  renderJobs,
  songs,
  recordings,
  userSettings,
  users,
  glueJobs,
  glueTakes,
} from "@/db/schema";
import {
  loadResolvedGlueJunctions,
  loadResolvedGlueJunctionsForList,
  glueTakeSelectionError,
  resolveGlueJunctions,
  takeRowFromDb,
  type GlueJobRow,
  type GlueSelectionErrorCode,
  type GlueTakeRow,
  type ResolvedGlueJunction,
} from "@/lib/glue/resolve";
import { readGlueQuota } from "@/lib/glue/quota";
import { logGlueEvent } from "@/lib/glue/log";
import {
  deriveGlueJunction,
  loadGlueItemRows,
  normalizeGlueBeats,
  type GlueJunctionDerivation,
  type GlueResolverItem,
} from "@/lib/glue/quote";
import { eq, and, desc, gt, sql, asc, ilike, or, inArray, isNull, isNotNull } from "drizzle-orm";
import { nanoid } from "nanoid";
import { SONGSET_MAX_DURATION_SECONDS, SONGSET_MAX_SONGS } from "@/lib/constants";
import {
  ARRANGEMENT_UNAVAILABLE_OR_STALE_MESSAGE,
  getItemDurationSeconds,
  getSongsetDurationSeconds,
  parseRecordingBeats,
  parseRecordingSections,
  parseSongArrangement,
  validateSongArrangement,
  type ArrangementItemInput,
  type ArrangementValidationCode,
  type RecordingSection,
  type SongArrangement,
} from "@/lib/arrangements";
import { sanitizeRenderErrorMessage } from "@/lib/render/error-message";
import { getEffectiveKey, type EffectiveKey } from "@/lib/music/effective-key";

export type RenderState = "unrendered" | "rendering" | "fresh" | "stale" | "failed";

/** Collect non-null recording themes in item order. */
function collectThemes(
  items: { recording?: { theme?: string | null } | null }[]
): string[] {
  return items
    .map((item) => item.recording?.theme)
    .filter((t): t is string => t != null);
}

export interface PublicSongsetItem {
  id: string;
  position: number;
  songTitle: string | null;
  composer: string | null;
  lyricist: string | null;
  albumName: string | null;
  songMusicalKey: string | null;
  durationSeconds: number | null;
  tempoBpm: number | null;
  recordingMusicalKey: string | null;
  effectiveKey: string | null;
  effectiveKeySource: EffectiveKey["source"];
  effectiveKeyStartRoot: string | null;
  effectiveKeyEndRoot: string | null;
  effectiveKeyMode: EffectiveKey["mode"];
  effectiveKeyStartPitchClass: number | null;
  effectiveKeyEndPitchClass: number | null;
  keyWarning: EffectiveKey["warning"];
  theme: string | null;
}

export interface SongsetPublicView {
  id: string;
  name: string;
  description: string | null;
  updatedAt: Date;
  totalDurationSeconds: number | null;
  renderState: RenderState;
  latestRenderJobId: string | null;
  lastCompletedRenderJobId: string | null;
  items: PublicSongsetItem[];
}

export async function getSongsetPublicView(songsetId: string): Promise<SongsetPublicView | null> {
  const songset = await db.query.songsets.findFirst({
    where: eq(songsets.id, songsetId),
  });

  if (!songset) return null;

  const items = await db
    .select({
      id: songsetItems.id,
      position: songsetItems.position,
      songTitle: songs.title,
      composer: songs.composer,
      lyricist: songs.lyricist,
      albumName: songs.albumName,
      songMusicalKey: songs.musicalKey,
      arrangementSteps: songsetItems.arrangementSteps,
      gapBeats: songsetItems.gapBeats,
      crossfadeEnabled: songsetItems.crossfadeEnabled,
      crossfadeDurationSeconds: songsetItems.crossfadeDurationSeconds,
      durationSeconds: recordings.durationSeconds,
      tempoBpm: recordings.tempoBpm,
      recordingSections: recordings.sections,
      recordingBeats: recordings.beats,
      recordingMusicalKey: recordings.musicalKey,
      recordingMusicalMode: recordings.musicalMode,
      keyConfidence: recordings.keyConfidence,
      keyScoreMargin: recordings.keyScoreMargin,
      keyWindowAgreement: recordings.keyWindowAgreement,
      recordingDeletedAt: recordings.deletedAt,
      recordingTheme: recordings.theme,
    })
    .from(songsetItems)
    .leftJoin(songs, eq(songsetItems.songId, songs.id))
    .leftJoin(recordings, eq(songsetItems.recordingHashPrefix, recordings.hashPrefix))
    .where(eq(songsetItems.songsetId, songsetId))
    .orderBy(songsetItems.position);

  // Soft-deleted recordings are invisible everywhere, including the total
  // (pre-existing bug fixed here: the reduce used to run over `items`).
  const visibleItems = items.filter((item) => !item.recordingDeletedAt);

  const publicItems: PublicSongsetItem[] = visibleItems.map((item) => ({
    id: item.id,
    position: item.position,
    songTitle: item.songTitle,
    composer: item.composer,
    lyricist: item.lyricist,
    albumName: item.albumName,
    songMusicalKey: item.songMusicalKey,
    durationSeconds: getItemDurationSeconds(toArrangementInput(item)),
    tempoBpm: item.tempoBpm,
    recordingMusicalKey: item.recordingMusicalKey,
    ...effectiveKeyFields(item),
    theme: item.recordingTheme,
  }));

  const totalDurationSeconds =
    getSongsetDurationSeconds(visibleItems.map(toArrangementInput)) || null;

  const renderState = await computeRenderState(songsetId);

  return {
    id: songset.id,
    name: songset.name,
    description: songset.description,
    updatedAt: songset.updatedAt,
    totalDurationSeconds,
    renderState,
    latestRenderJobId: songset.latestRenderJobId,
    lastCompletedRenderJobId: songset.lastCompletedRenderJobId,
    items: publicItems,
  };
}

export interface SongsetContainingSong {
  id: string;
  name: string;
  description: string | null;
  updatedAt: Date;
  itemCount: number;
  /** How many Items in this Songset are the Song (>= 1). */
  occurrenceCount: number;
  /** Zero-based positions of every occurrence, ascending. */
  positions: number[];
  isOrigin: boolean;
  owner: {
    id: number;
    name: string;
  };
}

/**
 * Finds all songsets owned by `userId` that contain the given `songId`, one row
 * per Songset with the number of occurrences and their positions. When
 * `originSongsetId` is provided, that songset is sorted first.
 */
export async function findSongsetsContainingSong(
  songId: string,
  userId: number,
  originSongsetId?: string | null
): Promise<SongsetContainingSong[]> {
  return timePageLoad("findSongsetsContainingSong", async () => {
    const itemCountSubquery = db
      .$with("item_counts")
      .as(
        db
          .select({
            songsetId: songsetItems.songsetId,
            count: sql<number>`count(${songsetItems.id})::int`.as("count"),
          })
          .from(songsetItems)
          .innerJoin(songsets, eq(songsets.id, songsetItems.songsetId))
          .where(eq(songsets.userId, userId))
          .groupBy(songsetItems.songsetId)
      );

    const rows = await db
      .with(itemCountSubquery)
      .select({
        id: songsets.id,
        name: songsets.name,
        description: songsets.description,
        updatedAt: songsets.updatedAt,
        itemCount: itemCountSubquery.count,
        occurrenceCount: sql<number>`count(${songsetItems.id})::int`,
        positions: sql<number[]>`array_agg(${songsetItems.position} order by ${songsetItems.position})`,
        ownerId: users.id,
        ownerName: users.name,
      })
      .from(songsets)
      .innerJoin(songsetItems, eq(songsetItems.songsetId, songsets.id))
      .innerJoin(users, eq(users.id, songsets.userId))
      .leftJoin(itemCountSubquery, eq(itemCountSubquery.songsetId, songsets.id))
      .where(
        and(
          eq(songsets.userId, userId),
          eq(songsetItems.songId, songId)
        )
      )
      .groupBy(songsets.id, users.id, users.name, itemCountSubquery.count)
      .orderBy(desc(songsets.updatedAt))
      .limit(50);

    const sorted = [...rows].sort((a, b) => {
      if (originSongsetId) {
        if (a.id === originSongsetId && b.id !== originSongsetId) return -1;
        if (b.id === originSongsetId && a.id !== originSongsetId) return 1;
      }
      return b.updatedAt.getTime() - a.updatedAt.getTime();
    });

    return sorted.map((row) => ({
      id: row.id,
      name: row.name,
      description: row.description,
      updatedAt: row.updatedAt,
      itemCount: Number(row.itemCount ?? 0),
      occurrenceCount: Number(row.occurrenceCount ?? 0),
      positions: row.positions ?? [],
      isOrigin: row.id === originSongsetId,
      owner: {
        id: row.ownerId,
        name: row.ownerName ?? "Unknown",
      },
    }));
  });
}

export interface SongsetListItem {
  id: string;
  name: string;
  description: string | null;
  createdAt: Date;
  updatedAt: Date;
  renderState: RenderState;
  itemCount: number;
  durationSeconds: number | null;
  latestRenderJobId: string | null;
  lastFailedRenderJobId: string | null;
  lastCompletedRenderJobId: string | null;
  renderErrorMessage: string | null;
  failedAt: Date | null;
  themes: string[];
}

/** SongsetListItem plus list-only fields (listSongsetSummaries). */
export interface SongsetListSummary extends SongsetListItem {
  /** The last COMPLETED render produced an MP4 (audio-only renders don't). */
  lastCompletedRenderHasVideo: boolean;
}

export interface SongsetItemRecording {
  contentHash: string;
  durationSeconds: number | null;
  tempoBpm: number | null;
  musicalKey: string | null;
  effectiveKey: string | null;
  effectiveKeySource: EffectiveKey["source"];
  effectiveKeyStartRoot: string | null;
  effectiveKeyEndRoot: string | null;
  effectiveKeyMode: EffectiveKey["mode"];
  effectiveKeyStartPitchClass: number | null;
  effectiveKeyEndPitchClass: number | null;
  r2AudioUrl: string | null;
  theme: string | null;
  /** Parsed analysis; NULL means the Recording has no readable sections. */
  sections: RecordingSection[] | null;
  /** Parsed beats; empty means analysis is unavailable (spec §1.2). */
  beats: number[];
}

export interface SongsetItemDetail {
  id: string;
  songId: string;
  recordingHashPrefix: string | null;
  position: number;
  gapBeats: number | null;
  crossfadeEnabled: number | null;
  crossfadeDurationSeconds: number | null;
  keyShiftSemitones: number | null;
  tempoRatio: number | null;
  /** Row timestamp; AI-transition preconditions compare against it. */
  updatedAt: string;
  /** Outgoing AI transition switch. */
  glueEnabled: boolean;
  glueBeats: 2 | 4 | 8 | 16;
  /** The outgoing AI transition (spec §5.1), always present. */
  glue: ResolvedGlueJunction;
  /** Effective outgoing take id, or null when the junction is not ready. */
  glueTakeId: string | null;
  /** Actual staged seconds of that take, or null. */
  glueSeconds: number | null;
  /**
   * Raw persisted arrangement JSON. Kept verbatim even when malformed so
   * diagnostics and duplication never lose data (spec §2.2).
   */
  arrangementSteps: string | null;
  /** Parsed arrangement; NULL when absent or structurally invalid. */
  arrangement: SongArrangement | null;
  /** True when `arrangementSteps` is present but not the persisted schema. */
  arrangementInvalid: boolean;
  markedLineCount: number;
  song: {
    id: string;
    title: string;
    composer: string | null;
    lyricist: string | null;
    albumName: string | null;
    musicalKey: string | null;
    effectiveKey: string | null;
    effectiveKeySource: EffectiveKey["source"];
    effectiveKeyStartRoot: string | null;
    effectiveKeyEndRoot: string | null;
    effectiveKeyMode: EffectiveKey["mode"];
    effectiveKeyStartPitchClass: number | null;
    effectiveKeyEndPitchClass: number | null;
    keyWarning: EffectiveKey["warning"];
  } | null;
  recording: SongsetItemRecording | null;
}

export interface SongsetDetail extends SongsetListItem {
  items: SongsetItemDetail[];
  /** Items whose Recording is soft-deleted and therefore filtered from `items`. */
  deletedItemCount: number;
}

/**
 * `getSongsetEditorData` payload. The editor alone needs the in-flight render's
 * estimate, to show "Rendering · ~N minutes" without a second round trip.
 */
export interface SongsetEditorData extends SongsetDetail {
  latestRenderJobEstimatedTotalSeconds: number | null;
  /** One resolved outgoing Junction per item, in position order. */
  glue: ResolvedGlueJunction[];
}

export interface RenderJobSummary {
  id: string;
  status: string;
  createdAt: Date | null;
  elapsedSeconds: number | null;
  estimatedTotalSeconds: number | null;
  template: string;
  resolution: string;
  audioEnabled: boolean;
  videoEnabled: boolean;
  fontFamily: string;
  fontSizePreset: string;
  includeTitleCard: boolean;
  titleCardDurationSeconds: number | null;
  titleCardLines: string[] | null;
  mp3R2Key: string | null;
  mp4R2Key: string | null;
  chaptersR2Key: string | null;
}

export interface RenderPageData {
  songset: {
    id: string;
    name: string;
    description: string | null;
    markedLineCount: number;
    renderState: RenderState;
    /** One entry per visible Item: the web preview dedupes by Song id. */
    songs: { id: string; title: string }[];
    lastCompletedRenderJobId: string | null;
    durationSeconds: number | null;
  };
  userSettings: {
    defaultVideoTemplate: string;
    defaultResolution: string;
    defaultFontSizePreset: string;
    defaultFontFamily: string;
  } | null;
  latestJob: RenderJobSummary | null;
  previousCompletedJob: RenderJobSummary | null;
  /** Blocking AI-transition preflight; non-null disables the render form. */
  gluePreflight?: GlueRenderPreflight | null;
}

function effectiveKeyFields(input: {
  songMusicalKey?: string | null;
  recordingMusicalKey?: string | null;
  recordingMusicalMode?: string | null;
  keyConfidence?: number | null;
  keyScoreMargin?: number | null;
  keyWindowAgreement?: number | null;
}) {
  const effective = getEffectiveKey({
    catalogKey: input.songMusicalKey,
    detectedKey: input.recordingMusicalKey,
    detectedMode: input.recordingMusicalMode,
    detectedConfidence: input.keyConfidence,
    detectedMargin: input.keyScoreMargin,
    detectedWindowAgreement: input.keyWindowAgreement,
  });

  return {
    effectiveKey: effective.display,
    effectiveKeySource: effective.source,
    effectiveKeyStartRoot: effective.startRoot,
    effectiveKeyEndRoot: effective.endRoot,
    effectiveKeyMode: effective.mode,
    effectiveKeyStartPitchClass: effective.startPitchClass,
    effectiveKeyEndPitchClass: effective.endPitchClass,
    keyWarning: effective.warning,
  };
}

/**
 * Raw persisted arrangement JSON plus its parsed form (spec §2.2). SQL NULL
 * means "original full Recording"; malformed JSON stays verbatim so it can be
 * repaired or duplicated, never silently dropped.
 */
function arrangementFieldsFromRaw(
  raw: string | null | undefined
): Pick<SongsetItemDetail, "arrangementSteps" | "arrangement" | "arrangementInvalid"> {
  const arrangementSteps = raw ?? null;
  const parsed = parseSongArrangement(arrangementSteps);
  if (!parsed.present) {
    return { arrangementSteps: null, arrangement: null, arrangementInvalid: false };
  }
  if ("invalid" in parsed) {
    return { arrangementSteps, arrangement: null, arrangementInvalid: true };
  }
  return { arrangementSteps, arrangement: parsed.arrangement, arrangementInvalid: false };
}

/** Recording analysis exposed through the editor detail shape. */
function recordingAnalysisFields(
  sectionsRaw: string | null | undefined,
  beatsRaw: string | null | undefined
): Pick<SongsetItemRecording, "sections" | "beats"> {
  return {
    sections: parseRecordingSections(sectionsRaw ?? null),
    beats: parseRecordingBeats(beatsRaw ?? null),
  };
}

/** Row shape shared by the three relational `songsetItems` queries. */
type RelationalSongsetItem = typeof songsetItems.$inferSelect & {
  song: typeof songs.$inferSelect | null;
  recording:
    | (typeof recordings.$inferSelect & { lyricMarks: { id: string }[] })
    | null;
};

/** Map a relational item row to the editor detail shape. */
function mapRelationalSongsetItem(
  item: RelationalSongsetItem,
  glue: ResolvedGlueJunction
): SongsetItemDetail {
  const keyFields = effectiveKeyFields({
    songMusicalKey: item.song?.musicalKey,
    recordingMusicalKey: item.recording?.musicalKey,
    recordingMusicalMode: item.recording?.musicalMode,
    keyConfidence: item.recording?.keyConfidence,
    keyScoreMargin: item.recording?.keyScoreMargin,
    keyWindowAgreement: item.recording?.keyWindowAgreement,
  });

  return {
    id: item.id,
    songId: item.songId,
    recordingHashPrefix: item.recordingHashPrefix,
    position: item.position,
    gapBeats: item.gapBeats ?? null,
    crossfadeEnabled: item.crossfadeEnabled ?? null,
    crossfadeDurationSeconds: item.crossfadeDurationSeconds ?? null,
    keyShiftSemitones: item.keyShiftSemitones ?? null,
    tempoRatio: item.tempoRatio ?? null,
    updatedAt: item.updatedAt.toISOString(),
    glueEnabled: item.glueEnabled,
    glueBeats: item.glueBeats as 2 | 4 | 8 | 16,
    glue,
    glueTakeId: glue.selectedTake?.id ?? null,
    glueSeconds: glue.selectedTake?.seconds ?? null,
    ...arrangementFieldsFromRaw(item.arrangementSteps),
    markedLineCount: item.recording?.lyricMarks.length ?? 0,
    song: item.song
      ? {
          id: item.song.id,
          title: item.song.title,
          composer: item.song.composer,
          lyricist: item.song.lyricist,
          albumName: item.song.albumName,
          musicalKey: item.song.musicalKey,
          ...keyFields,
        }
      : null,
    recording: item.recording
      ? {
          contentHash: item.recording.contentHash,
          durationSeconds: item.recording.durationSeconds,
          tempoBpm: item.recording.tempoBpm,
          musicalKey: item.recording.musicalKey,
          ...keyFields,
          r2AudioUrl: item.recording.r2AudioUrl,
          theme: item.recording.theme,
          ...recordingAnalysisFields(item.recording.sections, item.recording.beats),
        }
      : null,
  };
}

/** Duration-bearing view of an item for the canonical duration helpers. */
function toArrangementInput(item: {
  arrangementSteps: string | null;
  gapBeats: number | null;
  crossfadeEnabled: number | null;
  crossfadeDurationSeconds: number | null;
  tempoBpm?: number | null;
  durationSeconds?: number | null;
  glueTakeId?: string | null;
  glueSeconds?: number | null;
}): ArrangementItemInput {
  return {
    arrangementSteps: item.arrangementSteps,
    gapBeats: item.gapBeats,
    crossfadeEnabled: item.crossfadeEnabled,
    crossfadeDurationSeconds: item.crossfadeDurationSeconds,
    tempoBpm: item.tempoBpm ?? null,
    recordingDurationSeconds: item.durationSeconds ?? null,
    glueTakeId: item.glueTakeId ?? null,
    glueSeconds: item.glueSeconds ?? null,
  };
}

function requireJunction(
  byItemId: ReadonlyMap<string, ResolvedGlueJunction>,
  itemId: string
): ResolvedGlueJunction {
  const glue = byItemId.get(itemId);
  if (glue) return glue;
  // A resolver miss means a join/projection did not line up for this Item.
  // Degrade to a disabled Junction instead of 500ing the editor, list or
  // render page: the server resolver stays the only source of take state.
  const known = byItemId.values().next().value;
  return {
    fromItemId: itemId,
    toItemId: null,
    glueEnabled: false,
    glueBeats: known?.glueBeats ?? 4,
    descriptorKey: null,
    state: "disabled",
    selectedTake: null,
    availableTakes: [],
    activeJob: null,
    quote: null,
    quotaLimit: known?.quotaLimit ?? 5,
    quotaUsed: known?.quotaUsed ?? 0,
    quotaRemaining: known?.quotaRemaining ?? 5,
    quotaResetAt: known?.quotaResetAt ?? null,
    expectedFromUpdatedAt: null,
    expectedToUpdatedAt: null,
    unavailableReason: null,
  };
}

async function timePageLoad<T>(label: string, fn: () => Promise<T>): Promise<T> {
  if (process.env.SOW_WEBAPP_TIMING !== "1") return fn();
  const startedAt = performance.now();
  try {
    return await fn();
  } finally {
    const elapsedMs = Math.round(performance.now() - startedAt);
    console.info(`[page-load] ${label} ${elapsedMs}ms`);
  }
}

/**
 * Derives the render state from snapshot fields already present on the songsets row
 * and the joined latest render job.
 *
 * Note: The "newer render job" staleness check from `computeRenderState` is intentionally
 * omitted here. That check looked for render jobs created after the latest job's completion,
 * but `latestRenderJobId` on the songsets row is updated whenever a new job is created, so
 * any newer job would already be the latest and caught by the "rendering" status check.
 * Do not re-add this check.
 */
export function mapRenderStateFromSnapshot(input: {
  latestRenderJobId: string | null;
  lastFailedRenderJobId: string | null;
  latestJobStatus: string | null;
  latestJobCompletedAt: Date | null;
  latestItemUpdatedAt?: Date | null;
}): RenderState {
  if (!input.latestRenderJobId || !input.latestJobStatus) return "unrendered";
  if (input.latestJobStatus === "queued" || input.latestJobStatus === "running") return "rendering";
  if (
    input.latestJobStatus === "failed" ||
    input.lastFailedRenderJobId === input.latestRenderJobId
  ) {
    return "failed";
  }
  if (input.latestJobStatus === "completed") {
    if (
      input.latestJobCompletedAt &&
      input.latestItemUpdatedAt &&
      input.latestItemUpdatedAt > input.latestJobCompletedAt
    ) {
      return "stale";
    }
    return "fresh";
  }
  return "unrendered";
}

function parseTitleCardLines(value: string | null): string[] | null {
  if (!value) return null;
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) && parsed.length > 0 ? parsed : null;
  } catch {
    return null;
  }
}

function mapRenderJobSummary(row: typeof renderJobs.$inferSelect | null | undefined): RenderJobSummary | null {
  if (!row) return null;
  return {
    id: row.id,
    status: row.status,
    createdAt: row.createdAt,
    elapsedSeconds: row.elapsedSeconds,
    estimatedTotalSeconds: row.estimatedTotalSeconds,
    template: row.template,
    resolution: row.resolution,
    audioEnabled: row.audioEnabled ?? true,
    videoEnabled: row.videoEnabled ?? true,
    fontFamily: row.fontFamily,
    fontSizePreset: row.fontSizePreset,
    includeTitleCard: row.includeTitleCard ?? false,
    titleCardDurationSeconds: row.titleCardDurationSeconds,
    titleCardLines: parseTitleCardLines(row.titleCardLines),
    mp3R2Key: row.mp3R2Key,
    mp4R2Key: row.mp4R2Key,
    chaptersR2Key: row.chaptersR2Key,
  };
}

/** One visible item's duration inputs inside the summaries JSON aggregate. */
interface SummaryItemDurationInput {
  itemId: string;
  glueEnabled: boolean;
  arrangementSteps: string | null;
  durationSeconds: number | null;
  gapBeats: number | null;
  crossfadeEnabled: number | null;
  crossfadeDurationSeconds: number | null;
  tempoBpm: number | null;
}

export async function listSongsetSummaries(
  userId: number,
  limit = 50,
  offset = 0,
  search?: string
): Promise<{ songsets: SongsetListSummary[]; total: number }> {
  return timePageLoad("listSongsetSummaries", async () => {
    const trimmedSearch = search?.trim();
    const whereCondition = trimmedSearch
      ? and(
          eq(songsets.userId, userId),
          or(
            ilike(songsets.name, `%${trimmedSearch}%`),
            ilike(songsets.description, `%${trimmedSearch}%`),
            // Fuzzy search across song metadata of any song in the songset:
            // title, title_pinyin, composer, lyricist, album_name, album_series.
            // Correlated EXISTS avoids row inflation so the groupBy/aggregates
            // above are unaffected; the same condition feeds the count query.
            sql<boolean>`exists (
              select 1
              from ${songsetItems}
              join ${songs} on ${songs.id} = ${songsetItems.songId}
              where ${songsetItems.songsetId} = ${songsets.id}
                and (
                  ${ilike(songs.title, `%${trimmedSearch}%`)}
                  or ${ilike(songs.titlePinyin, `%${trimmedSearch}%`)}
                  or ${ilike(songs.composer, `%${trimmedSearch}%`)}
                  or ${ilike(songs.lyricist, `%${trimmedSearch}%`)}
                  or ${ilike(songs.albumName, `%${trimmedSearch}%`)}
                  or ${ilike(songs.albumSeries, `%${trimmedSearch}%`)}
                )
            )`
          )
        )
      : eq(songsets.userId, userId);

    const rows = await db
      .select({
        id: songsets.id,
        name: songsets.name,
        description: songsets.description,
        createdAt: songsets.createdAt,
        updatedAt: songsets.updatedAt,
        latestRenderJobId: songsets.latestRenderJobId,
        lastFailedRenderJobId: songsets.lastFailedRenderJobId,
        lastCompletedRenderJobId: songsets.lastCompletedRenderJobId,
        itemCount: sql<number>`count(${songsetItems.id}) filter (where ${recordings.deletedAt} is null)::int`,
        // Per-visible-item duration inputs aggregated as JSON, then totaled in
        // TypeScript (spec §2.6): arrangement_steps stays a JSON *string*
        // inside json_build_object, so malformed data can never break the list
        // query, and a structurally invalid arrangement yields null.
        itemDurationInputs: sql<SummaryItemDurationInput[] | null>`json_agg(
          json_build_object(
            'itemId', ${songsetItems.id},
            'glueEnabled', ${songsetItems.glueEnabled},
            'arrangementSteps', ${songsetItems.arrangementSteps},
            'durationSeconds', ${recordings.durationSeconds},
            'gapBeats', ${songsetItems.gapBeats},
            'crossfadeEnabled', ${songsetItems.crossfadeEnabled},
            'crossfadeDurationSeconds', ${songsetItems.crossfadeDurationSeconds},
            'tempoBpm', ${recordings.tempoBpm}
          ) order by ${songsetItems.position}
        ) filter (where ${recordings.deletedAt} is null)`,
        latestItemUpdatedAt: sql<Date | null>`max(${songsetItems.updatedAt}) filter (where ${recordings.deletedAt} is null)`,
        latestJobStatus: renderJobs.status,
        latestJobCompletedAt: renderJobs.completedAt,
        renderErrorMessage: sql<string | null>`left(${renderJobs.errorMessage}, 4000)`,
        latestJobStartedAt: renderJobs.startedAt,
        latestJobCreatedAt: renderJobs.createdAt,
        // Correlated EXISTS avoids another renderJobs join (which would need
        // aliasing + groupBy changes); reads the completed job's own
        // videoEnabled/mp4R2Key rather than the latest job's.
        lastCompletedRenderHasVideo: sql<boolean>`exists (
          select 1
          from ${renderJobs} as completed_job
          where completed_job.id = ${songsets.lastCompletedRenderJobId}
            and completed_job.video_enabled = true
            and completed_job.mp4_r2_key is not null
        )`,
        themes: sql<string[]>`array_agg(${recordings.theme} order by ${songsetItems.position}) filter (where ${recordings.deletedAt} is null and ${recordings.theme} is not null)`,
      })
      .from(songsets)
      .leftJoin(songsetItems, eq(songsetItems.songsetId, songsets.id))
      .leftJoin(recordings, eq(songsetItems.recordingHashPrefix, recordings.hashPrefix))
      .leftJoin(renderJobs, eq(renderJobs.id, songsets.latestRenderJobId))
      .where(whereCondition)
      .groupBy(
        songsets.id,
        renderJobs.id
      )
      .orderBy(desc(songsets.updatedAt))
      .limit(limit)
      .offset(offset);

    const countResult = await db
      .select({ count: sql<number>`count(*)::int` })
      .from(songsets)
      .where(whereCondition);

    const glueBySongset = await loadResolvedGlueJunctionsForList(
      rows.map((row) => row.id),
      userId
    );

    return {
      total: Number(countResult[0]?.count ?? 0),
      songsets: rows.map((row) => {
        const renderState = mapRenderStateFromSnapshot({
          latestRenderJobId: row.latestRenderJobId,
          lastFailedRenderJobId: row.lastFailedRenderJobId,
          latestJobStatus: row.latestJobStatus,
          latestJobCompletedAt: row.latestJobCompletedAt,
          latestItemUpdatedAt: row.latestItemUpdatedAt,
        });
        const failedAt = row.latestJobStartedAt ?? row.latestJobCreatedAt;
        return {
          id: row.id,
          name: row.name,
          description: row.description,
          createdAt: row.createdAt,
          updatedAt: row.updatedAt,
          latestRenderJobId: row.latestRenderJobId,
          lastFailedRenderJobId: row.lastFailedRenderJobId,
          lastCompletedRenderJobId: row.lastCompletedRenderJobId,
          itemCount: Number(row.itemCount ?? 0),
          durationSeconds: row.itemDurationInputs
            ? getSongsetDurationSeconds(
                row.itemDurationInputs.map((input) => {
                  const glue = glueBySongset.get(row.id)?.get(input.itemId);
                  return toArrangementInput({
                    arrangementSteps: input.arrangementSteps,
                    gapBeats: input.gapBeats,
                    crossfadeEnabled: input.crossfadeEnabled,
                    crossfadeDurationSeconds: input.crossfadeDurationSeconds,
                    tempoBpm: input.tempoBpm,
                    durationSeconds: input.durationSeconds,
                    glueTakeId: glue?.selectedTake?.id ?? null,
                    glueSeconds: glue?.selectedTake?.seconds ?? null,
                  });
                })
              )
            : null,
          renderState,
          renderErrorMessage:
            renderState === "failed" ? sanitizeRenderErrorMessage(row.renderErrorMessage) : null,
          failedAt: renderState === "failed" ? failedAt : null,
          themes: (row.themes ?? []).filter(Boolean),
          lastCompletedRenderHasVideo: Boolean(row.lastCompletedRenderHasVideo),
        };
      }),
    };
  });
}

export async function getSongsetEditorData(
  id: string,
  userId: number
): Promise<SongsetEditorData | null> {
  return timePageLoad("getSongsetEditorData", async () => {
    const [row] = await db
      .select({
        id: songsets.id,
        name: songsets.name,
        description: songsets.description,
        createdAt: songsets.createdAt,
        updatedAt: songsets.updatedAt,
        latestRenderJobId: songsets.latestRenderJobId,
        lastFailedRenderJobId: songsets.lastFailedRenderJobId,
        lastCompletedRenderJobId: songsets.lastCompletedRenderJobId,
        latestJobStatus: renderJobs.status,
        latestJobCompletedAt: renderJobs.completedAt,
        latestJobEstimatedTotalSeconds: renderJobs.estimatedTotalSeconds,
        renderErrorMessage: renderJobs.errorMessage,
        latestJobStartedAt: renderJobs.startedAt,
        latestJobCreatedAt: renderJobs.createdAt,
      })
      .from(songsets)
      .leftJoin(renderJobs, eq(renderJobs.id, songsets.latestRenderJobId))
      .where(and(eq(songsets.id, id), eq(songsets.userId, userId)))
      .limit(1);

    if (!row) return null;

    const itemRows = await db
      .select({
        id: songsetItems.id,
        songId: songsetItems.songId,
        recordingHashPrefix: songsetItems.recordingHashPrefix,
        position: songsetItems.position,
        gapBeats: songsetItems.gapBeats,
        crossfadeEnabled: songsetItems.crossfadeEnabled,
        crossfadeDurationSeconds: songsetItems.crossfadeDurationSeconds,
        keyShiftSemitones: songsetItems.keyShiftSemitones,
        tempoRatio: songsetItems.tempoRatio,
        arrangementSteps: songsetItems.arrangementSteps,
        updatedAt: songsetItems.updatedAt,
        glueEnabled: songsetItems.glueEnabled,
        glueBeats: songsetItems.glueBeats,
        songTitle: songs.title,
        composer: songs.composer,
        lyricist: songs.lyricist,
        albumName: songs.albumName,
        songMusicalKey: songs.musicalKey,
        recordingContentHash: recordings.contentHash,
        durationSeconds: recordings.durationSeconds,
        tempoBpm: recordings.tempoBpm,
        recordingSections: recordings.sections,
        recordingBeats: recordings.beats,
        recordingMusicalKey: recordings.musicalKey,
        recordingMusicalMode: recordings.musicalMode,
        keyConfidence: recordings.keyConfidence,
        keyScoreMargin: recordings.keyScoreMargin,
        keyWindowAgreement: recordings.keyWindowAgreement,
        r2AudioUrl: recordings.r2AudioUrl,
        recordingTheme: recordings.theme,
        recordingDeletedAt: recordings.deletedAt,
        markedLineCount: sql<number>`count(distinct ${lyricMarks.id})::int`,
      })
      .from(songsetItems)
      .leftJoin(songs, eq(songsetItems.songId, songs.id))
      .leftJoin(recordings, eq(songsetItems.recordingHashPrefix, recordings.hashPrefix))
      .leftJoin(
        lyricMarks,
        and(
          eq(lyricMarks.userId, userId),
          eq(lyricMarks.recordingContentHash, recordings.contentHash)
        )
      )
      .where(eq(songsetItems.songsetId, id))
      .groupBy(
        songsetItems.id,
        songsetItems.songId,
        songsetItems.recordingHashPrefix,
        songsetItems.position,
        songsetItems.gapBeats,
        songsetItems.crossfadeEnabled,
        songsetItems.crossfadeDurationSeconds,
        songsetItems.keyShiftSemitones,
        songsetItems.tempoRatio,
        songsetItems.arrangementSteps,
        songsetItems.updatedAt,
        songs.id,
        songs.title,
        songs.composer,
        songs.lyricist,
        songs.albumName,
        songs.musicalKey,
        recordings.contentHash,
        recordings.durationSeconds,
        recordings.tempoBpm,
        recordings.sections,
        recordings.beats,
        recordings.musicalKey,
        recordings.musicalMode,
        recordings.keyConfidence,
        recordings.keyScoreMargin,
        recordings.keyWindowAgreement,
        recordings.r2AudioUrl,
        recordings.theme,
        recordings.deletedAt
      )
      .orderBy(asc(songsetItems.position));

    const visibleItemRows = itemRows.filter((item) => !item.recordingDeletedAt);
    const glueJunctions = await loadResolvedGlueJunctions(id, userId);
    const glueByItemId = new Map(
      glueJunctions.map((junction) => [junction.fromItemId, junction] as const)
    );
    const latestItemUpdatedAt = visibleItemRows.reduce<Date | null>((latest, item) => {
      if (!latest || item.updatedAt > latest) return item.updatedAt;
      return latest;
    }, null);

    const items: SongsetItemDetail[] = visibleItemRows.map((item) => ({
      id: item.id,
      songId: item.songId,
      recordingHashPrefix: item.recordingHashPrefix,
      position: item.position,
      gapBeats: item.gapBeats ?? null,
      crossfadeEnabled: item.crossfadeEnabled ?? null,
      crossfadeDurationSeconds: item.crossfadeDurationSeconds ?? null,
      keyShiftSemitones: item.keyShiftSemitones ?? null,
      tempoRatio: item.tempoRatio ?? null,
      updatedAt: item.updatedAt.toISOString(),
      glueEnabled: item.glueEnabled,
      glueBeats: item.glueBeats as 2 | 4 | 8 | 16,
      glue: requireJunction(glueByItemId, item.id),
      glueTakeId: requireJunction(glueByItemId, item.id).selectedTake?.id ?? null,
      glueSeconds: requireJunction(glueByItemId, item.id).selectedTake?.seconds ?? null,
      ...arrangementFieldsFromRaw(item.arrangementSteps),
      markedLineCount: Number(item.markedLineCount ?? 0),
      song: item.songTitle
        ? {
            id: item.songId,
            title: item.songTitle,
            composer: item.composer,
            lyricist: item.lyricist,
            albumName: item.albumName,
            musicalKey: item.songMusicalKey,
            ...effectiveKeyFields(item),
          }
        : null,
      recording: item.recordingContentHash
        ? {
            contentHash: item.recordingContentHash,
            durationSeconds: item.durationSeconds,
            tempoBpm: item.tempoBpm,
            musicalKey: item.recordingMusicalKey,
            ...effectiveKeyFields(item),
            r2AudioUrl: item.r2AudioUrl,
            theme: item.recordingTheme,
            ...recordingAnalysisFields(item.recordingSections, item.recordingBeats),
          }
        : null,
    }));

    const renderState = mapRenderStateFromSnapshot({
      latestRenderJobId: row.latestRenderJobId,
      lastFailedRenderJobId: row.lastFailedRenderJobId,
      latestJobStatus: row.latestJobStatus,
      latestJobCompletedAt: row.latestJobCompletedAt,
      latestItemUpdatedAt,
    });
    const failedAt = row.latestJobStartedAt ?? row.latestJobCreatedAt;

    return {
      id: row.id,
      name: row.name,
      description: row.description,
      createdAt: row.createdAt,
      updatedAt: row.updatedAt,
      latestRenderJobId: row.latestRenderJobId,
      lastFailedRenderJobId: row.lastFailedRenderJobId,
      lastCompletedRenderJobId: row.lastCompletedRenderJobId,
      itemCount: items.length,
      deletedItemCount: itemRows.length - visibleItemRows.length,
      durationSeconds: getSongsetDurationSeconds(
        items.map((item) =>
          toArrangementInput({
            arrangementSteps: item.arrangementSteps,
            gapBeats: item.gapBeats,
            crossfadeEnabled: item.crossfadeEnabled,
            crossfadeDurationSeconds: item.crossfadeDurationSeconds,
            tempoBpm: item.recording?.tempoBpm ?? null,
            durationSeconds: item.recording?.durationSeconds ?? null,
            glueTakeId: item.glueTakeId,
            glueSeconds: item.glueSeconds,
          })
        )
      ),
      renderState,
      latestRenderJobEstimatedTotalSeconds: row.latestJobEstimatedTotalSeconds ?? null,
      renderErrorMessage:
        renderState === "failed" ? sanitizeRenderErrorMessage(row.renderErrorMessage) : null,
      failedAt: renderState === "failed" ? failedAt : null,
      themes: collectThemes(items),
      items,
      glue: glueJunctions,
    };
  });
}

export async function getRenderPageData(
  id: string,
  userId: number
): Promise<RenderPageData | null> {
  return timePageLoad("getRenderPageData", async () => {
    const [row] = await db
      .select({
        id: songsets.id,
        name: songsets.name,
        description: songsets.description,
        latestRenderJobId: songsets.latestRenderJobId,
        lastFailedRenderJobId: songsets.lastFailedRenderJobId,
        lastCompletedRenderJobId: songsets.lastCompletedRenderJobId,
        latestJobStatus: renderJobs.status,
        latestJobCompletedAt: renderJobs.completedAt,
      })
      .from(songsets)
      .leftJoin(renderJobs, eq(renderJobs.id, songsets.latestRenderJobId))
      .where(and(eq(songsets.id, id), eq(songsets.userId, userId)))
      .limit(1);

    if (!row) return null;

    const itemRows = await db
      .select({
        id: songsetItems.id,
        position: songsetItems.position,
        songId: songsetItems.songId,
        songTitle: songs.title,
        markedLineCount: sql<number>`count(distinct ${lyricMarks.id})::int`,
        recordingDeletedAt: recordings.deletedAt,
        recordingDurationSeconds: recordings.durationSeconds,
        arrangementSteps: songsetItems.arrangementSteps,
        gapBeats: songsetItems.gapBeats,
        crossfadeEnabled: songsetItems.crossfadeEnabled,
        crossfadeDurationSeconds: songsetItems.crossfadeDurationSeconds,
        recordingSections: recordings.sections,
        recordingBeats: recordings.beats,
        tempoBpm: recordings.tempoBpm,
        updatedAt: songsetItems.updatedAt,
      })
      .from(songsetItems)
      .leftJoin(songs, eq(songsetItems.songId, songs.id))
      .leftJoin(recordings, eq(songsetItems.recordingHashPrefix, recordings.hashPrefix))
      .leftJoin(
        lyricMarks,
        and(
          eq(lyricMarks.userId, userId),
          eq(lyricMarks.recordingContentHash, recordings.contentHash)
        )
      )
      .where(eq(songsetItems.songsetId, id))
      .groupBy(
        songsetItems.id,
        songsetItems.position,
        songsetItems.songId,
        songs.title,
        recordings.deletedAt,
        recordings.durationSeconds,
        songsetItems.arrangementSteps,
        songsetItems.gapBeats,
        songsetItems.crossfadeEnabled,
        songsetItems.crossfadeDurationSeconds,
        recordings.sections,
        recordings.beats,
        recordings.tempoBpm,
        songsetItems.updatedAt
      )
      .orderBy(asc(songsetItems.position));

    const visibleItems = itemRows.filter((item) => !item.recordingDeletedAt);
    const glueJunctions = await loadResolvedGlueJunctions(id, userId);
    const glueByItemId = new Map(
      glueJunctions.map((junction) => [junction.fromItemId, junction] as const)
    );
    const gluePreflight = glueJunctions.some(
      (junction) => junction.glueEnabled && junction.toItemId !== null
    )
      ? buildGlueRenderPreflight({
          junctions: glueJunctions,
          items: visibleItems.map((item) => ({
            id: item.id,
            position: item.position,
            title: item.songTitle ?? null,
          })),
          jobs: await loadGlueRenderJobFacts(id, userId),
        })
      : null;
    if (gluePreflight) reportGlueRenderPreflight(id, userId, gluePreflight);
    const latestItemUpdatedAt = visibleItems.reduce<Date | null>((latest, item) => {
      if (!latest || item.updatedAt > latest) return item.updatedAt;
      return latest;
    }, null);

    const renderState = mapRenderStateFromSnapshot({
      latestRenderJobId: row.latestRenderJobId,
      lastFailedRenderJobId: row.lastFailedRenderJobId,
      latestJobStatus: row.latestJobStatus,
      latestJobCompletedAt: row.latestJobCompletedAt,
      latestItemUpdatedAt,
    });

    const [settingsRow, latestJobRow, previousCompletedJobRow] = await Promise.all([
      db.query.userSettings.findFirst({
        where: eq(userSettings.userId, userId),
      }),
      row.latestRenderJobId
        ? db.query.renderJobs.findFirst({
            where: and(eq(renderJobs.id, row.latestRenderJobId), eq(renderJobs.userId, userId)),
          })
        : Promise.resolve(null),
      row.lastCompletedRenderJobId
        ? db.query.renderJobs.findFirst({
            where: and(
              eq(renderJobs.id, row.lastCompletedRenderJobId),
              eq(renderJobs.userId, userId)
            ),
          })
        : Promise.resolve(null),
    ]);

    return {
      songset: {
        id: row.id,
        name: row.name,
        description: row.description,
        markedLineCount: visibleItems.reduce(
          (sum, item) => sum + Number(item.markedLineCount ?? 0),
          0
        ),
        renderState,
        songs: visibleItems.map((item) => ({
          id: item.songId,
          title: item.songTitle ?? "Unknown Song",
        })),
        lastCompletedRenderJobId: row.lastCompletedRenderJobId,
        durationSeconds: getSongsetDurationSeconds(
          visibleItems.map((item) => {
            const glue = glueByItemId.get(item.id);
            return toArrangementInput({
              arrangementSteps: item.arrangementSteps,
              gapBeats: item.gapBeats,
              crossfadeEnabled: item.crossfadeEnabled,
              crossfadeDurationSeconds: item.crossfadeDurationSeconds,
              tempoBpm: item.tempoBpm,
              durationSeconds: item.recordingDurationSeconds,
              glueTakeId: glue?.selectedTake?.id ?? null,
              glueSeconds: glue?.selectedTake?.seconds ?? null,
            });
          })
        ),
      },
      userSettings: settingsRow
        ? {
            defaultVideoTemplate: settingsRow.defaultVideoTemplate,
            defaultResolution: settingsRow.defaultResolution,
            defaultFontSizePreset: settingsRow.defaultFontSizePreset,
            defaultFontFamily: settingsRow.defaultFontFamily,
          }
        : null,
      latestJob: mapRenderJobSummary(latestJobRow),
      previousCompletedJob: mapRenderJobSummary(previousCompletedJobRow),
      gluePreflight,
    };
  });
}

/** @deprecated Use `mapRenderStateFromSnapshot` with snapshot fields instead to avoid extra DB queries. */
export async function computeRenderState(songsetId: string): Promise<RenderState> {
  const songset = await db.query.songsets.findFirst({
    where: eq(songsets.id, songsetId),
  });

  if (!songset) throw new Error("Songset not found");

  if (!songset.latestRenderJobId) return "unrendered";

  const job = await db.query.renderJobs.findFirst({
    where: eq(renderJobs.id, songset.latestRenderJobId),
  });

  if (!job) return "unrendered";

  if (job.status === "queued" || job.status === "running") return "rendering";

  if (job.status === "failed" || songset.lastFailedRenderJobId === songset.latestRenderJobId) {
    return "failed";
  }

  if (job.status === "completed") {
    if (job.completedAt) {
      const newerItem = await db.query.songsetItems.findFirst({
        where: and(
          eq(songsetItems.songsetId, songsetId),
          gt(songsetItems.updatedAt, job.completedAt)
        ),
      });
      if (newerItem) return "stale";
      const newerJob = await db.query.renderJobs.findFirst({
        where: and(
          eq(renderJobs.songsetId, songsetId),
          gt(renderJobs.createdAt, job.completedAt)
        ),
      });
      if (newerJob) return "stale";
    }
    return "fresh";
  }

  return "unrendered";
}



/** @deprecated Use `getSongsetEditorData` for the editor page, or targeted queries for other use cases. */
export async function getSongset(
  id: string,
  userId: number
): Promise<SongsetDetail | null> {
  const row = await db.query.songsets.findFirst({
    where: and(eq(songsets.id, id), eq(songsets.userId, userId)),
    with: {
      items: {
        with: {
          song: true,
          recording: {
            with: {
              lyricMarks: {
                columns: { id: true },
                where: eq(lyricMarks.userId, userId),
              },
            },
          },
        },
      },
    },
  });

  if (!row) return null;

  const sortedItems = [...row.items].sort((a, b) => a.position - b.position);
  const glueJunctions = await loadResolvedGlueJunctions(id, userId);
  const glueByItemId = new Map(
    glueJunctions.map((junction) => [junction.fromItemId, junction] as const)
  );

  const items: SongsetItemDetail[] = sortedItems
    .filter((item) => !item.recording?.deletedAt)
    .map((item) => mapRelationalSongsetItem(item, requireJunction(glueByItemId, item.id)));

  const renderState = await computeRenderState(id);

  return {
    id: row.id,
    name: row.name,
    description: row.description,
    createdAt: row.createdAt,
    updatedAt: row.updatedAt,
    latestRenderJobId: row.latestRenderJobId,
    lastFailedRenderJobId: row.lastFailedRenderJobId,
    lastCompletedRenderJobId: row.lastCompletedRenderJobId,
    itemCount: items.length,
    deletedItemCount: sortedItems.length - items.length,
    durationSeconds:
      getSongsetDurationSeconds(
        items.map((item) =>
          toArrangementInput({
            arrangementSteps: item.arrangementSteps,
            gapBeats: item.gapBeats,
            crossfadeEnabled: item.crossfadeEnabled,
            crossfadeDurationSeconds: item.crossfadeDurationSeconds,
            tempoBpm: item.recording?.tempoBpm ?? null,
            durationSeconds: item.recording?.durationSeconds ?? null,
          })
        )
      ) || null,
    renderState,
    renderErrorMessage: null,
    failedAt: null,
    themes: collectThemes(items),
    items,
  };
}

export interface CreateSongsetErrorResult {
  error: string;
  status: number;
}

export async function createSongset(
  userId: number,
  data: {
    name: string;
    description?: string;
    songIds?: string[];
  }
): Promise<Omit<SongsetListItem, "themes"> | CreateSongsetErrorResult> {
  const id = nanoid();

  if (data.songIds && data.songIds.length > 0) {
    // Verify every song exists before inserting (unknown songId → 400).
    const songRows = await db
      .select({ id: songs.id })
      .from(songs)
      .where(inArray(songs.id, data.songIds));
    const knownSongIds = new Set(songRows.map((row) => row.id));
    const unknownSongIds = data.songIds.filter((songId) => !knownSongIds.has(songId));
    if (unknownSongIds.length > 0) {
      return {
        error: `Unknown song id(s): ${unknownSongIds.join(", ")}`,
        status: 400,
      };
    }

    // All songs must have at least one visible recording (published/review).
    const visibleRows = await db
      .selectDistinct({ songId: recordings.songId })
      .from(recordings)
      .where(
        and(
          inArray(recordings.songId, data.songIds),
          isNull(recordings.deletedAt),
          inArray(recordings.visibilityStatus, ["published", "review"])
        )
      );
    const visibleSongIds = new Set(visibleRows.map((row) => row.songId));
    const noVisibleRecording = data.songIds.filter(
      (songId) => !visibleSongIds.has(songId)
    );
    if (noVisibleRecording.length > 0) {
      return {
        error: `No playable recording for song id(s): ${noVisibleRecording.join(", ")}`,
        status: 400,
      };
    }

    // Pick the recording the same way the editor's add-song flow does: the
    // first recording of that song, from the published/review set.
    const recordingRows = await db
      .select({ songId: recordings.songId, hashPrefix: recordings.hashPrefix })
      .from(recordings)
      .where(
        and(
          inArray(recordings.songId, data.songIds),
          isNull(recordings.deletedAt),
          inArray(recordings.visibilityStatus, ["published", "review"])
        )
      )
      .orderBy(asc(recordings.contentHash));
    const hashBySongId = new Map<string, string>();
    for (const row of recordingRows) {
      const { songId, hashPrefix } = row;
      if (songId === null || hashPrefix === null) continue;
      if (!hashBySongId.has(songId)) {
        hashBySongId.set(songId, hashPrefix);
      }
    }

    const itemsToInsert = data.songIds.map((songId, position) => ({
      id: nanoid(),
      songsetId: id,
      songId,
      recordingHashPrefix: hashBySongId.get(songId) ?? null,
      position,
      gapBeats: 2.0,
      crossfadeEnabled: 0,
      keyShiftSemitones: 0,
      tempoRatio: 1.0,
    }));

    await db.batch([
      db.insert(songsets).values({ id, userId, name: data.name, description: data.description ?? null }),
      db.insert(songsetItems).values(itemsToInsert),
    ]);
  } else {
    await db
      .insert(songsets)
      .values({ id, userId, name: data.name, description: data.description ?? null })
      .returning();
  }

  return {
    id,
    name: data.name,
    description: data.description ?? null,
    createdAt: new Date(),
    updatedAt: new Date(),
    latestRenderJobId: null,
    lastFailedRenderJobId: null,
    lastCompletedRenderJobId: null,
    itemCount: data.songIds?.length ?? 0,
    renderErrorMessage: null,
    failedAt: null,
    durationSeconds: null,
    renderState: "unrendered",
  };
}

export async function updateSongset(
  id: string,
  userId: number,
  patch: { name?: string; description?: string | null }
): Promise<Omit<SongsetListItem, "themes"> | null> {
  const existing = await db.query.songsets.findFirst({
    where: and(eq(songsets.id, id), eq(songsets.userId, userId)),
  });
  if (!existing) return null;

  await db
    .update(songsets)
    .set({ ...patch, updatedAt: new Date() })
    .where(and(eq(songsets.id, id), eq(songsets.userId, userId)))
    .returning();

  const updated = await db.query.songsets.findFirst({
    where: and(eq(songsets.id, id), eq(songsets.userId, userId)),
    with: {
      items: {
        columns: {
          id: true,
          arrangementSteps: true,
          gapBeats: true,
          crossfadeEnabled: true,
          crossfadeDurationSeconds: true,
        },
        with: {
          recording: {
            columns: { durationSeconds: true, tempoBpm: true, deletedAt: true },
          },
        },
      },
    },
  });

  if (!updated) return null;

  const visibleItems = updated.items.filter((item) => !item.recording?.deletedAt);
  const renderState = await computeRenderState(id);

  return {
    id: updated.id,
    name: updated.name,
    description: updated.description,
    createdAt: updated.createdAt,
    updatedAt: updated.updatedAt,
    latestRenderJobId: updated.latestRenderJobId,
    lastFailedRenderJobId: updated.lastFailedRenderJobId,
    lastCompletedRenderJobId: updated.lastCompletedRenderJobId,
    itemCount: visibleItems.length,
    durationSeconds: getSongsetDurationSeconds(
      visibleItems.map((item) =>
        toArrangementInput({
          arrangementSteps: item.arrangementSteps,
          gapBeats: item.gapBeats,
          crossfadeEnabled: item.crossfadeEnabled,
          crossfadeDurationSeconds: item.crossfadeDurationSeconds,
          durationSeconds: item.recording?.durationSeconds ?? null,
          tempoBpm: item.recording?.tempoBpm ?? null,
        })
      )
    ) || null,
    renderState,
    renderErrorMessage: null,
    failedAt: null,
  };
}


export async function deleteSongset(id: string, userId: number): Promise<boolean> {
  const existing = await db.query.songsets.findFirst({
    where: and(eq(songsets.id, id), eq(songsets.userId, userId)),
  });
  if (!existing) return false;

  await db.delete(songsets).where(and(eq(songsets.id, id), eq(songsets.userId, userId)));
  return true;
}

/**
 * Append one Item to the end of an owned Songset. The server owns the position:
 * `max(position) + 1` over the persisted rows, computed under the parent-row
 * lock so concurrent appends, duplicates, reorders, and deletes serialize on
 * the same cap/count read.
 */
export async function addSongsetItem(
  songsetId: string,
  userId: number,
  data: {
    songId: string;
    recordingHashPrefix?: string | null;
    gapBeats?: number | null;
    crossfadeEnabled?: number | null;
    crossfadeDurationSeconds?: number | null;
    keyShiftSemitones?: number | null;
    tempoRatio?: number | null;
  }
): Promise<SongsetItemDetail | null> {
  const insertedId = await mutationDb.transaction(async (tx) => {
    const [ownedSongset] = await tx
      .select({ id: songsets.id })
      .from(songsets)
      .where(and(eq(songsets.id, songsetId), eq(songsets.userId, userId)))
      .for("update");

    if (!ownedSongset) return null;

    const siblings = await tx
      .select({ id: songsetItems.id, position: songsetItems.position })
      .from(songsetItems)
      .where(eq(songsetItems.songsetId, songsetId));

    if (siblings.length >= SONGSET_MAX_SONGS) {
      throw new SongsetRenderValidationError(
        "SONGSET_FULL",
        `Songset already has maximum of ${SONGSET_MAX_SONGS} songs`
      );
    }

    const nextPosition = siblings.reduce((max, row) => Math.max(max, row.position), -1) + 1;
    const id = nanoid();
    await tx.insert(songsetItems).values({ id, songsetId, position: nextPosition, ...data });

    await tx.update(songsets).set({ updatedAt: new Date() }).where(eq(songsets.id, songsetId));

    return id;
  });

  if (!insertedId) return null;

  const item = await db.query.songsetItems.findFirst({
    where: eq(songsetItems.id, insertedId),
    with: {
      song: true,
      recording: {
        with: {
          lyricMarks: {
            columns: { id: true },
            where: eq(lyricMarks.userId, userId),
          },
        },
      },
    },
  });

  if (!item) return null;

  const glue = requireJunction(
    new Map(
      (await loadResolvedGlueJunctions(songsetId, userId)).map(
        (junction) => [junction.fromItemId, junction] as const
      )
    ),
    item.id
  );

  return mapRelationalSongsetItem(item, glue);
}

export class GlueSelectionError extends Error {
  readonly code: GlueSelectionErrorCode;
  readonly status = 409;

  constructor(code: GlueSelectionErrorCode, message: string) {
    super(message);
    this.name = "GlueSelectionError";
    this.code = code;
  }
}

async function loadGlueTakeById(takeId: string): Promise<GlueTakeRow | null> {
  const [take] = await db
    .select()
    .from(glueTakes)
    .where(eq(glueTakes.id, takeId))
    .limit(1);
  return take ? takeRowFromDb(take) : null;
}

async function loadNewestReadyGlueTake(
  userId: number,
  descriptorKey: string
): Promise<GlueTakeRow | null> {
  const [take] = await db
    .select()
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
  return take ? takeRowFromDb(take) : null;
}

/**
 * Validate a PATCH's AI-transition selection against the saved Junction before
 * the write. Selection is free: no job, quota, or provider involvement. The
 * caller's expected item timestamp is returned so the transaction can reject a
 * concurrent item change (`GLUE_INPUTS_CHANGED`).
 */
async function validateGlueSelectionForPatch(args: {
  songsetId: string;
  userId: number;
  itemId: string;
  patch: { glueEnabled?: boolean; glueActiveTakeId?: string | null };
}): Promise<{ selectedTakeId: string | null; expectedUpdatedAt: string } | null> {
  const itemRows =
    (await loadGlueItemRows([args.songsetId], args.userId)).get(args.songsetId) ?? [];
  const index = itemRows.findIndex((item) => item.itemId === args.itemId);
  if (index === -1) return null;
  const item = itemRows[index];
  const to = itemRows[index + 1] ?? null;

  if (to === null) {
    throw new GlueSelectionError(
      "GLUE_NO_SUCCESSOR",
      "The last Song has no next Song to transition into."
    );
  }

  const derivation: GlueJunctionDerivation = deriveGlueJunction(item, to);
  const explicitTakeId =
    args.patch.glueActiveTakeId === undefined ? undefined : args.patch.glueActiveTakeId;

  if (explicitTakeId !== undefined) {
    const take = explicitTakeId === null ? null : await loadGlueTakeById(explicitTakeId);
    if (explicitTakeId === null) {
      return { selectedTakeId: null, expectedUpdatedAt: item.updatedAt };
    }
    const code = glueTakeSelectionError({
      glueEnabled: args.patch.glueEnabled ?? item.glueEnabled,
      hasSuccessor: true,
      derivation,
      take,
      ownerUserId: args.userId,
    });
    if (code) {
      throw new GlueSelectionError(
        code,
        code === "GLUE_STALE_SNAPSHOT"
          ? "This AI transition belongs to a different version of the Song sequence."
          : "This AI transition is not available for selection."
      );
    }
    return { selectedTakeId: explicitTakeId, expectedUpdatedAt: item.updatedAt };
  }

  if (args.patch.glueEnabled === true && derivation.ok) {
    const take = await loadNewestReadyGlueTake(args.userId, derivation.descriptorKey);
    return { selectedTakeId: take?.id ?? null, expectedUpdatedAt: item.updatedAt };
  }

  return { selectedTakeId: null, expectedUpdatedAt: item.updatedAt };
}

/**
 * Copy one Songset Item into the same Songset immediately below itself,
 * carrying the Song, the Recording, the Transition settings, and the Song
 * Arrangement verbatim. A copy is a new occurrence, so the Songset Item cap
 * and the duration limit both apply to it.
 */
export async function duplicateSongsetItem(
  itemId: string,
  songsetId: string,
  userId: number
): Promise<SongsetItemDetail | null> {
  const insertedId = await mutationDb.transaction(async (tx) => {
    // Parent-row lock serializes duplicate/append writes per Songset: the cap
    // and duration admission read the siblings' persisted state.
    const [ownedSongset] = await tx
      .select({ id: songsets.id })
      .from(songsets)
      .where(and(eq(songsets.id, songsetId), eq(songsets.userId, userId)))
      .for("update");

    if (!ownedSongset) return null;

    const siblingRows = await tx
      .select({
        ...arrangementItemColumns,
        songId: songsetItems.songId,
        recordingHashPrefix: songsetItems.recordingHashPrefix,
        position: songsetItems.position,
        keyShiftSemitones: songsetItems.keyShiftSemitones,
        tempoRatio: songsetItems.tempoRatio,
        updatedAt: songsetItems.updatedAt,
        glueBeats: songsetItems.glueBeats,
        glueActiveTakeId: songsetItems.glueActiveTakeId,
        contentHash: recordings.contentHash,
      })
      .from(songsetItems)
      .leftJoin(recordings, eq(songsetItems.recordingHashPrefix, recordings.hashPrefix))
      .where(eq(songsetItems.songsetId, songsetId))
      .orderBy(asc(songsetItems.position));

    const source = siblingRows.find((row) => row.id === itemId);
    if (!source) return null;

    // A Recording with a deletedAt is invisible to renders, so it neither counts
    // toward the cap nor contributes to the projected duration.
    const visibleRows = siblingRows.filter((row) => !row.recordingDeletedAt);
    if (visibleRows.length >= SONGSET_MAX_SONGS) {
      throw new SongsetRenderValidationError(
        "SONGSET_FULL",
        `Songset already has maximum of ${SONGSET_MAX_SONGS} songs`
      );
    }

    const nextId = nanoid();
    const now = new Date();
    const activeTakeIds = [...visibleRows, source]
      .map((row) => row.glueActiveTakeId)
      .filter((id): id is string => id !== null);
    const takeRows = activeTakeIds.length > 0
      ? await tx
          .select()
          .from(glueTakes)
          .where(and(eq(glueTakes.userId, userId), inArray(glueTakes.id, [...new Set(activeTakeIds)])))
      : [];
    const takesById = new Map(takeRows.map((row) => [row.id, takeRowFromDb(row)] as const));
    const copiedTakeId =
      source.glueActiveTakeId !== null && takesById.get(source.glueActiveTakeId)?.userId === userId
        ? source.glueActiveTakeId
        : null;

    // Resolve the prospective adjacency. The source and copy have distinct
    // outgoing junctions even when they play the same audio.
    const projectedRows = [...visibleRows];
    const sourceIndex = projectedRows.findIndex((row) => row.id === itemId);
    if (sourceIndex >= 0) {
      projectedRows.splice(sourceIndex + 1, 0, {
        ...source,
        id: nextId,
        position: source.position + 1,
        updatedAt: now,
        glueActiveTakeId: copiedTakeId,
      });
    }
    const resolverItems: GlueResolverItem[] = projectedRows.map((row) => ({
      itemId: row.id,
      position: row.position,
      userId,
      glueEnabled: row.glueEnabled,
      glueBeats: normalizeGlueBeats(row.glueBeats),
      glueActiveTakeId: row.glueActiveTakeId,
      updatedAt: row.updatedAt.toISOString(),
      arrangementSteps: row.arrangementSteps,
      recording: row.contentHash === null
        ? null
        : {
            contentHash: row.contentHash,
            durationSeconds: row.durationSeconds,
            tempoBpm: row.tempoBpm,
            sections: parseRecordingSections(row.recordingSections),
            beats: parseRecordingBeats(row.recordingBeats),
          },
    }));
    const prospectiveTotalSeconds = getSongsetDurationSeconds(
      projectedRows.map((row, index) => {
        const from = resolverItems[index];
        const to = resolverItems[index + 1];
        const take = from.glueActiveTakeId && takesById.get(from.glueActiveTakeId);
        if (!from.glueEnabled || !to || !take) return toArrangementInput(row);
        const selectionError = glueTakeSelectionError({
          glueEnabled: true,
          hasSuccessor: true,
          derivation: deriveGlueJunction(from, to),
          take,
          ownerUserId: userId,
        });
        return selectionError === null
          ? toArrangementInput({ ...row, glueTakeId: take.id, glueSeconds: take.seconds })
          : toArrangementInput(row);
      })
    );
    if (
      prospectiveTotalSeconds != null &&
      prospectiveTotalSeconds > SONGSET_MAX_DURATION_SECONDS
    ) {
      throw new SongsetRenderValidationError(
        "SONGSET_TOO_LONG",
        `Songset exceeds maximum duration of ${Math.floor(SONGSET_MAX_DURATION_SECONDS / 60)} minutes`
      );
    }

    await tx
      .update(songsetItems)
      .set({ position: sql`${songsetItems.position} + 1`, updatedAt: now })
      .where(
        and(eq(songsetItems.songsetId, songsetId), gt(songsetItems.position, source.position))
      );

    await tx.insert(songsetItems).values({
      id: nextId,
      songsetId,
      songId: source.songId,
      recordingHashPrefix: source.recordingHashPrefix,
      position: source.position + 1,
      gapBeats: source.gapBeats,
      crossfadeEnabled: source.crossfadeEnabled,
      crossfadeDurationSeconds: source.crossfadeDurationSeconds,
      keyShiftSemitones: source.keyShiftSemitones,
      tempoRatio: source.tempoRatio,
      arrangementSteps: source.arrangementSteps,
      glueEnabled: source.glueEnabled,
      glueBeats: source.glueBeats,
      glueActiveTakeId: copiedTakeId,
      updatedAt: now,
    });
    await tx.update(songsets).set({ updatedAt: now }).where(eq(songsets.id, songsetId));

    return nextId;
  });

  if (!insertedId) return null;

  const inserted = await db.query.songsetItems.findFirst({
    where: eq(songsetItems.id, insertedId),
    with: {
      song: true,
      recording: {
        with: {
          lyricMarks: {
            columns: { id: true },
            where: eq(lyricMarks.userId, userId),
          },
        },
      },
    },
  });

  if (!inserted) return null;

  // Resolve the copied selection against the committed successor.
  const glue = requireJunction(
    new Map(
      (await loadResolvedGlueJunctions(songsetId, userId)).map(
        (junction) => [junction.fromItemId, junction] as const
      )
    ),
    inserted.id
  );

  return mapRelationalSongsetItem(inserted, glue);
}

export async function updateSongsetItem(
  itemId: string,
  songsetId: string,
  userId: number,
  patch: Partial<{
    recordingHashPrefix: string | null;
    gapBeats: number | null;
    crossfadeEnabled: number | null;
    crossfadeDurationSeconds: number | null;
    keyShiftSemitones: number | null;
    tempoRatio: number | null;
    glueEnabled: boolean;
    glueBeats: 2 | 4 | 8 | 16;
    glueActiveTakeId: string | null;
  }>
): Promise<SongsetItemDetail | null> {
  const needsSelectionValidation =
    (patch.glueActiveTakeId !== undefined && patch.glueActiveTakeId !== null) ||
    patch.glueEnabled === true;
  const selection = needsSelectionValidation
    ? await validateGlueSelectionForPatch({
        songsetId,
        userId,
        itemId,
        patch,
      })
    : null;
  if (needsSelectionValidation && selection === null) return null;

  const applied = await mutationDb.transaction(async (tx) => {
    // Serialize writes for this owned Songset: without the parent-row lock two
    // concurrent saves can both validate against pre-mutation siblings.
    const [ownedSongset] = await tx
      .select({ id: songsets.id })
      .from(songsets)
      .where(and(eq(songsets.id, songsetId), eq(songsets.userId, userId)))
      .for("update");

    if (!ownedSongset) return false;

    const [item] = await tx
      .select({
        id: songsetItems.id,
        recordingHashPrefix: songsetItems.recordingHashPrefix,
        updatedAt: songsetItems.updatedAt,
      })
      .from(songsetItems)
      .where(and(eq(songsetItems.id, itemId), eq(songsetItems.songsetId, songsetId)))
      .limit(1);

    if (!item) return false;

    if (
      selection !== null &&
      item.updatedAt.toISOString() !== selection.expectedUpdatedAt
    ) {
      throw new GlueSelectionError(
        "GLUE_INPUTS_CHANGED",
        "The Song sequence changed while this AI transition was being selected."
      );
    }

    const now = new Date();
    const {
      recordingHashPrefix,
      gapBeats,
      crossfadeEnabled,
      crossfadeDurationSeconds,
      keyShiftSemitones,
      tempoRatio,
      glueEnabled,
      glueBeats,
      glueActiveTakeId,
    } = patch;
    // Always bump the item timestamp: render staleness compares
    // songset_items.updated_at to the job's completion, so transition-only and
    // recording changes must invalidate a fresh render too (pre-existing gap).
    // Built field by field so a route payload's extra keys never reach SQL.
    const updateSet: Partial<typeof songsetItems.$inferInsert> = {
      recordingHashPrefix,
      gapBeats,
      crossfadeEnabled,
      crossfadeDurationSeconds,
      keyShiftSemitones,
      tempoRatio,
      glueEnabled,
      glueBeats,
      glueActiveTakeId:
        selection !== null ? selection.selectedTakeId : glueActiveTakeId,
      updatedAt: now,
    };
    // A changed Recording invalidates the saved arrangement (spec §2.5); the
    // generic transition PATCH never writes arrangement data otherwise.
    if (recordingHashPrefix !== undefined && recordingHashPrefix !== item.recordingHashPrefix) {
      if (recordingHashPrefix !== null) {
        // Reject a dangling replacement inside the transaction so the saved
        // arrangement is never cleared for a Recording that does not exist.
        const [replacement] = await tx
          .select({ hashPrefix: recordings.hashPrefix })
          .from(recordings)
          .where(eq(recordings.hashPrefix, recordingHashPrefix))
          .limit(1);
        if (!replacement) return false;
      }
      updateSet.arrangementSteps = null;
    }

    await tx.update(songsetItems).set(updateSet).where(eq(songsetItems.id, itemId));

    await tx.update(songsets).set({ updatedAt: now }).where(eq(songsets.id, songsetId));

    return true;
  });

  if (!applied) return null;

  const updated = await db.query.songsetItems.findFirst({
    where: eq(songsetItems.id, itemId),
    with: {
      song: true,
      recording: {
        with: {
          lyricMarks: {
            columns: { id: true },
            where: eq(lyricMarks.userId, userId),
          },
        },
      },
    },
  });

  if (!updated) return null;

  const glue = requireJunction(
    new Map(
      (await loadResolvedGlueJunctions(songsetId, userId)).map(
        (junction) => [junction.fromItemId, junction] as const
      )
    ),
    itemId
  );

  return mapRelationalSongsetItem(updated, glue);
}

/**
 * Store a take selection for an item's outgoing Junction. Returns the flat
 * detail and the resolved outgoing Junction; validation lives in
 * `updateSongsetItem` (selection is free: no job, quota, or provider call).
 */
export async function selectGlueTake(
  itemId: string,
  songsetId: string,
  userId: number,
  takeId: string
): Promise<{ item: SongsetItemDetail; glue: ResolvedGlueJunction } | null> {
  const item = await updateSongsetItem(itemId, songsetId, userId, {
    glueActiveTakeId: takeId,
  });
  if (!item) return null;
  return { item, glue: item.glue };
}

export async function deleteSongsetItem(
  itemId: string,
  songsetId: string,
  userId: number
): Promise<boolean> {
  return mutationDb.transaction(async (tx) => {
    // Same parent-row lock as append/duplicate/reorder so a delete cannot
    // interleave with a cap or position read.
    const [ownedSongset] = await tx
      .select({ id: songsets.id })
      .from(songsets)
      .where(and(eq(songsets.id, songsetId), eq(songsets.userId, userId)))
      .for("update");

    if (!ownedSongset) return false;

    const [item] = await tx
      .select({ id: songsetItems.id })
      .from(songsetItems)
      .where(and(eq(songsetItems.id, itemId), eq(songsetItems.songsetId, songsetId)))
      .limit(1);

    if (!item) return false;

    const now = new Date();
    await tx.delete(songsetItems).where(eq(songsetItems.id, itemId));
    await tx.update(songsets).set({ updatedAt: now }).where(eq(songsets.id, songsetId));

    return true;
  });
}

/**
 * Apply caller-supplied Item positions atomically. Every target Item must
 * belong to the owned Songset; the whole update is rejected otherwise, so a
 * partial reorder never lands. Holds the shared parent-row lock.
 */
export async function reorderSongsetItems(
  songsetId: string,
  userId: number,
  updates: { itemId: string; position: number }[]
): Promise<"ok" | "songset-not-found" | "item-not-found"> {
  return mutationDb.transaction(async (tx) => {
    const [ownedSongset] = await tx
      .select({ id: songsets.id })
      .from(songsets)
      .where(and(eq(songsets.id, songsetId), eq(songsets.userId, userId)))
      .for("update");

    if (!ownedSongset) return "songset-not-found";

    if (updates.length === 0) return "ok";

    const targetIds = updates.map((update) => update.itemId);
    const ownedItems = await tx
      .select({ id: songsetItems.id })
      .from(songsetItems)
      .where(and(eq(songsetItems.songsetId, songsetId), inArray(songsetItems.id, targetIds)));
    const ownedIds = new Set(ownedItems.map((item) => item.id));

    if (targetIds.some((itemId) => !ownedIds.has(itemId))) return "item-not-found";

    const now = new Date();
    for (const update of updates) {
      await tx
        .update(songsetItems)
        .set({ position: update.position, updatedAt: now })
        .where(eq(songsetItems.id, update.itemId));
    }

    await tx.update(songsets).set({ updatedAt: now }).where(eq(songsets.id, songsetId));

    return "ok";
  });
}

export type SongsetRenderValidationCode =
  | "SONGSET_NOT_FOUND"
  | "SONGSET_TOO_LONG"
  | "SONGSET_FULL"
  | "ARRANGEMENT_ANALYSIS_UNAVAILABLE"
  | "ARRANGEMENT_STALE"
  | "ARRANGEMENT_INVALID"
  | "GLUE_NOT_READY";

/**
 * One enabled-but-unavailable Glue Junction, ordered by the outgoing item's
 * Songset position (spec §5.5). `reasonCode` is a stable machine code derived
 * from the resolved Junction; the client localizes it.
 */
export interface GlueRenderPreflightGlue {
  fromItemId: string;
  toItemId: string;
  fromTitle: string;
  toTitle: string;
  reasonCode: string;
  jobId: string | null;
  jobPhase: string | null;
  retryKind: string | null;
}

/** Blocking render preflight payload (spec §5.5). */
export interface GlueRenderPreflight {
  code: "GLUE_NOT_READY";
  glues: GlueRenderPreflightGlue[];
}

/**
 * Frozen render snapshot entry written to `render_jobs.glue_transitions`
 * (spec §5.5/C7). Field names and order are part of the worker contract.
 */
export interface RenderGlueSnapshotEntry {
  from_item_id: string;
  from_updated_at: string;
  to_item_id: string;
  to_updated_at: string;
  take_id: string;
  descriptor_key: string;
  r2_key: string;
  seconds: number;
  sha256: string;
}

export interface RenderGluePlan {
  preflight: GlueRenderPreflight | null;
  snapshot: RenderGlueSnapshotEntry[];
}

export interface GlueRenderItemIdentity {
  id: string;
  position: number;
  title: string | null;
}

export interface GlueRenderJobFact {
  id: string;
  fromItemId: string;
  phase: string;
  retryKind: string | null;
  errorCode: string | null;
  createdAt: Date;
}

export const GLUE_NOT_READY_MESSAGE =
  "Finish or turn off the AI transitions before rendering.";

/**
 * Typed admission/write failure shared by `updateSongArrangement` and the
 * render entry points. Analysis failures all carry the canonical
 * repair-or-reset message (spec §2.3/§2.7). `GLUE_NOT_READY` carries the
 * ordered preflight payload so the route and page can render the remedies.
 */
export class SongsetRenderValidationError extends Error {
  constructor(
    readonly code: SongsetRenderValidationCode,
    message: string = ARRANGEMENT_UNAVAILABLE_OR_STALE_MESSAGE,
    readonly glue: GlueRenderPreflight | null = null
  ) {
    super(message);
    this.name = "SongsetRenderValidationError";
  }
}

/** Map a `validateSongArrangement` failure code to the render/route vocabulary. */
function analysisValidationError(code: ArrangementValidationCode): SongsetRenderValidationError {
  if (code === "ANALYSIS_UNAVAILABLE") {
    return new SongsetRenderValidationError("ARRANGEMENT_ANALYSIS_UNAVAILABLE");
  }
  return new SongsetRenderValidationError(code);
}

// --- Glue render preflight + frozen snapshot (spec §5.5) -------------------

const GLUE_RENDER_ACTIVE_STATES: Readonly<Record<string, true>> = {
  queued: true,
  preparing: true,
  uploading: true,
  composing: true,
  "storing-response": true,
  staging: true,
};

function latestGlueJobFacts(
  jobs: readonly GlueRenderJobFact[]
): Map<string, GlueRenderJobFact> {
  const latest = new Map<string, GlueRenderJobFact>();
  for (const job of jobs) {
    const previous = latest.get(job.fromItemId);
    if (!previous || job.createdAt.getTime() >= previous.createdAt.getTime()) {
      latest.set(job.fromItemId, job);
    }
  }
  return latest;
}

/**
 * Stable machine reason for a blocked Junction. Derived from the resolved
 * Junction's `state`/`unavailableReason` (the resolver's vocabulary) plus the
 * latest `glue_jobs` row for job id/phase/retry kind; the client localizes it.
 */
function glueRenderReasonCode(
  junction: ResolvedGlueJunction,
  job: GlueRenderJobFact | null
): string {
  if (junction.state === "failed") {
    if (job?.errorCode) return job.errorCode;
    if (job?.retryKind === "paid") return "GLUE_RETRY_PAID";
    if (job?.retryKind === "free") return "GLUE_RETRY_PROCESSING_FREE";
    return "GLUE_WORKER_FAILED";
  }
  if (junction.state === "stale") return "GLUE_STALE_SNAPSHOT";
  if (GLUE_RENDER_ACTIVE_STATES[junction.state]) return "GLUE_ACTIVE_JOB";
  switch (junction.unavailableReason) {
    case "quota":
      return "GLUE_QUOTA_EXHAUSTED";
    case "no-tempo":
      return "GLUE_TEMPO_UNAVAILABLE";
    case "no-analysis":
      return "GLUE_NOT_ANALYZED";
    case "invalid-arrangement":
      return "GLUE_ARRANGEMENT_INVALID";
    case "unsaved-draft":
      return "GLUE_UNSAVED_DRAFT";
    case "active-job":
      return "GLUE_ACTIVE_JOB";
    default:
      return "GLUE_NOT_READY";
  }
}

function gluePreflightEntry(
  junction: ResolvedGlueJunction,
  identities: ReadonlyMap<string, GlueRenderItemIdentity>,
  latestJobs: ReadonlyMap<string, GlueRenderJobFact>
): GlueRenderPreflightGlue {
  const job = latestJobs.get(junction.fromItemId) ?? null;
  const from = identities.get(junction.fromItemId);
  const to = junction.toItemId ? identities.get(junction.toItemId) : undefined;
  return {
    fromItemId: junction.fromItemId,
    toItemId: junction.toItemId ?? "",
    fromTitle: from?.title ?? "Unknown Song",
    toTitle: to?.title ?? "Unknown Song",
    reasonCode: glueRenderReasonCode(junction, job),
    jobId: junction.activeJob?.id ?? job?.id ?? null,
    jobPhase: junction.activeJob?.phase ?? job?.phase ?? null,
    retryKind: job?.retryKind ?? null,
  };
}

/**
 * Pure preflight payload builder: one entry per enabled Junction without an
 * effective ready take, ordered by the outgoing item's Songset position.
 */
export function buildGlueRenderPreflight(args: {
  junctions: readonly ResolvedGlueJunction[];
  items: readonly GlueRenderItemIdentity[];
  jobs: readonly GlueRenderJobFact[];
}): GlueRenderPreflight | null {
  const identities = new Map(args.items.map((item) => [item.id, item] as const));
  const order = new Map(
    [...args.items]
      .sort((a, b) => a.position - b.position || a.id.localeCompare(b.id))
      .map((item, index) => [item.id, index] as const)
  );
  const latestJobs = latestGlueJobFacts(args.jobs);
  const glues = args.junctions
    .filter(
      (junction) =>
        junction.glueEnabled &&
        junction.toItemId !== null &&
        !(junction.state === "ready" && junction.selectedTake !== null)
    )
    .sort(
      (a, b) =>
        (order.get(a.fromItemId) ?? Number.MAX_SAFE_INTEGER) -
          (order.get(b.fromItemId) ?? Number.MAX_SAFE_INTEGER) ||
        a.fromItemId.localeCompare(b.fromItemId)
    )
    .map((junction) => gluePreflightEntry(junction, identities, latestJobs));
  return glues.length > 0 ? { code: "GLUE_NOT_READY", glues } : null;
}

function reportGlueRenderPreflight(
  songsetId: string,
  userId: number,
  preflight: GlueRenderPreflight,
  stage?: "create"
): void {
  logGlueEvent("glue.render.preflight.failed", {
    songsetId,
    userId,
    junctions: preflight.glues.length,
    reasonCodes: [...new Set(preflight.glues.map((glue) => glue.reasonCode))],
    ...(stage ? { stage } : {}),
  });
}

function rejectGlueRenderPreflight(
  songsetId: string,
  userId: number,
  preflight: GlueRenderPreflight,
  stage?: "create"
): never {
  reportGlueRenderPreflight(songsetId, userId, preflight, stage);
  throw new SongsetRenderValidationError(
    "GLUE_NOT_READY",
    GLUE_NOT_READY_MESSAGE,
    preflight
  );
}

/** Visible items with the titles the render preflight panel needs. */
export async function loadGlueRenderItemIdentities(
  songsetId: string
): Promise<GlueRenderItemIdentity[]> {
  const rows = await db
    .select({
      id: songsetItems.id,
      position: songsetItems.position,
      title: songs.title,
    })
    .from(songsetItems)
    .leftJoin(songs, eq(songsetItems.songId, songs.id))
    .leftJoin(recordings, eq(songsetItems.recordingHashPrefix, recordings.hashPrefix))
    .where(and(eq(songsetItems.songsetId, songsetId), isNull(recordings.deletedAt)))
    .orderBy(asc(songsetItems.position));
  return rows.map((row) => ({
    id: row.id,
    position: row.position,
    title: row.title ?? null,
  }));
}

/** Latest-first job facts (job id/phase/retry kind) per outgoing item. */
export async function loadGlueRenderJobFacts(
  songsetId: string,
  userId: number
): Promise<GlueRenderJobFact[]> {
  const rows = await db
    .select({
      id: glueJobs.id,
      fromItemId: glueJobs.fromItemId,
      phase: glueJobs.phase,
      retryKind: glueJobs.retryKind,
      errorCode: glueJobs.errorCode,
      createdAt: glueJobs.createdAt,
    })
    .from(glueJobs)
    .where(and(eq(glueJobs.songsetId, songsetId), eq(glueJobs.userId, userId)))
    .orderBy(asc(glueJobs.createdAt));
  return rows.map((row) => ({
    id: row.id,
    fromItemId: row.fromItemId,
    phase: row.phase,
    retryKind: row.retryKind,
    errorCode: row.errorCode,
    createdAt: row.createdAt,
  }));
}

function glueJobRowForResolve(row: typeof glueJobs.$inferSelect): GlueJobRow {
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
    startedAt: row.startedAt ? row.startedAt.toISOString() : null,
    finishedAt: row.finishedAt ? row.finishedAt.toISOString() : null,
    heartbeatAt: row.heartbeatAt ? row.heartbeatAt.toISOString() : null,
  };
}

export type GlueRenderTransaction = Parameters<
  Parameters<typeof mutationDb.transaction>[0]
>[0];

/**
 * Transactional render admission + snapshot writer (spec §5.5). Re-reads the
 * items, selected takes, and descriptor inputs inside the caller's transaction
 * so the frozen entry timestamps are exactly what the worker will re-check.
 * Returns the blocking preflight instead of throwing so the caller can log the
 * `stage: "create"` event before rejecting.
 */
export async function createRenderGlueSnapshot(
  tx: GlueRenderTransaction,
  songsetId: string,
  userId: number
): Promise<RenderGluePlan> {
  const rows = await tx
    .select({
      id: songsetItems.id,
      position: songsetItems.position,
      title: songs.title,
      updatedAt: songsetItems.updatedAt,
      glueEnabled: songsetItems.glueEnabled,
      glueBeats: songsetItems.glueBeats,
      glueActiveTakeId: songsetItems.glueActiveTakeId,
      arrangementSteps: songsetItems.arrangementSteps,
      contentHash: recordings.contentHash,
      durationSeconds: recordings.durationSeconds,
      tempoBpm: recordings.tempoBpm,
      sections: recordings.sections,
      beats: recordings.beats,
      recordingDeletedAt: recordings.deletedAt,
    })
    .from(songsetItems)
    .leftJoin(songs, eq(songsetItems.songId, songs.id))
    .leftJoin(recordings, eq(songsetItems.recordingHashPrefix, recordings.hashPrefix))
    .where(eq(songsetItems.songsetId, songsetId))
    .orderBy(asc(songsetItems.position));

  const visible = rows.filter((row) => !row.recordingDeletedAt);
  if (!visible.some((row) => row.glueEnabled)) {
    return { preflight: null, snapshot: [] };
  }

  const items: GlueResolverItem[] = visible.map((row) => ({
    itemId: row.id,
    position: row.position,
    userId,
    glueEnabled: row.glueEnabled,
    glueBeats: normalizeGlueBeats(row.glueBeats),
    glueActiveTakeId: row.glueActiveTakeId,
    updatedAt: row.updatedAt.toISOString(),
    arrangementSteps: row.arrangementSteps,
    recording:
      row.contentHash === null
        ? null
        : {
            contentHash: row.contentHash,
            durationSeconds: row.durationSeconds,
            tempoBpm: row.tempoBpm,
            sections: parseRecordingSections(row.sections ?? null),
            beats: parseRecordingBeats(row.beats ?? null),
          },
  }));

  const selectedTakeIds = items
    .map((item) => item.glueActiveTakeId)
    .filter((takeId): takeId is string => takeId !== null);
  const takeRows =
    selectedTakeIds.length > 0
      ? await tx.select().from(glueTakes).where(inArray(glueTakes.id, selectedTakeIds))
      : [];
  const jobRows = await tx
    .select()
    .from(glueJobs)
    .where(and(eq(glueJobs.songsetId, songsetId), eq(glueJobs.userId, userId)))
    .orderBy(asc(glueJobs.createdAt));

  const quota = await readGlueQuota(userId);
  const takes = takeRows.map(takeRowFromDb);
  const jobs = jobRows.map(glueJobRowForResolve);
  const junctions = resolveGlueJunctions({
    items,
    takes,
    jobs,
    quota,
    cachedExcerptKeys: new Set<string>(),
    rejectedExcerptKeys: new Set<string>(),
  });

  const identities: GlueRenderItemIdentity[] = visible.map((row) => ({
    id: row.id,
    position: row.position,
    title: row.title ?? null,
  }));
  const jobFacts: GlueRenderJobFact[] = jobRows.map((row) => ({
    id: row.id,
    fromItemId: row.fromItemId,
    phase: row.phase,
    retryKind: row.retryKind,
    errorCode: row.errorCode,
    createdAt: row.createdAt,
  }));
  const preflight = buildGlueRenderPreflight({
    junctions,
    items: identities,
    jobs: jobFacts,
  });
  if (preflight) return { preflight, snapshot: [] };

  const identityById = new Map(identities.map((item) => [item.id, item] as const));
  const rowById = new Map(visible.map((row) => [row.id, row] as const));
  const takeById = new Map(takes.map((take) => [take.id, take] as const));
  const snapshot: RenderGlueSnapshotEntry[] = [];

  for (const junction of junctions) {
    if (
      !junction.glueEnabled ||
      junction.toItemId === null ||
      junction.state !== "ready" ||
      junction.selectedTake === null
    ) {
      continue;
    }
    const selectedTake = junction.selectedTake;
    const take = takeById.get(selectedTake.id);
    const fromRow = rowById.get(junction.fromItemId);
    const toRow = rowById.get(junction.toItemId);
    const descriptorKey = junction.descriptorKey;
    // Revalidate the immutable take row immediately before insert (spec §5.5).
    if (
      take === undefined ||
      fromRow === undefined ||
      toRow === undefined ||
      descriptorKey === null ||
      take.userId !== userId ||
      take.status !== "ready" ||
      take.r2Key === null ||
      take.r2Key !== selectedTake.r2Key ||
      take.descriptorKey !== descriptorKey ||
      take.seconds === null ||
      take.seconds <= 0 ||
      take.audioSha256 === null
    ) {
      return {
        preflight: {
          code: "GLUE_NOT_READY",
          glues: [gluePreflightEntry(junction, identityById, latestGlueJobFacts(jobFacts))],
        },
        snapshot: [],
      };
    }
    snapshot.push({
      from_item_id: junction.fromItemId,
      from_updated_at: fromRow.updatedAt.toISOString(),
      to_item_id: junction.toItemId,
      to_updated_at: toRow.updatedAt.toISOString(),
      take_id: take.id,
      descriptor_key: descriptorKey,
      r2_key: take.r2Key,
      seconds: selectedTake.seconds,
      sha256: take.audioSha256,
    });
  }

  return { preflight: null, snapshot };
}

/** Throw the blocking preflight payload (logs the stable event first). */
export function requireGlueRenderReady(
  songsetId: string,
  userId: number,
  preflight: GlueRenderPreflight | null,
  stage?: "create"
): void {
  if (preflight) rejectGlueRenderPreflight(songsetId, userId, preflight, stage);
}

/** Item columns every arrangement/duration computation reads. */
const arrangementItemColumns = {
  id: songsetItems.id,
  arrangementSteps: songsetItems.arrangementSteps,
  gapBeats: songsetItems.gapBeats,
  crossfadeEnabled: songsetItems.crossfadeEnabled,
  crossfadeDurationSeconds: songsetItems.crossfadeDurationSeconds,
  durationSeconds: recordings.durationSeconds,
  tempoBpm: recordings.tempoBpm,
  recordingSections: recordings.sections,
  recordingBeats: recordings.beats,
  recordingDeletedAt: recordings.deletedAt,
  glueEnabled: songsetItems.glueEnabled,
};

/**
 * Save (`arrangement`) or reset (`null`) one item's arrangement (spec §2.3).
 * NULL is always valid, even without analysis. Non-NULL data must match the
 * item's current analysis and the prospective whole-Songset laid-out timeline
 * total must stay within the duration limit. Both the item and the Songset
 * timestamps bump so `mapRenderStateFromSnapshot` reports "stale" after any
 * change.
 */
export async function updateSongArrangement(
  itemId: string,
  songsetId: string,
  userId: number,
  arrangement: SongArrangement | null
): Promise<SongsetItemDetail | null> {
  const applied = await mutationDb.transaction(async (tx) => {
    // Parent-row lock serializes arrangement saves per Songset: admission reads
    // the siblings' persisted state, so two unlocked saves could each pass the
    // limit against identical snapshots.
    const [ownedSongset] = await tx
      .select({ id: songsets.id })
      .from(songsets)
      .where(and(eq(songsets.id, songsetId), eq(songsets.userId, userId)))
      .for("update");

    if (!ownedSongset) return false;

    const [item] = await tx
      .select({ id: songsetItems.id })
      .from(songsetItems)
      .where(and(eq(songsetItems.id, itemId), eq(songsetItems.songsetId, songsetId)))
      .limit(1);

    if (!item) return false;

    let serialized: string | null = arrangement === null ? null : JSON.stringify(arrangement);

    const siblingRows = await tx
      .select(arrangementItemColumns)
      .from(songsetItems)
      .leftJoin(recordings, eq(songsetItems.recordingHashPrefix, recordings.hashPrefix))
      .where(eq(songsetItems.songsetId, songsetId))
      .orderBy(asc(songsetItems.position));

    if (serialized !== null) {
      const row = siblingRows.find((sibling) => sibling.id === itemId);
      if (!row || row.recordingDeletedAt || row.durationSeconds == null) {
        throw new SongsetRenderValidationError("ARRANGEMENT_ANALYSIS_UNAVAILABLE");
      }
      const validated = validateSongArrangement(
        serialized,
        parseRecordingSections(row.recordingSections),
        parseRecordingBeats(row.recordingBeats),
        row.durationSeconds
      );
      if (!validated.ok) {
        throw analysisValidationError(validated.code);
      }
      if (validated.arrangement === null) {
        // Unreachable for a non-NULL input; never write the string "null".
        throw new SongsetRenderValidationError("ARRANGEMENT_INVALID");
      }
      // Canonical wire form (exact schema fields only).
      serialized = JSON.stringify(validated.arrangement);

      const prospectiveTotalSeconds = getSongsetDurationSeconds(
        siblingRows
          .filter((sibling) => !sibling.recordingDeletedAt)
          .map((sibling) =>
            toArrangementInput({
              arrangementSteps: sibling.id === itemId ? serialized : sibling.arrangementSteps,
              gapBeats: sibling.gapBeats,
              crossfadeEnabled: sibling.crossfadeEnabled,
              crossfadeDurationSeconds: sibling.crossfadeDurationSeconds,
              tempoBpm: sibling.tempoBpm,
              durationSeconds: sibling.durationSeconds,
            })
          )
      );
      if (
        prospectiveTotalSeconds != null &&
        prospectiveTotalSeconds > SONGSET_MAX_DURATION_SECONDS
      ) {
        throw new SongsetRenderValidationError(
          "SONGSET_TOO_LONG",
          `Songset exceeds maximum duration of ${Math.floor(SONGSET_MAX_DURATION_SECONDS / 60)} minutes`
        );
      }
    }

    const now = new Date();
    await tx
      .update(songsetItems)
      .set({ arrangementSteps: serialized, updatedAt: now })
      .where(eq(songsetItems.id, itemId));
    await tx.update(songsets).set({ updatedAt: now }).where(eq(songsets.id, songsetId));

    return true;
  });

  if (!applied) return null;

  const updated = await db.query.songsetItems.findFirst({
    where: eq(songsetItems.id, itemId),
    with: {
      song: true,
      recording: {
        with: {
          lyricMarks: {
            columns: { id: true },
            where: eq(lyricMarks.userId, userId),
          },
        },
      },
    },
  });

  if (!updated) return null;

  const glue = requireJunction(
    new Map(
      (await loadResolvedGlueJunctions(songsetId, userId)).map(
        (junction) => [junction.fromItemId, junction] as const
      )
    ),
    itemId
  );

  return mapRelationalSongsetItem(updated, glue);
}

export interface SongsetRenderMetrics {
  /** Owned, visible items in play order. */
  items: { id: string }[];
  songCount: number;
  /**
   * Last item's end on the laid-out timeline; unknown durations contribute 0;
   * null when all relevant sources are unknown.
   */
  songsetDurationSeconds: number | null;
}

/**
 * Shared render admission read (spec §2.7): owner-scoped visible items plus
 * the arranged selected-audio duration, with full analysis validation for
 * every non-NULL arrangement. Throws typed errors the render entry points map
 * to 409 (analysis) / 400 (over-limit is compared by the caller).
 */
export async function getSongsetRenderMetrics(
  songsetId: string,
  userId: number
): Promise<SongsetRenderMetrics> {
  const songset = await db.query.songsets.findFirst({
    where: and(eq(songsets.id, songsetId), eq(songsets.userId, userId)),
  });

  if (!songset) {
    throw new SongsetRenderValidationError(
      "SONGSET_NOT_FOUND",
      "Songset not found or access denied"
    );
  }

  const rows = await db
    .select(arrangementItemColumns)
    .from(songsetItems)
    .leftJoin(recordings, eq(songsetItems.recordingHashPrefix, recordings.hashPrefix))
    .where(eq(songsetItems.songsetId, songsetId))
    .orderBy(asc(songsetItems.position));

  const items = rows.filter((row) => !row.recordingDeletedAt);

  for (const row of items) {
    if (row.arrangementSteps == null) continue;
    if (row.durationSeconds == null) {
      throw new SongsetRenderValidationError("ARRANGEMENT_ANALYSIS_UNAVAILABLE");
    }
    const validated = validateSongArrangement(
      row.arrangementSteps,
      parseRecordingSections(row.recordingSections),
      parseRecordingBeats(row.recordingBeats),
      row.durationSeconds
    );
    if (!validated.ok) {
      throw analysisValidationError(validated.code);
    }
  }

  const glueJunctions = items.some((row) => row.glueEnabled)
    ? await loadResolvedGlueJunctions(songsetId, userId)
    : [];
  if (glueJunctions.some((junction) => junction.glueEnabled && junction.toItemId !== null)) {
    requireGlueRenderReady(
      songsetId,
      userId,
      buildGlueRenderPreflight({
        junctions: glueJunctions,
        items: await loadGlueRenderItemIdentities(songsetId),
        jobs: await loadGlueRenderJobFacts(songsetId, userId),
      })
    );
  }
  const glueByItemId = new Map(
    glueJunctions.map((junction) => [junction.fromItemId, junction] as const)
  );

  return {
    items: items.map((row) => ({ id: row.id })),
    songCount: items.length,
    songsetDurationSeconds: getSongsetDurationSeconds(
      items.map((row) => {
        const glue = glueByItemId.get(row.id);
        return toArrangementInput({
          ...row,
          glueTakeId: glue?.selectedTake?.id ?? null,
          glueSeconds: glue?.selectedTake?.seconds ?? null,
        });
      })
    ),
  };
}

export async function duplicateSongset(
  sourceId: string,
  userId: number,
  newName: string,
  newDescription: string | null
): Promise<SongsetDetail | null> {
  const source = await getSongset(sourceId, userId);
  if (!source) return null;

  const newId = nanoid();
  await db
    .insert(songsets)
    .values({ id: newId, userId, name: newName, description: newDescription })
    .returning();

  if (source.items.length > 0) {
    const itemsToInsert = source.items.map((item) => ({
      id: nanoid(),
      songsetId: newId,
      songId: item.songId,
      recordingHashPrefix: item.recordingHashPrefix,
      position: item.position,
      gapBeats: item.gapBeats,
      crossfadeEnabled: item.crossfadeEnabled,
      crossfadeDurationSeconds: item.crossfadeDurationSeconds,
      keyShiftSemitones: item.keyShiftSemitones,
      tempoRatio: item.tempoRatio,
      // Verbatim, even malformed: duplication must not drop or rewrite it.
      arrangementSteps: item.arrangementSteps,
      glueEnabled: item.glueEnabled,
      glueBeats: item.glueBeats,
      // The take belongs to the same owner; the resolver re-checks it against
      // the copied Junction, so a now-stale selection simply comes back stale.
      glueActiveTakeId: item.glueTakeId,
    }));

    await db.insert(songsetItems).values(itemsToInsert);
  }

  const duplicated = await getSongset(newId, userId);
  return duplicated;
}
