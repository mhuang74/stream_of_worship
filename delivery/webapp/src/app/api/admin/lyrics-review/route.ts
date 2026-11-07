import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { canReviewLyrics } from "@/lib/lyrics/review-access";
import { canonicalReviewLyrics, reviewQueue, reviewRecording } from "@/lib/lyrics/review";
import { createR2ClientFromEnv } from "@/lib/r2/client";
import { db } from "@/db";
import { lyricsFeedback } from "@/db/schema";
import { and, eq } from "drizzle-orm";
import { nanoid } from "nanoid";
import { validateFeedbackSubmission, type FeedbackRating, type FeedbackReason } from "@/lib/lyrics/situation";

const json = (data: unknown, status = 200) => NextResponse.json(data, {
  status, headers: { "Cache-Control": "private, no-store" },
});

/**
 * Same-origin check for writes. Compare the `Origin` host against the host the
 * request was addressed to, not `nextUrl.origin`: the latter carries the server's
 * bind address (e.g. `http://0.0.0.0:8080` under `next dev -H 0.0.0.0`) or a
 * rewritten internal URL, so every real browser write looked cross-origin.
 */
function isSameOrigin(request: NextRequest) {
  const origin = request.headers.get("origin");
  const host = request.headers.get("host") ?? request.nextUrl.host;
  if (!origin || !host) return false;
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}

async function handle(request: NextRequest) {
  const session = await auth.api.getSession({ headers: request.headers });
  if (!session?.user) return json({ error: "Unauthorized" }, 401);
  if (!canReviewLyrics(session.user)) return json({ error: "Forbidden" }, 403);
  if (request.method !== "GET" && !isSameOrigin(request)) {
    return json({ error: "Invalid origin" }, 403);
  }
  const userId = Number(session.user.id);
  const hash = request.nextUrl.searchParams.get("recording");
  if (!hash && request.method === "GET") {
    const offset = Number(request.nextUrl.searchParams.get("offset") ?? 0);
    if (!Number.isSafeInteger(offset) || offset < 0) return json({ error: "Invalid offset" }, 400);
    return json(await reviewQueue(
      (request.nextUrl.searchParams.get("q") ?? "").trim().slice(0, 200),
      request.nextUrl.searchParams.get("reviewed") === "1", offset,
      request.nextUrl.searchParams.get("set")?.trim() || null));
  }
  if (!hash) return json({ error: "Recording required" }, 400);
  const recording = await reviewRecording(hash);
  if (!recording) return json({ error: "Review recording not found" }, 404);
  const where = and(eq(lyricsFeedback.userId, userId), eq(lyricsFeedback.recordingContentHash, hash));
  if (request.method === "DELETE") {
    await db.delete(lyricsFeedback).where(where);
    return json({ feedback: null });
  }
  const lyrics = await canonicalReviewLyrics(recording.hashPrefix);
  if (request.method === "GET") {
    const feedback = await db.query.lyricsFeedback.findFirst({ where });
    const audio = await createR2ClientFromEnv().getAudioSignedUrl(recording.hashPrefix);
    return json({ recording, ...lyrics, audioUrl: audio.url,
      feedback: feedback?.resolvedAt === null ? {
        rating: feedback.rating, reason: feedback.reason, note: feedback.note,
      } : null });
  }
  let body;
  try { body = await request.json(); } catch { return json({ error: "Invalid JSON" }, 400); }
  const { rating, reason = null, note = null } = body ?? {};
  if (rating === "happy" && !lyrics.synced) return json({ error: "Good requires canonical timed Lyrics" }, 400);
  const validation = validateFeedbackSubmission(rating, reason, note, { kind: lyrics.synced ? "synced" : "none" });
  if (!validation.valid) return json({ error: validation.error }, 400);
  const feedback = { rating: rating as FeedbackRating,
    reason: rating === "sad" ? reason as FeedbackReason : null,
    note: rating === "sad" && typeof note === "string" ? note.trim() || null : null };
  await db.insert(lyricsFeedback).values({ id: nanoid(), userId, recordingContentHash: hash, ...feedback })
    .onConflictDoUpdate({ target: [lyricsFeedback.userId, lyricsFeedback.recordingContentHash],
      set: { ...feedback, resolvedAt: null, updatedAt: new Date() } });
  return json({ feedback });
}

async function safeHandle(request: NextRequest) {
  try { return await handle(request); }
  catch (error) {
    console.error("Lyrics review failed:", error);
    return json({ error: "Unable to load or save this review. Please retry." }, 503);
  }
}
export const GET = safeHandle;
export const PUT = safeHandle;
export const DELETE = safeHandle;
