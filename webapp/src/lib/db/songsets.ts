import { db } from "@/db";
import {
  lyricMarks,
  songsets,
  songsetItems,
  renderJobs,
  songs,
  recordings,
  userSettings,
} from "@/db/schema";
import { eq, and, desc, gt, sql, asc } from "drizzle-orm";
import { nanoid } from "nanoid";
import { SONGSET_MAX_SONGS } from "@/lib/constants";

export type RenderState = "unrendered" | "rendering" | "fresh" | "stale" | "failed";

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
      durationSeconds: recordings.durationSeconds,
      tempoBpm: recordings.tempoBpm,
      recordingMusicalKey: recordings.musicalKey,
      recordingDeletedAt: recordings.deletedAt,
    })
    .from(songsetItems)
    .leftJoin(songs, eq(songsetItems.songId, songs.id))
    .leftJoin(recordings, eq(songsetItems.recordingHashPrefix, recordings.hashPrefix))
    .where(eq(songsetItems.songsetId, songsetId))
    .orderBy(songsetItems.position);

  const publicItems: PublicSongsetItem[] = items
    .filter((item) => !item.recordingDeletedAt)
    .map((item) => ({
    id: item.id,
    position: item.position,
    songTitle: item.songTitle,
    composer: item.composer,
    lyricist: item.lyricist,
    albumName: item.albumName,
    songMusicalKey: item.songMusicalKey,
    durationSeconds: item.durationSeconds,
    tempoBpm: item.tempoBpm,
    recordingMusicalKey: item.recordingMusicalKey,
  }));

  const totalDurationSeconds = items.reduce(
    (sum, item) => sum + (item.durationSeconds ?? 0),
    0
  ) || null;

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
}

export interface SongsetItemRecording {
  contentHash: string;
  durationSeconds: number | null;
  tempoBpm: number | null;
  musicalKey: string | null;
  r2AudioUrl: string | null;
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
  markedLineCount: number;
  song: {
    id: string;
    title: string;
    composer: string | null;
    lyricist: string | null;
    albumName: string | null;
    musicalKey: string | null;
  } | null;
  recording: SongsetItemRecording | null;
}

export interface SongsetDetail extends SongsetListItem {
  items: SongsetItemDetail[];
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
    songTitles: string[];
    lastCompletedRenderJobId: string | null;
  };
  userSettings: {
    defaultVideoTemplate: string;
    defaultResolution: string;
    defaultFontSizePreset: string;
    defaultFontFamily: string;
  } | null;
  latestJob: RenderJobSummary | null;
  previousCompletedJob: RenderJobSummary | null;
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

function mapRenderStateFromSnapshot(input: {
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

export async function listSongsetSummaries(
  userId: number,
  limit = 50,
  offset = 0
): Promise<{ songsets: SongsetListItem[]; total: number }> {
  return timePageLoad("listSongsetSummaries", async () => {
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
        durationSeconds: sql<number | null>`nullif(sum(coalesce(${recordings.durationSeconds}, 0)) filter (where ${recordings.deletedAt} is null), 0)`,
        latestItemUpdatedAt: sql<Date | null>`max(${songsetItems.updatedAt}) filter (where ${recordings.deletedAt} is null)`,
        latestJobStatus: renderJobs.status,
        latestJobCompletedAt: renderJobs.completedAt,
      })
      .from(songsets)
      .leftJoin(songsetItems, eq(songsetItems.songsetId, songsets.id))
      .leftJoin(recordings, eq(songsetItems.recordingHashPrefix, recordings.hashPrefix))
      .leftJoin(renderJobs, eq(renderJobs.id, songsets.latestRenderJobId))
      .where(eq(songsets.userId, userId))
      .groupBy(
        songsets.id,
        songsets.name,
        songsets.description,
        songsets.createdAt,
        songsets.updatedAt,
        songsets.latestRenderJobId,
        songsets.lastFailedRenderJobId,
        songsets.lastCompletedRenderJobId,
        renderJobs.status,
        renderJobs.completedAt
      )
      .orderBy(desc(songsets.updatedAt))
      .limit(limit)
      .offset(offset);

    const countResult = await db
      .select({ count: sql<number>`count(*)::int` })
      .from(songsets)
      .where(eq(songsets.userId, userId));

    return {
      total: Number(countResult[0]?.count ?? 0),
      songsets: rows.map((row) => ({
        id: row.id,
        name: row.name,
        description: row.description,
        createdAt: row.createdAt,
        updatedAt: row.updatedAt,
        latestRenderJobId: row.latestRenderJobId,
        lastFailedRenderJobId: row.lastFailedRenderJobId,
        lastCompletedRenderJobId: row.lastCompletedRenderJobId,
        itemCount: Number(row.itemCount ?? 0),
        durationSeconds: row.durationSeconds == null ? null : Number(row.durationSeconds),
        renderState: mapRenderStateFromSnapshot({
          latestRenderJobId: row.latestRenderJobId,
          lastFailedRenderJobId: row.lastFailedRenderJobId,
          latestJobStatus: row.latestJobStatus,
          latestJobCompletedAt: row.latestJobCompletedAt,
          latestItemUpdatedAt: row.latestItemUpdatedAt,
        }),
      })),
    };
  });
}

export async function getSongsetEditorData(
  id: string,
  userId: number
): Promise<SongsetDetail | null> {
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
        updatedAt: songsetItems.updatedAt,
        songTitle: songs.title,
        composer: songs.composer,
        lyricist: songs.lyricist,
        albumName: songs.albumName,
        songMusicalKey: songs.musicalKey,
        recordingContentHash: recordings.contentHash,
        durationSeconds: recordings.durationSeconds,
        tempoBpm: recordings.tempoBpm,
        recordingMusicalKey: recordings.musicalKey,
        r2AudioUrl: recordings.r2AudioUrl,
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
        recordings.musicalKey,
        recordings.r2AudioUrl,
        recordings.deletedAt
      )
      .orderBy(asc(songsetItems.position));

    const visibleItemRows = itemRows.filter((item) => !item.recordingDeletedAt);
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
      markedLineCount: Number(item.markedLineCount ?? 0),
      song: item.songTitle
        ? {
            id: item.songId,
            title: item.songTitle,
            composer: item.composer,
            lyricist: item.lyricist,
            albumName: item.albumName,
            musicalKey: item.songMusicalKey,
          }
        : null,
      recording: item.recordingContentHash
        ? {
            contentHash: item.recordingContentHash,
            durationSeconds: item.durationSeconds,
            tempoBpm: item.tempoBpm,
            musicalKey: item.recordingMusicalKey,
            r2AudioUrl: item.r2AudioUrl,
          }
        : null,
    }));

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
      durationSeconds:
        items.reduce((sum, item) => sum + (item.recording?.durationSeconds ?? 0), 0) || null,
      renderState: mapRenderStateFromSnapshot({
        latestRenderJobId: row.latestRenderJobId,
        lastFailedRenderJobId: row.lastFailedRenderJobId,
        latestJobStatus: row.latestJobStatus,
        latestJobCompletedAt: row.latestJobCompletedAt,
        latestItemUpdatedAt,
      }),
      items,
    };
  });
}

export async function getRenderPageData(
  id: string,
  userId: number
): Promise<RenderPageData | null> {
  return timePageLoad("getRenderPageData", async () => {
    const detail = await getSongsetEditorData(id, userId);
    if (!detail) return null;

    const [settingsRow, latestJobRow, previousCompletedJobRow] = await Promise.all([
      db.query.userSettings.findFirst({
        where: eq(userSettings.userId, userId),
      }),
      detail.latestRenderJobId
        ? db.query.renderJobs.findFirst({
            where: and(eq(renderJobs.id, detail.latestRenderJobId), eq(renderJobs.userId, userId)),
          })
        : Promise.resolve(null),
      detail.lastCompletedRenderJobId
        ? db.query.renderJobs.findFirst({
            where: and(
              eq(renderJobs.id, detail.lastCompletedRenderJobId),
              eq(renderJobs.userId, userId)
            ),
          })
        : Promise.resolve(null),
    ]);

    return {
      songset: {
        id: detail.id,
        name: detail.name,
        description: detail.description,
        markedLineCount: detail.items.reduce(
          (sum, item) => sum + (item.markedLineCount ?? 0),
          0
        ),
        renderState: detail.renderState,
        songTitles: detail.items.map((item) => item.song?.title ?? "Unknown Song"),
        lastCompletedRenderJobId: detail.lastCompletedRenderJobId,
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
    };
  });
}

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

export async function listSongsets(
  userId: number,
  limit = 50,
  offset = 0
): Promise<{ songsets: SongsetListItem[]; total: number }> {
  const rows = await db.query.songsets.findMany({
    where: eq(songsets.userId, userId),
    orderBy: [desc(songsets.updatedAt)],
    limit,
    offset,
    with: {
      items: {
        columns: { id: true, createdAt: true },
        with: {
          recording: { columns: { durationSeconds: true, deletedAt: true } },
        },
      },
      renderJobs: {
        columns: {
          id: true,
          status: true,
          completedAt: true,
        },
        orderBy: [desc(renderJobs.createdAt)],
        limit: 1,
      },
    },
  });

  const countResult = await db
    .select({ count: sql<number>`count(*)` })
    .from(songsets)
    .where(eq(songsets.userId, userId));

  const total = countResult[0]?.count ?? 0;

  const mapped = rows.map((row) => {
    const latestJob = row.renderJobs[0];
    let renderState: RenderState = "unrendered";

    if (row.latestRenderJobId && latestJob) {
      if (latestJob.status === "queued" || latestJob.status === "running") {
        renderState = "rendering";
      } else if (
        latestJob.status === "failed" ||
        row.lastFailedRenderJobId === row.latestRenderJobId
      ) {
        renderState = "failed";
      } else if (latestJob.status === "completed") {
          if (latestJob.completedAt) {
            const hasNewerItem = row.items.some(
              (item) => (item as { createdAt: Date }).createdAt > latestJob.completedAt!
            );
            if (hasNewerItem) {
              renderState = "stale";
            } else {
              renderState = "fresh";
            }
          } else {
            renderState = "fresh";
          }
        }
    }

    return {
      id: row.id,
      name: row.name,
      description: row.description,
      createdAt: row.createdAt,
      updatedAt: row.updatedAt,
      latestRenderJobId: row.latestRenderJobId,
      lastFailedRenderJobId: row.lastFailedRenderJobId,
      lastCompletedRenderJobId: row.lastCompletedRenderJobId,
      itemCount: row.items.filter((item) => !item.recording?.deletedAt).length,
      durationSeconds: row.items
        .filter((item) => !item.recording?.deletedAt)
        .reduce(
          (sum, item) => sum + (item.recording?.durationSeconds ?? 0),
          0
        ) || null,
      renderState,
    };
  });

  return { songsets: mapped, total };
}

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

  const items: SongsetItemDetail[] = sortedItems
    .filter((item) => !item.recording?.deletedAt)
    .map((item) => ({
    id: item.id,
    songId: item.songId,
    recordingHashPrefix: item.recordingHashPrefix,
    position: item.position,
    gapBeats: item.gapBeats ?? null,
    crossfadeEnabled: item.crossfadeEnabled ?? null,
    crossfadeDurationSeconds: item.crossfadeDurationSeconds ?? null,
    keyShiftSemitones: item.keyShiftSemitones ?? null,
    tempoRatio: item.tempoRatio ?? null,
    markedLineCount: item.recording?.lyricMarks.length ?? 0,
    song: item.song
      ? {
          id: item.song.id,
          title: item.song.title,
          composer: item.song.composer,
          lyricist: item.song.lyricist,
          albumName: item.song.albumName,
          musicalKey: item.song.musicalKey,
        }
      : null,
    recording: item.recording
      ? {
          contentHash: item.recording.contentHash,
          durationSeconds: item.recording.durationSeconds,
          tempoBpm: item.recording.tempoBpm,
          musicalKey: item.recording.musicalKey,
          r2AudioUrl: item.recording.r2AudioUrl,
        }
      : null,
  }));

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
    durationSeconds: items.reduce(
      (sum, item) => sum + (item.recording?.durationSeconds ?? 0),
      0
    ) || null,
    renderState,
    items,
  };
}

export async function createSongset(
  userId: number,
  data: { name: string; description?: string }
): Promise<SongsetListItem> {
  const id = nanoid();
  const rows = await db
    .insert(songsets)
    .values({ id, userId, name: data.name, description: data.description ?? null })
    .returning();
  const row = rows[0];

  return {
    id: row.id,
    name: row.name,
    description: row.description,
    createdAt: row.createdAt,
    updatedAt: row.updatedAt,
    latestRenderJobId: row.latestRenderJobId,
    lastFailedRenderJobId: row.lastFailedRenderJobId,
    lastCompletedRenderJobId: row.lastCompletedRenderJobId,
    itemCount: 0,
    durationSeconds: null,
    renderState: "unrendered",
  };
}

export async function updateSongset(
  id: string,
  userId: number,
  patch: { name?: string; description?: string | null }
): Promise<SongsetListItem | null> {
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
        columns: { id: true },
        with: {
          recording: { columns: { durationSeconds: true, deletedAt: true } },
        },
      },
    },
  });

  if (!updated) return null;

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
    itemCount: updated.items.filter((item) => !item.recording?.deletedAt).length,
    durationSeconds: updated.items
      .filter((item) => !item.recording?.deletedAt)
      .reduce(
        (sum, item) => sum + (item.recording?.durationSeconds ?? 0),
        0
      ) || null,
    renderState,
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

export async function addSongsetItem(
  songsetId: string,
  userId: number,
  data: {
    songId: string;
    position: number;
    recordingHashPrefix?: string | null;
    gapBeats?: number | null;
    crossfadeEnabled?: number | null;
    crossfadeDurationSeconds?: number | null;
    keyShiftSemitones?: number | null;
    tempoRatio?: number | null;
  }
): Promise<SongsetItemDetail | null> {
  const songset = await db.query.songsets.findFirst({
    where: and(eq(songsets.id, songsetId), eq(songsets.userId, userId)),
  });
  if (!songset) return null;

  const currentCount = await db.query.songsetItems.findMany({
    where: eq(songsetItems.songsetId, songsetId),
    columns: { id: true },
  });

  if (currentCount.length >= SONGSET_MAX_SONGS) {
    throw new Error(`Songset already has maximum of ${SONGSET_MAX_SONGS} songs`);
  }

  const id = nanoid();
  await db.insert(songsetItems).values({ id, songsetId, ...data }).returning();

  await db.update(songsets).set({ updatedAt: new Date() }).where(eq(songsets.id, songsetId));

  const item = await db.query.songsetItems.findFirst({
    where: eq(songsetItems.id, id),
    with: { song: true, recording: true },
  });

  if (!item) return null;

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
    markedLineCount: 0,
    song: item.song
      ? {
          id: item.song.id,
          title: item.song.title,
          composer: item.song.composer,
          lyricist: item.song.lyricist,
          albumName: item.song.albumName,
          musicalKey: item.song.musicalKey,
        }
      : null,
    recording: item.recording
      ? {
          contentHash: item.recording.contentHash,
          durationSeconds: item.recording.durationSeconds,
          tempoBpm: item.recording.tempoBpm,
          musicalKey: item.recording.musicalKey,
          r2AudioUrl: item.recording.r2AudioUrl,
        }
      : null,
  };
}

export async function updateSongsetItem(
  itemId: string,
  songsetId: string,
  userId: number,
  patch: Partial<{
    position: number;
    recordingHashPrefix: string | null;
    gapBeats: number | null;
    crossfadeEnabled: number | null;
    crossfadeDurationSeconds: number | null;
    keyShiftSemitones: number | null;
    tempoRatio: number | null;
  }>
): Promise<SongsetItemDetail | null> {
  const item = await db.query.songsetItems.findFirst({
    where: eq(songsetItems.id, itemId),
    with: { songset: true },
  });

  if (!item || item.songsetId !== songsetId || item.songset.userId !== userId) return null;

  await db.update(songsetItems).set(patch).where(eq(songsetItems.id, itemId)).returning();

  await db.update(songsets).set({ updatedAt: new Date() }).where(eq(songsets.id, songsetId));

  const updated = await db.query.songsetItems.findFirst({
    where: eq(songsetItems.id, itemId),
    with: { song: true, recording: true },
  });

  if (!updated) return null;

  return {
    id: updated.id,
    songId: updated.songId,
    recordingHashPrefix: updated.recordingHashPrefix,
    position: updated.position,
    gapBeats: updated.gapBeats ?? null,
    crossfadeEnabled: updated.crossfadeEnabled ?? null,
    crossfadeDurationSeconds: updated.crossfadeDurationSeconds ?? null,
    keyShiftSemitones: updated.keyShiftSemitones ?? null,
    tempoRatio: updated.tempoRatio ?? null,
    markedLineCount: 0,
    song: updated.song
      ? {
          id: updated.song.id,
          title: updated.song.title,
          composer: updated.song.composer,
          lyricist: updated.song.lyricist,
          albumName: updated.song.albumName,
          musicalKey: updated.song.musicalKey,
        }
      : null,
    recording: updated.recording
      ? {
          contentHash: updated.recording.contentHash,
          durationSeconds: updated.recording.durationSeconds,
          tempoBpm: updated.recording.tempoBpm,
          musicalKey: updated.recording.musicalKey,
          r2AudioUrl: updated.recording.r2AudioUrl,
        }
      : null,
  };
}

export async function deleteSongsetItem(
  itemId: string,
  songsetId: string,
  userId: number
): Promise<boolean> {
  const item = await db.query.songsetItems.findFirst({
    where: eq(songsetItems.id, itemId),
    with: { songset: true },
  });

  if (!item || item.songsetId !== songsetId || item.songset.userId !== userId) return false;

  await db.delete(songsetItems).where(eq(songsetItems.id, itemId));
  await db.update(songsets).set({ updatedAt: new Date() }).where(eq(songsets.id, songsetId));

  return true;
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
    }));

    await db.insert(songsetItems).values(itemsToInsert);
  }

  const duplicated = await getSongset(newId, userId);
  return duplicated;
}
