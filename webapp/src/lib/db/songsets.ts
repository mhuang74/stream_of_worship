import { db } from "@/db";
import { songsets, songsetItems, renderJobs } from "@/db/schema";
import { eq, and, desc, gt, sql } from "drizzle-orm";
import { nanoid } from "nanoid";

export type RenderState = "unrendered" | "rendering" | "fresh" | "stale" | "failed";

export interface SongsetListItem {
  id: string;
  name: string;
  description: string | null;
  createdAt: Date;
  updatedAt: Date;
  renderState: RenderState;
  itemCount: number;
  latestRenderJobId: string | null;
  lastFailedRenderJobId: string | null;
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
  song: {
    id: string;
    title: string;
    composer: string | null;
    lyricist: string | null;
    albumName: string | null;
    musicalKey: string | null;
  } | null;
  recording: null;
}

export interface SongsetDetail extends SongsetListItem {
  items: SongsetItemDetail[];
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
    const newerItem = await db.query.songsetItems.findFirst({
      where: and(
        eq(songsetItems.songsetId, songsetId),
        gt(songsetItems.createdAt, job.completedAt)
      ),
    });
    if (newerItem) return "stale";
    if (songset.updatedAt > job.completedAt) return "stale";
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
    with: { items: { columns: { id: true } } },
  });

  const countResult = await db
    .select({ count: sql<number>`count(*)` })
    .from(songsets)
    .where(eq(songsets.userId, userId));

  const total = countResult[0]?.count ?? 0;

  const mapped = await Promise.all(
    rows.map(async (row) => ({
      id: row.id,
      name: row.name,
      description: row.description,
      createdAt: row.createdAt,
      updatedAt: row.updatedAt,
      latestRenderJobId: row.latestRenderJobId,
      lastFailedRenderJobId: row.lastFailedRenderJobId,
      itemCount: row.items.length,
      renderState: await computeRenderState(row.id),
    }))
  );

  return { songsets: mapped, total };
}

export async function getSongset(
  id: string,
  userId: number
): Promise<SongsetDetail | null> {
  const row = await db.query.songsets.findFirst({
    where: and(eq(songsets.id, id), eq(songsets.userId, userId)),
    with: { items: { with: { song: true } } },
  });

  if (!row) return null;

  const sortedItems = [...row.items].sort((a, b) => a.position - b.position);

  const items: SongsetItemDetail[] = sortedItems.map((item) => ({
    id: item.id,
    songId: item.songId,
    recordingHashPrefix: item.recordingHashPrefix,
    position: item.position,
    gapBeats: item.gapBeats ?? null,
    crossfadeEnabled: item.crossfadeEnabled ?? null,
    crossfadeDurationSeconds: item.crossfadeDurationSeconds ?? null,
    keyShiftSemitones: item.keyShiftSemitones ?? null,
    tempoRatio: item.tempoRatio ?? null,
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
    recording: null,
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
    itemCount: items.length,
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
    itemCount: 0,
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
    with: { items: { columns: { id: true } } },
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
    itemCount: updated.items.length,
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

  const id = nanoid();
  await db.insert(songsetItems).values({ id, songsetId, ...data }).returning();

  await db.update(songsets).set({ updatedAt: new Date() }).where(eq(songsets.id, songsetId));

  const item = await db.query.songsetItems.findFirst({
    where: eq(songsetItems.id, id),
    with: { song: true },
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
    recording: null,
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
    with: { song: true },
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
    recording: null,
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
