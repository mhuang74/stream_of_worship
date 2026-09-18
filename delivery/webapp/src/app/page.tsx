import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { auth } from "@/lib/auth";
import { resolveUserLocale } from "@/lib/i18n/server";
import {
  getDashboardStats,
  getRecentSongsets,
  getRecentFavoriteSongs,
  getCommunityFavoriteSample,
} from "@/lib/db/dashboard";
import { HomePageClient } from "./HomePageClient";

export default async function HomePage() {
  const locale = await resolveUserLocale();
  const session = await auth.api.getSession({ headers: await headers() });

  // Marketing moved to streamofworship.com (issue #213): signed-out visitors
  // are login-first. The proxy also redirects; this is the server-render
  // belt-and-braces.
  if (!session?.user) {
    redirect("/login");
  }

  const userId = Number(session.user.id);
  const [stats, recentSongsets, recentFavoriteSongs, communityFavorites] = await Promise.all([
    getDashboardStats(userId),
    getRecentSongsets(userId, 3),
    getRecentFavoriteSongs(userId, 4),
    getCommunityFavoriteSample(userId, 4),
  ]);

  return (
    <HomePageClient
      locale={locale}
      userName={session.user.name}
      stats={stats}
      recentSongsets={recentSongsets.map((songset) => ({
        ...songset,
        updatedAt: songset.updatedAt.toISOString(),
      }))}
      recentFavoriteSongs={recentFavoriteSongs}
      communityFavorites={communityFavorites}
    />
  );
}
