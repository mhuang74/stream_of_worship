import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { canReviewLyrics } from "@/lib/lyrics/review-access";
import { reviewSets } from "@/lib/lyrics/review";

const json = (data: unknown, status = 200) => NextResponse.json(data, {
  status, headers: { "Cache-Control": "private, no-store" },
});

/** Review sets are CLI-owned; this endpoint is read-only for the picker. */
async function handle(request: NextRequest) {
  const session = await auth.api.getSession({ headers: request.headers });
  if (!session?.user) return json({ error: "Unauthorized" }, 401);
  if (!canReviewLyrics(session.user)) return json({ error: "Forbidden" }, 403);
  return json({ sets: await reviewSets() });
}

async function safeHandle(request: NextRequest) {
  try { return await handle(request); }
  catch (error) {
    console.error("Lyrics review sets failed:", error);
    return json({ error: "Unable to load review sets. Please retry." }, 503);
  }
}
export const GET = safeHandle;
