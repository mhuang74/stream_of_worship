import { db } from "@/db";
import { renderJobs, songsets } from "@/db/schema";
import { eq, and } from "drizzle-orm";
import { nanoid } from "nanoid";

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
  percentComplete: number;
  estimatedSecondsLeft: number;
  elapsedSeconds: number;
}

export interface CreateRenderJobInput {
  songsetId: string;
  template?: string;
  resolution?: string;
  audioEnabled?: boolean;
  videoEnabled?: boolean;
  fontSizePreset?: string;
  includeTitleCard?: boolean;
  titleCardDurationSeconds?: number;
}

export interface RenderJob {
  id: string;
  songsetId: string;
  userId: number;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  phase: RenderPhase | null;
  phaseIndex: number | null;
  totalPhases: number | null;
  percentComplete: number;
  estimatedSecondsLeft: number | null;
  elapsedSeconds: number | null;
  errorMessage: string | null;
  template: string;
  resolution: string;
  audioEnabled: boolean;
  videoEnabled: boolean;
  fontSizePreset: string;
  includeTitleCard: boolean;
  titleCardDurationSeconds: number | null;
  mp3R2Key: string | null;
  mp4R2Key: string | null;
  chaptersR2Key: string | null;
  createdAt: Date;
  updatedAt: Date;
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
  return {
    id: row.id,
    songsetId: row.songsetId,
    userId: row.userId,
    status: row.status as RenderJob["status"],
    phase: row.phase as RenderPhase | null,
    phaseIndex: row.phaseIndex,
    totalPhases: row.totalPhases,
    percentComplete: row.percentComplete ?? 0,
    estimatedSecondsLeft: row.estimatedSecondsLeft,
    elapsedSeconds: row.elapsedSeconds,
    errorMessage: row.errorMessage,
    template: row.template,
    resolution: row.resolution,
    audioEnabled: row.audioEnabled ?? true,
    videoEnabled: row.videoEnabled ?? true,
    fontSizePreset: row.fontSizePreset,
    includeTitleCard: row.includeTitleCard ?? false,
    titleCardDurationSeconds: row.titleCardDurationSeconds,
    mp3R2Key: row.mp3R2Key,
    mp4R2Key: row.mp4R2Key,
    chaptersR2Key: row.chaptersR2Key,
    createdAt: row.createdAt,
    updatedAt: row.updatedAt,
    completedAt: row.completedAt,
  };
}

export async function createRenderJob(
  userId: number,
  input: CreateRenderJobInput
): Promise<RenderJob> {
  // Verify songset exists and belongs to user
  const songset = await db.query.songsets.findFirst({
    where: and(eq(songsets.id, input.songsetId), eq(songsets.userId, userId)),
  });

  if (!songset) {
    throw new Error("Songset not found or access denied");
  }

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
      percentComplete: 0,
      estimatedSecondsLeft: null,
      elapsedSeconds: 0,
      template: input.template ?? "dark",
      resolution: input.resolution ?? "720p",
      audioEnabled: input.audioEnabled ?? true,
      videoEnabled: input.videoEnabled ?? true,
      fontSizePreset: input.fontSizePreset ?? "M",
      includeTitleCard: input.includeTitleCard ?? false,
      titleCardDurationSeconds: input.titleCardDurationSeconds ?? null,
      createdAt: now,
      updatedAt: now,
    })
    .returning();

  // Update songset with latest render job
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

  if (progress.percentComplete !== undefined) {
    updates.percentComplete = progress.percentComplete;
  }

  if (progress.estimatedSecondsLeft !== undefined) {
    updates.estimatedSecondsLeft = progress.estimatedSecondsLeft;
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

  const [updated] = await db
    .update(renderJobs)
    .set({
      status: "completed",
      phase: "completed",
      phaseIndex: TOTAL_PHASES,
      percentComplete: 100,
      mp3R2Key: output.mp3R2Key ?? null,
      mp4R2Key: output.mp4R2Key ?? null,
      chaptersR2Key: output.chaptersR2Key ?? null,
      completedAt: now,
      updatedAt: now,
    })
    .where(and(eq(renderJobs.id, id), eq(renderJobs.userId, userId)))
    .returning();

  if (!updated) return null;

  return mapRowToRenderJob(updated);
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

  return {
    id: updated.id,
    songsetId: updated.songsetId,
    userId: updated.userId,
    status: updated.status as RenderJob["status"],
    phase: updated.phase as RenderPhase | null,
    phaseIndex: updated.phaseIndex,
    totalPhases: updated.totalPhases,
    percentComplete: updated.percentComplete ?? 0,
    estimatedSecondsLeft: updated.estimatedSecondsLeft,
    elapsedSeconds: updated.elapsedSeconds,
    errorMessage: updated.errorMessage,
    template: updated.template,
    resolution: updated.resolution,
    audioEnabled: updated.audioEnabled ?? true,
    videoEnabled: updated.videoEnabled ?? true,
    fontSizePreset: updated.fontSizePreset,
    includeTitleCard: updated.includeTitleCard ?? false,
    titleCardDurationSeconds: updated.titleCardDurationSeconds,
    mp3R2Key: updated.mp3R2Key,
    mp4R2Key: updated.mp4R2Key,
  return mapRowToRenderJob(updated);
}

/**
 * Update render job with R2 keys after upload.
 *
 * @param id - Render job ID
 * @param userId - User ID
 * @param r2Keys - R2 keys for uploaded artifacts
 * @returns Updated render job or null if not found
 */
export async function updateRenderJobR2Keys(
  id: string,
  userId: number,
  r2Keys: {
    mp3R2Key?: string;
    mp4R2Key?: string;
    chaptersR2Key?: string;
  }
): Promise<RenderJob | null> {
  const [updated] = await db
    .update(renderJobs)
    .set({
      mp3R2Key: r2Keys.mp3R2Key ?? null,
      mp4R2Key: r2Keys.mp4R2Key ?? null,
      chaptersR2Key: r2Keys.chaptersR2Key ?? null,
      updatedAt: new Date(),
    })
    .where(and(eq(renderJobs.id, id), eq(renderJobs.userId, userId)))
    .returning();

  if (!updated) return null;

  return mapRowToRenderJob(updated);
}

/**
 * Update songset with latest render job ID.
 * Called when a new render job is created or completed.
 *
 * @param songsetId - Songset ID
 * @param renderJobId - Render job ID
 */
export async function updateSongsetLatestRenderJob(
  songsetId: string,
  renderJobId: string
): Promise<void> {
  await db
    .update(songsets)
    .set({
      latestRenderJobId: renderJobId,
      updatedAt: new Date(),
    })
    .where(eq(songsets.id, songsetId));
}

/**
 * Update songset with failed render job ID.
 * Called when a render job fails.
 *
 * @param songsetId - Songset ID
 * @param renderJobId - Failed render job ID
 */
export async function updateSongsetFailedRenderJob(
  songsetId: string,
  renderJobId: string
): Promise<void> {
  await db
    .update(songsets)
    .set({
      lastFailedRenderJobId: renderJobId,
      updatedAt: new Date(),
    })
    .where(eq(songsets.id, songsetId));
}

/**
 * Clear failed render job ID from songset.
 * Called when a new render job succeeds (to clear previous failure).
 *
 * @param songsetId - Songset ID
 */
export async function clearSongsetFailedRenderJob(songsetId: string): Promise<void> {
  await db
    .update(songsets)
    .set({
      lastFailedRenderJobId: null,
      updatedAt: new Date(),
    })
    .where(eq(songsets.id, songsetId));
}

/**
 * Get the latest render job for a songset.
 *
 * @param songsetId - Songset ID
 * @param userId - User ID
 * @returns Latest render job or null
 */
export async function getLatestRenderJobForSongset(
  songsetId: string,
  userId: number
): Promise<RenderJob | null> {
  const songset = await db.query.songsets.findFirst({
    where: and(eq(songsets.id, songsetId), eq(songsets.userId, userId)),
  });

  if (!songset?.latestRenderJobId) {
    return null;
  }

  return getRenderJob(songset.latestRenderJobId, userId);
}

/**
 * Check if render artifacts exist in R2 for a job.
 *
 * @param renderJobId - Render job ID
 * @returns Object with existence status for each artifact type
 */
export async function checkRenderArtifactsExist(
  renderJobId: string
): Promise<{
  mp3Exists: boolean;
  mp4Exists: boolean;
  chaptersExists: boolean;
}> {
  const { R2Uploader } = await import("./uploader");
  const uploader = new R2Uploader();

  const [mp3Exists, mp4Exists, chaptersExists] = await Promise.all([
    uploader.fileExists(R2Uploader.getMp3Key(renderJobId)),
    uploader.fileExists(R2Uploader.getMp4Key(renderJobId)),
    uploader.fileExists(R2Uploader.getChaptersKey(renderJobId)),
  ]);

  return { mp3Exists, mp4Exists, chaptersExists };
}
