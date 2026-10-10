import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { auth } from "@/lib/auth";
import { getFavoriteSongIds } from "@/lib/db/favorites";
import { ListenClient } from "./ListenClient";

export default async function ListenPage() {
  const session = await auth.api.getSession({ headers: await headers() });
  if (!session?.user) {
    redirect("/login");
  }

  const userId = Number(session.user.id);
  const favoriteSongIds = await getFavoriteSongIds(userId);

  return (
    <ListenClient
      favoriteSongIds={favoriteSongIds}
    />
  );
}
