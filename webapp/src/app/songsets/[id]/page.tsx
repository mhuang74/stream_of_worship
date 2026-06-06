import { headers } from "next/headers";
import { notFound, redirect } from "next/navigation";
import { auth } from "@/lib/auth";
import { getSongsetEditorData } from "@/lib/db/songsets";
import { SongsetEditorClient } from "./SongsetEditorClient";

export default async function SongsetEditorPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const session = await auth.api.getSession({ headers: await headers() });

  if (!session?.user) {
    redirect("/login");
  }

  const { id } = await params;
  const songset = await getSongsetEditorData(id, Number(session.user.id));

  if (!songset) {
    notFound();
  }

  return (
    <SongsetEditorClient
      songsetId={id}
      initialData={{
        id: songset.id,
        name: songset.name,
        description: songset.description,
        createdAt: songset.createdAt.toISOString(),
        updatedAt: songset.updatedAt.toISOString(),
        renderState: songset.renderState,
        itemCount: songset.itemCount,
        durationSeconds: songset.durationSeconds,
        latestRenderJobId: songset.latestRenderJobId,
        lastFailedRenderJobId: songset.lastFailedRenderJobId,
        lastCompletedRenderJobId: songset.lastCompletedRenderJobId,
        isArtifactsStale: songset.renderState === "stale",
        items: songset.items.map((item) => ({
          id: item.id,
          songId: item.songId,
          recordingHashPrefix: item.recordingHashPrefix,
          position: item.position,
          gapBeats: item.gapBeats ?? 2,
          crossfadeEnabled: item.crossfadeEnabled ?? 0,
          crossfadeDurationSeconds: item.crossfadeDurationSeconds,
          keyShiftSemitones: item.keyShiftSemitones ?? 0,
          tempoRatio: item.tempoRatio ?? 1,
          markedLineCount: item.markedLineCount,
          song: item.song,
          recording: item.recording
            ? {
                contentHash: item.recording.contentHash,
                durationSeconds: item.recording.durationSeconds,
                tempoBpm: item.recording.tempoBpm,
                musicalKey: item.recording.musicalKey,
              }
            : null,
        })),
      }}
    />
  );
}
