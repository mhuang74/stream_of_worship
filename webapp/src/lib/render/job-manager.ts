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

  return {
    id: job.id,
    songsetId: job.songsetId,
    userId: job.userId,
    status: job.status as RenderJob["status"],
    phase: job.phase as RenderPhase | null,
    phaseIndex: job.phaseIndex,
    totalPhases: job.totalPhases,
    percentComplete: job.percentComplete ?? 0,
    estimatedSecondsLeft: job.estimatedSecondsLeft,
    elapsedSeconds: job.elapsedSeconds,
    errorMessage: job.errorMessage,
    template: job.template,
    resolution: job.resolution,
    audioEnabled: job.audioEnabled ?? true,
    videoEnabled: job.videoEnabled ?? true,
    fontSizePreset: job.fontSizePreset,
    includeTitleCard: job.includeTitleCard ?? false,
    titleCardDurationSeconds: job.titleCardDurationSeconds,
    mp3R2Key: job.mp3R2Key,
    mp4R2Key: job.mp4R2Key,
    chaptersR2Key: job.chaptersR2Key,
    createdAt: job.createdAt,
    updatedAt: job.updatedAt,
    completedAt: job.completedAt,
  };
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

  return {
    id: job.id,
    songsetId: job.songsetId,
    userId: job.userId,
    status: job.status as RenderJob["status"],
    phase: job.phase as RenderPhase | null,
    phaseIndex: job.phaseIndex,
    totalPhases: job.totalPhases,
    percentComplete: job.percentComplete ?? 0,
    estimatedSecondsLeft: job.estimatedSecondsLeft,
    elapsedSeconds: job.elapsedSeconds,
    errorMessage: job.errorMessage,
    template: job.template,
    resolution: job.resolution,
    audioEnabled: job.audioEnabled ?? true,
    videoEnabled: job.videoEnabled ?? true,
    fontSizePreset: job.fontSizePreset,
    includeTitleCard: job.includeTitleCard ?? false,
    titleCardDurationSeconds: job.titleCardDurationSeconds,
    mp3R2Key: job.mp3R2Key,
    mp4R2Key: job.mp4R2Key,
    chaptersR2Key: job.chaptersR2Key,
    createdAt: job.createdAt,
    updatedAt: job.updatedAt,
    completedAt: job.completedAt,
  };
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
    chaptersR2Key: updated.chaptersR2Key,
    createdAt: updated.createdAt,
    updatedAt: updated.updatedAt,
    completedAt: updated.completedAt,
  };
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
    chaptersR2Key: updated.chaptersR2Key,
    createdAt: updated.createdAt,
    updatedAt: updated.updatedAt,
    completedAt: updated.completedAt,
  };
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
    chaptersR2Key: updated.chaptersR2Key,
    createdAt: updated.createdAt,
    updatedAt: updated.updatedAt,
    completedAt: updated.completedAt,
  };
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
    chaptersR2Key: updated.chaptersR2Key,
    createdAt: updated.createdAt,
    updatedAt: updated.updatedAt,
    completedAt: updated.completedAt,
  };
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
    chaptersR2Key: updated.chaptersR2Key,
    createdAt: updated.createdAt,
    updatedAt: updated.updatedAt,
    completedAt: updated.completedAt,
  };
}
