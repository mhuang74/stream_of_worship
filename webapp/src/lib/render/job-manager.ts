import { db } from "@/db";
import { renderJobs, songsets, songsetItems, recordings } from "@/db/schema";
import { eq, and, lt } from "drizzle-orm";
import { nanoid } from "nanoid";
import { normalizeFontFamily } from "@/lib/constants";

export type RenderPhase =
  | "preparing"
  | "mixing_audio"
  | "rendering_frames"
  | "encoding_video"
  | "uploading"
  | "completed";

export interface RenderProgress {
  phase: RenderPhase;
  phaseIndex: number;
  totalPhases: number;
  estimatedTotalSeconds?: number;
  totalDurationSeconds?: number;
  startedAt?: Date;
  elapsedSeconds: number;
}

export interface CreateRenderJobInput {
  songsetId: string;
  template?: string;
  resolution?: string;
  audioEnabled?: boolean;
  videoEnabled?: boolean;
  fontSizePreset?: string;
  fontFamily?: string;
  includeTitleCard?: boolean;
  titleCardDurationSeconds?: number;
  titleCardLines?: string[];
}

export interface RenderJob {
  id: string;
  songsetId: string;
  userId: number;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  phase: RenderPhase | null;
  phaseIndex: number | null;
  totalPhases: number | null;
  elapsedSeconds: number | null;
  errorMessage: string | null;
  estimatedTotalSeconds: number | null;
  totalDurationSeconds: number | null;
  startedAt: Date | null;
  template: string;
  resolution: string;
  audioEnabled: boolean;
  videoEnabled: boolean;
  fontSizePreset: string;
  fontFamily: string;
  includeTitleCard: boolean;
  titleCardDurationSeconds: number | null;
  titleCardLines: string[] | null;
  mp3R2Key: string | null;
  mp4R2Key: string | null;
  chaptersR2Key: string | null;
  songCount: number | null;
  songsetDurationSeconds: number | null;
  createdAt: Date | null;
  updatedAt: Date | null;
  completedAt: Date | null;
}

const TOTAL_PHASES = 5;

const PHASE_ORDER: RenderPhase[] = [
  "preparing",
  "mixing_audio",
  "rendering_frames",
  "encoding_video",
  "uploading",
];

export function getPhaseIndex(phase: RenderPhase): number {
  if (phase === "completed") return TOTAL_PHASES;
  return PHASE_ORDER.indexOf(phase);
}

function mapRowToRenderJob(row: typeof renderJobs.$inferSelect): RenderJob {
  let titleCardLines: string[] | null = null;
  if (row.titleCardLines) {
    try {
      const parsed = JSON.parse(row.titleCardLines);
      titleCardLines = parsed && parsed.length > 0 ? parsed : null;
    } catch {
      titleCardLines = null;
    }
  }

  return {
    id: row.id,
    songsetId: row.songsetId,
    userId: row.userId,
    status: row.status as RenderJob["status"],
    phase: row.phase as RenderPhase | null,
    phaseIndex: row.phaseIndex,
    totalPhases: row.totalPhases,
    elapsedSeconds: row.elapsedSeconds,
    errorMessage: row.errorMessage,
    estimatedTotalSeconds: row.estimatedTotalSeconds ?? null,
    totalDurationSeconds: row.totalDurationSeconds ?? null,
    startedAt: row.startedAt,
    template: row.template,
    resolution: row.resolution,
    audioEnabled: row.audioEnabled ?? true,
    videoEnabled: row.videoEnabled ?? true,
    fontSizePreset: row.fontSizePreset,
    fontFamily: normalizeFontFamily(row.fontFamily),
    includeTitleCard: row.includeTitleCard ?? false,
    titleCardDurationSeconds: row.titleCardDurationSeconds,
    titleCardLines,
    mp3R2Key: row.mp3R2Key,
    mp4R2Key: row.mp4R2Key,
    chaptersR2Key: row.chaptersR2Key,
    songCount: row.songCount ?? null,
    songsetDurationSeconds: row.songsetDurationSeconds ?? null,
    createdAt: row.createdAt,
    updatedAt: row.updatedAt,
    completedAt: row.completedAt,
  };
}

export async function createRenderJob(
  userId: number,
  input: CreateRenderJobInput
): Promise<RenderJob> {
  await recoverOrphanedJobs();

  const songset = await db.query.songsets.findFirst({
    where: and(eq(songsets.id, input.songsetId), eq(songsets.userId, userId)),
  });

  if (!songset) {
    throw new Error("Songset not found or access denied");
  }

  const items = await db
    .select({
      durationSeconds: recordings.durationSeconds,
    })
    .from(songsetItems)
    .leftJoin(recordings, eq(songsetItems.recordingHashPrefix, recordings.hashPrefix))
    .where(eq(songsetItems.songsetId, input.songsetId));

  const songCount = items.length;
  const songsetDurationSeconds = Math.round(
    items.reduce((sum, item) => sum + (item.durationSeconds ?? 0), 0)
  ) || null;

  const id = nanoid();
  const now = new Date();

  const [job] = await db
    .insert(renderJobs)
    .values({
      id,
      songsetId: input.songsetId,
      userId,
      status: "queued",
      phase: "preparing",
      phaseIndex: 0,
      totalPhases: TOTAL_PHASES,
      elapsedSeconds: 0,
      estimatedTotalSeconds: null,
      totalDurationSeconds: null,
      startedAt: null,
      template: input.template ?? "dark",
      resolution: input.resolution ?? "720p",
      audioEnabled: input.audioEnabled ?? true,
      videoEnabled: input.videoEnabled ?? true,
      fontSizePreset: input.fontSizePreset ?? "M",
      fontFamily: input.fontFamily ?? "noto_serif_tc",
      includeTitleCard: input.includeTitleCard ?? false,
      titleCardDurationSeconds: input.titleCardDurationSeconds ?? null,
      titleCardLines:
        input.titleCardLines && input.titleCardLines.length > 0
          ? JSON.stringify(input.titleCardLines)
          : null,
      songCount,
      songsetDurationSeconds,
      createdAt: now,
      updatedAt: now,
    })
    .returning();

  await db
    .update(songsets)
    .set({
      latestRenderJobId: id,
      updatedAt: now,
    })
    .where(eq(songsets.id, input.songsetId));

  return mapRowToRenderJob(job);
}

export async function getRenderJob(
  id: string,
  userId: number
): Promise<RenderJob | null> {
  const job = await db.query.renderJobs.findFirst({
    where: and(eq(renderJobs.id, id), eq(renderJobs.userId, userId)),
  });

  if (!job) {
    return null;
  }

  return mapRowToRenderJob(job);
}

export async function updateRenderProgress(
  id: string,
  userId: number,
  progress: Partial<RenderProgress>
): Promise<RenderJob | null> {
  const job = await getRenderJob(id, userId);
  if (!job) return null;

  const updates: Partial<typeof renderJobs.$inferInsert> = {
    updatedAt: new Date(),
  };

  if (progress.phase !== undefined) {
    updates.phase = progress.phase;
    updates.phaseIndex = getPhaseIndex(progress.phase);
  }

  if (progress.estimatedTotalSeconds !== undefined) {
    updates.estimatedTotalSeconds = progress.estimatedTotalSeconds;
  }

  if (progress.totalDurationSeconds !== undefined) {
    updates.totalDurationSeconds = progress.totalDurationSeconds;
  }

  if (progress.startedAt !== undefined) {
    updates.startedAt = progress.startedAt;
  }

  if (progress.elapsedSeconds !== undefined) {
    updates.elapsedSeconds = progress.elapsedSeconds;
  }

  const [updated] = await db
    .update(renderJobs)
    .set(updates)
    .where(and(eq(renderJobs.id, id), eq(renderJobs.userId, userId)))
    .returning();

  if (!updated) return null;

  return mapRowToRenderJob(updated);
}

export async function completeRenderJob(
  id: string,
  userId: number,
  output: {
    mp3R2Key?: string;
    mp4R2Key?: string;
    chaptersR2Key?: string;
  }
): Promise<RenderJob | null> {
  const now = new Date();

  const job = await getRenderJob(id, userId);
  if (!job) return null;

  const finalElapsedSeconds = job.startedAt
    ? (now.getTime() - job.startedAt.getTime()) / 1000
    : null;

  const result = await db.transaction(async (tx) => {
    const [updated] = await tx
      .update(renderJobs)
      .set({
        status: "completed",
        phase: "completed",
        phaseIndex: TOTAL_PHASES,
        elapsedSeconds: finalElapsedSeconds,
        mp3R2Key: output.mp3R2Key ?? null,
        mp4R2Key: output.mp4R2Key ?? null,
        chaptersR2Key: output.chaptersR2Key ?? null,
        completedAt: now,
        updatedAt: now,
      })
      .where(and(eq(renderJobs.id, id), eq(renderJobs.userId, userId)))
      .returning();

    if (!updated) return null;

    await tx
      .update(songsets)
      .set({
        lastCompletedRenderJobId: id,
        updatedAt: now,
      })
      .where(eq(songsets.id, updated.songsetId));

    return updated;
  });

  if (!result) return null;

  return mapRowToRenderJob(result);
}

export async function failRenderJob(
  id: string,
  userId: number,
  errorMessage: string
): Promise<RenderJob | null> {
  const now = new Date();

  const [updated] = await db
    .update(renderJobs)
    .set({
      status: "failed",
      errorMessage,
      updatedAt: now,
    })
    .where(and(eq(renderJobs.id, id), eq(renderJobs.userId, userId)))
    .returning();

  if (!updated) return null;

  // Update songset with failed job reference
  await db
    .update(songsets)
    .set({
      lastFailedRenderJobId: id,
      updatedAt: now,
    })
    .where(eq(songsets.id, updated.songsetId));

  return mapRowToRenderJob(updated);
}

export async function cancelRenderJob(
  id: string,
  userId: number
): Promise<RenderJob | null> {
  const job = await getRenderJob(id, userId);
  if (!job) return null;

  // Can only cancel queued or running jobs
  if (job.status !== "queued" && job.status !== "running") {
    throw new Error(`Cannot cancel job with status: ${job.status}`);
  }

  const now = new Date();

  const [updated] = await db
    .update(renderJobs)
    .set({
      status: "cancelled",
      updatedAt: now,
    })
    .where(and(eq(renderJobs.id, id), eq(renderJobs.userId, userId)))
    .returning();

  if (!updated) return null;

  return mapRowToRenderJob(updated);
}

export async function startRenderJob(
  id: string,
  userId: number
): Promise<RenderJob | null> {
  const [updated] = await db
    .update(renderJobs)
    .set({
      status: "running",
      updatedAt: new Date(),
    })
    .where(and(eq(renderJobs.id, id), eq(renderJobs.userId, userId)))
    .returning();

  if (!updated) return null;

  return mapRowToRenderJob(updated);
}

const ORPHANED_JOB_THRESHOLD_MINUTES = 15;

export async function recoverOrphanedJobs(): Promise<number> {
  const threshold = new Date(
    Date.now() - ORPHANED_JOB_THRESHOLD_MINUTES * 60 * 1000
  );

  const orphaned = await db
    .select({ id: renderJobs.id, songsetId: renderJobs.songsetId })
    .from(renderJobs)
    .where(
      and(
        eq(renderJobs.status, "running"),
        lt(renderJobs.updatedAt, threshold)
      )
    );

  if (orphaned.length === 0) return 0;

  const now = new Date();

  for (const job of orphaned) {
    await db
      .update(renderJobs)
      .set({
        status: "failed",
        errorMessage: `Job timed out after ${ORPHANED_JOB_THRESHOLD_MINUTES} minutes without progress`,
        updatedAt: now,
      })
      .where(eq(renderJobs.id, job.id));

    await db
      .update(songsets)
      .set({
        lastFailedRenderJobId: job.id,
        updatedAt: now,
      })
      .where(eq(songsets.id, job.songsetId));
  }

  return orphaned.length;
}
