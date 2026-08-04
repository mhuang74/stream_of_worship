import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { auth } from "@/lib/auth";
import { listSongsetSummaries } from "@/lib/db/songsets";
import { SongsetsClient } from "./SongsetsClient";

export default async function SongsetsPage({
  searchParams,
}: {
  searchParams: Promise<{ page?: string; search?: string }>;
}) {
  const session = await auth.api.getSession({ headers: await headers() });

  if (!session?.user) {
    redirect("/login");
  }

  const params = await searchParams;
  const page = Math.max(1, parseInt(params.page ?? "1") || 1);
  const search = params.search?.trim() || undefined;
  const pageSize = 20;
  const offset = (page - 1) * pageSize;

  const result = await listSongsetSummaries(
    Number(session.user.id),
    pageSize,
    offset,
    search
  );

  return (
    <SongsetsClient
      initialData={{
        total: result.total,
        songsets: result.songsets.map((songset) => ({
          ...songset,
          createdAt: songset.createdAt.toISOString(),
          updatedAt: songset.updatedAt.toISOString(),
          failedAt: songset.failedAt?.toISOString() ?? null,
        })),
      }}
      currentPage={page}
      pageSize={pageSize}
      initialSearch={search ?? ""}
    />
  );
}
