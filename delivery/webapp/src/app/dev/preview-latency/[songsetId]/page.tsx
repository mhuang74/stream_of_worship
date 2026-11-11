// PROTOTYPE (throwaway): measures the browser engine described in
// specs/songset_preview_with_arrangement.md; delete before production merge.
import { headers } from "next/headers";
import { notFound, redirect } from "next/navigation";
import { auth } from "@/lib/auth";
import { getSongsetEditorData } from "@/lib/db/songsets";
import type { TimelineItemSource } from "@/lib/arrangements";
import { PreviewLatencyClient } from "./PreviewLatencyClient";

export default async function PreviewLatencyPage({
  params,
}: {
  params: Promise<{ songsetId: string }>;
}) {
  const session = await auth.api.getSession({ headers: await headers() });

  if (!session?.user) {
    redirect("/login");
  }

  const { songsetId } = await params;
  const songset = await getSongsetEditorData(songsetId, Number(session.user.id));

  if (!songset) {
    notFound();
  }

  const items: TimelineItemSource[] = songset.items.map((item) => ({
    itemId: item.id,
    recordingHashPrefix: item.recordingHashPrefix,
    arrangementSteps: item.arrangementSteps,
    gapBeats: item.gapBeats ?? 2,
    crossfadeEnabled: item.crossfadeEnabled ?? 0,
    crossfadeDurationSeconds: item.crossfadeDurationSeconds,
    tempoBpm: item.recording?.tempoBpm ?? null,
    recordingDurationSeconds: item.recording?.durationSeconds ?? null,
    recordingSections: item.recording?.sections ?? null,
    recordingBeats: item.recording?.beats ?? [],
  }));

  return (
    <PreviewLatencyClient
      songsetId={songset.id}
      songsetName={songset.name}
      items={items}
    />
  );
}
