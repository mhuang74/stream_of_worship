import { headers } from "next/headers";
import { notFound, redirect } from "next/navigation";
import { auth } from "@/lib/auth";
import { canReviewLyrics } from "@/lib/lyrics/review-access";
import { LyricsReviewClient } from "./LyricsReviewClient";

export default async function LyricsReviewPage() {
  const session = await auth.api.getSession({ headers: await headers() });
  if (!session?.user) redirect("/login?callbackUrl=/admin/lyrics-review");
  if (!canReviewLyrics(session.user)) notFound();
  return <LyricsReviewClient />;
}
