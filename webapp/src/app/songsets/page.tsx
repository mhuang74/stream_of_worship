import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { auth } from "@/lib/auth";
import { listSongsetSummaries } from "@/lib/db/songsets";
import { SongsetsClient } from "./SongsetsClient";

export default async function SongsetsPage() {
  const session = await auth.api.getSession({ headers: await headers() });

  if (!session?.user) {
    redirect("/login");
  }

  const result = await listSongsetSummaries(Number(session.user.id), 50, 0);

  return (
    <SongsetsClient
      initialData={{
        total: result.total,
        songsets: result.songsets.map((songset) => ({
          ...songset,
          createdAt: songset.createdAt.toISOString(),
          updatedAt: songset.updatedAt.toISOString(),
        })),
      }}
    />
  );
}
