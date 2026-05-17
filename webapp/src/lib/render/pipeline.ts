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

const PHASES: { phase: RenderPhase; percent: number }[] = [
  { phase: "preparing", percent: 5 },
  { phase: "mixing_audio", percent: 30 },
  { phase: "rendering_frames", percent: 60 },
  { phase: "encoding_video", percent: 80 },
  { phase: "uploading", percent: 95 },
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
      percentComplete: PHASES[0].percent,
      estimatedSecondsLeft: 0,
      elapsedSeconds: 0,
    });

    await checkCancelled();

    const items = await fetchSongsetItems(job.songsetId);
    if (items.length === 0) {
      throw new Error("Songset has no items");
    }

    const audioEngine = new AudioEngine(assetFetcher);
    const audioOutputPath = `${tempDir}/${jobId}/output.mp3`;

    await updateRenderProgress(jobId, userId, {
      phase: PHASES[1].phase,
      phaseIndex: 1,
      totalPhases: PHASES.length,
      percentComplete: PHASES[1].percent,
      estimatedSecondsLeft: 0,
      elapsedSeconds: 0,
    });

    const audioResult = await audioEngine.generateSongsetAudio(
      items,
      audioOutputPath,
      (currentStep, totalSteps) => {
        const audioPercent = Math.round((currentStep / totalSteps) * 25) + 5;
        updateRenderProgress(jobId, userId, {
          percentComplete: audioPercent,
        }).catch((err) => {
          console.warn(`Progress update failed for job ${jobId}:`, err);
        });
      }
    );

    await checkCancelled();

    await updateRenderProgress(jobId, userId, {
      phase: PHASES[2].phase,
      phaseIndex: 2,
      totalPhases: PHASES.length,
      percentComplete: PHASES[2].percent,
      estimatedSecondsLeft: 0,
      elapsedSeconds: 0,
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
        percentComplete: PHASES[3].percent,
      });

      await videoEngine.generateVideo(
        audioOutputPath,
        audioResult.segments,
        videoOutputPath,
        (currentFrame, totalFrames) => {
          const videoPercent = Math.round((currentFrame / totalFrames) * 20) + 60;
          updateRenderProgress(jobId, userId, {
            percentComplete: videoPercent,
          }).catch((err) => {
            console.warn(`Progress update failed for job ${jobId}:`, err);
          });
        }
      );

      await checkCancelled();
    }

    await updateRenderProgress(jobId, userId, {
      phase: PHASES[4].phase,
      phaseIndex: 4,
      totalPhases: PHASES.length,
      percentComplete: PHASES[4].percent,
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
      },
      (fileType, bytesUploaded, totalBytes) => {
        const uploadPercent = Math.round((bytesUploaded / totalBytes) * 5) + 95;
        updateRenderProgress(jobId, userId, {
          percentComplete: uploadPercent,
        }).catch((err) => {
          console.warn(`Progress update failed for job ${jobId}:`, err);
        });
      }
    );

    await completeRenderJob(jobId, userId, {
      mp3R2Key: uploadResult.mp3R2Key,
      mp4R2Key: uploadResult.mp4R2Key,
      chaptersR2Key: uploadResult.chaptersR2Key,
    });
  } catch (error) {
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
