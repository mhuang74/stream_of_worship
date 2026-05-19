import { db } from "@/db";
import { songsetItems, songs, recordings } from "@/db/schema";
import { eq } from "drizzle-orm";
import {
  startRenderJob,
  updateRenderProgress,
  completeRenderJob,
  failRenderJob,
  getRenderJob,
  type RenderPhase,
} from "./job-manager";
import { AudioEngine, type SongsetItem } from "./audio-engine";
import { AssetFetcher } from "./asset-fetcher";
import { generateChaptersManifest } from "./chapters";
import { getRenderRatio } from "./render-ratio";

const PHASES: { phase: RenderPhase }[] = [
  { phase: "preparing" },
  { phase: "mixing_audio" },
  { phase: "rendering_frames" },
  { phase: "encoding_video" },
  { phase: "uploading" },
];

async function fetchSongsetItems(songsetId: string): Promise<SongsetItem[]> {
  const rows = await db
    .select({
      id: songsetItems.id,
      songsetId: songsetItems.songsetId,
      songId: songsetItems.songId,
      recordingHashPrefix: songsetItems.recordingHashPrefix,
      position: songsetItems.position,
      gapBeats: songsetItems.gapBeats,
      crossfadeEnabled: songsetItems.crossfadeEnabled,
      crossfadeDurationSeconds: songsetItems.crossfadeDurationSeconds,
      keyShiftSemitones: songsetItems.keyShiftSemitones,
      tempoRatio: songsetItems.tempoRatio,
      tempoBpm: recordings.tempoBpm,
      durationSeconds: recordings.durationSeconds,
      songTitle: songs.title,
    })
    .from(songsetItems)
    .leftJoin(recordings, eq(songsetItems.recordingHashPrefix, recordings.hashPrefix))
    .leftJoin(songs, eq(songsetItems.songId, songs.id))
    .where(eq(songsetItems.songsetId, songsetId))
    .orderBy(songsetItems.position);

  return rows.map((row) => ({
    id: row.id,
    songsetId: row.songsetId,
    songId: row.songId,
    recordingHashPrefix: row.recordingHashPrefix,
    position: row.position,
    gapBeats: row.gapBeats,
    crossfadeEnabled: row.crossfadeEnabled,
    crossfadeDurationSeconds: row.crossfadeDurationSeconds,
    keyShiftSemitones: row.keyShiftSemitones,
    tempoRatio: row.tempoRatio,
    tempoBpm: row.tempoBpm,
    durationSeconds: row.durationSeconds,
    songTitle: row.songTitle,
  }));
}

export async function executeRenderPipeline(
  jobId: string,
  userId: number
): Promise<void> {
  const job = await getRenderJob(jobId, userId);
  if (!job) {
    throw new Error(`Render job ${jobId} not found`);
  }

  const assetFetcher = new AssetFetcher();
  const tempDir = await assetFetcher.getTempDir();
  const pipelineStartTime = Date.now();
  const startedAt = new Date();

  const checkCancelled = async () => {
    const current = await getRenderJob(jobId, userId);
    if (!current || current.status === "cancelled") {
      throw new Error(`Render job ${jobId} was cancelled`);
    }
  };

  try {
    await startRenderJob(jobId, userId);

    await updateRenderProgress(jobId, userId, {
      phase: PHASES[0].phase,
      phaseIndex: 0,
      totalPhases: PHASES.length,
      startedAt,
      elapsedSeconds: 0,
    });

    await checkCancelled();

    const items = await fetchSongsetItems(job.songsetId);
    if (items.length === 0) {
      throw new Error("Songset has no items");
    }

    const totalDurationSeconds = items.reduce(
      (sum, item) => sum + (item.durationSeconds ?? 0),
      0
    );
    const renderRatio = await getRenderRatio(job.resolution, job.videoEnabled);
    const estimatedTotalSeconds = totalDurationSeconds > 0 ? totalDurationSeconds * renderRatio : 0;

    await updateRenderProgress(jobId, userId, {
      phase: PHASES[1].phase,
      phaseIndex: 1,
      totalPhases: PHASES.length,
      estimatedTotalSeconds,
      totalDurationSeconds,
      elapsedSeconds: (Date.now() - pipelineStartTime) / 1000,
    });

    const audioEngine = new AudioEngine(assetFetcher);
    const audioOutputPath = `${tempDir}/${jobId}/output.mp3`;

    const audioResult = await audioEngine.generateSongsetAudio(
      items,
      audioOutputPath
    );

    await checkCancelled();

    const accurateTotalDuration = audioResult.totalDurationSeconds;
    const accurateEstimatedTotal =
      accurateTotalDuration * (await getRenderRatio(job.resolution, job.videoEnabled));

    await updateRenderProgress(jobId, userId, {
      phase: PHASES[2].phase,
      phaseIndex: 2,
      totalPhases: PHASES.length,
      totalDurationSeconds: accurateTotalDuration,
      estimatedTotalSeconds: accurateEstimatedTotal,
      elapsedSeconds: (Date.now() - pipelineStartTime) / 1000,
    });

    let videoOutputPath: string | undefined;
    if (job.videoEnabled) {
      const { VideoEngine } = await import("./video-engine");
      const videoEngine = new VideoEngine(assetFetcher, {
        template: job.template as "dark" | "gradient_warm" | "gradient_blue",
        fontSizePreset: job.fontSizePreset as "S" | "M" | "L" | "XL",
        resolution: job.resolution as "720p" | "1080p",
        includeTitleCard: job.includeTitleCard,
        titleCardDurationSeconds: job.titleCardDurationSeconds ?? undefined,
      });

      await videoEngine.initialize();
      videoOutputPath = `${tempDir}/${jobId}/output.mp4`;

      await updateRenderProgress(jobId, userId, {
        phase: PHASES[3].phase,
        phaseIndex: 3,
        totalPhases: PHASES.length,
        elapsedSeconds: (Date.now() - pipelineStartTime) / 1000,
      });

      await videoEngine.generateVideo(
        audioOutputPath,
        audioResult.segments,
        videoOutputPath
      );

      await checkCancelled();
    }

    await updateRenderProgress(jobId, userId, {
      phase: PHASES[4].phase,
      phaseIndex: 4,
      totalPhases: PHASES.length,
      elapsedSeconds: (Date.now() - pipelineStartTime) / 1000,
    });

    const chaptersManifest = await generateChaptersManifest(
      audioResult.segments,
      assetFetcher,
      audioResult.totalDurationSeconds
    );

    const { R2Uploader } = await import("./uploader");
    const uploader = new R2Uploader();
    const uploadResult = await uploader.uploadRenderArtifacts(
      jobId,
      {
        mp3Path: job.audioEnabled ? audioOutputPath : undefined,
        mp4Path: videoOutputPath,
        chapters: chaptersManifest,
      }
    );

    await completeRenderJob(jobId, userId, {
      mp3R2Key: uploadResult.mp3R2Key,
      mp4R2Key: uploadResult.mp4R2Key,
      chaptersR2Key: uploadResult.chaptersR2Key,
    });
  } catch (error) {
    const currentJob = await getRenderJob(jobId, userId);
    if (currentJob?.status === "cancelled") {
      return;
    }
    const errorMessage =
      error instanceof Error ? error.message : "Unknown render error";
    console.error(`Render pipeline failed for job ${jobId}:`, errorMessage);
    await failRenderJob(jobId, userId, errorMessage).catch((failError) => {
      console.error(`Failed to mark job ${jobId} as failed:`, failError);
    });
    throw error;
  } finally {
    await assetFetcher.cleanupTemp().catch((err) => {
      console.warn(`Temp cleanup failed for job ${jobId}:`, err);
    });
  }
}
