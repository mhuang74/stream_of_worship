import { headers } from "next/headers";
import { notFound, redirect } from "next/navigation";
import { auth } from "@/lib/auth";
import { getRenderPageData, type RenderJobSummary } from "@/lib/db/songsets";
import { buildInitialRenderData } from "@/lib/render/render-defaults";
import { RenderPageClient } from "./RenderPageClient";

function serializeJob(job: RenderJobSummary | null) {
  if (!job) return null;
  return {
    ...job,
    createdAt: job.createdAt?.toISOString() ?? new Date(0).toISOString(),
    titleCardDurationSeconds: job.titleCardDurationSeconds ?? undefined,
  };
}

export default async function RenderPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const session = await auth.api.getSession({ headers: await headers() });

  if (!session?.user) {
    redirect("/login");
  }

  const { id } = await params;
  const data = await getRenderPageData(id, Number(session.user.id));

  if (!data) {
    notFound();
  }

  const latestJob = serializeJob(data.latestJob);
  const previousCompletedJob = serializeJob(data.previousCompletedJob);

  return (
    <RenderPageClient
      songsetId={id}
      initialSongset={data.songset}
      initialLatestJob={latestJob}
      initialPreviousCompletedJob={previousCompletedJob}
      initialRenderData={buildInitialRenderData(
        latestJob as unknown as Record<string, unknown> | null,
        data.userSettings
      )}
    />
  );
}
