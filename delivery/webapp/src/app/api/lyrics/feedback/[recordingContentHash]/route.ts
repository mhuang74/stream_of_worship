import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { db } from "@/db";
import { lyricsFeedback } from "@/db/schema";
import { eq, and } from "drizzle-orm";
import { nanoid } from "nanoid";
import {
  resolveLyricsSituation,
  validateFeedbackSubmission,
  type FeedbackRating,
  type FeedbackReason,
} from "@/lib/lyrics/situation";

export interface LyricsFeedbackResponse {
  feedback: { rating: string; reason: string | null } | null;
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ recordingContentHash: string }> }
) {
  try {
    const session = await auth.api.getSession({ headers: request.headers });
    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const { recordingContentHash } = await params;

    const row = await db.query.lyricsFeedback.findFirst({
      where: and(
        eq(lyricsFeedback.userId, Number(session.user.id)),
        eq(lyricsFeedback.recordingContentHash, recordingContentHash)
      ),
    });

    return NextResponse.json<LyricsFeedbackResponse>({
      feedback: row ? { rating: row.rating, reason: row.reason } : null,
    });
  } catch (error) {
    console.error("Error fetching lyrics feedback:", error);
    return NextResponse.json({ error: "Failed to fetch lyrics feedback" }, { status: 500 });
  }
}

export async function PUT(
  request: NextRequest,
  { params }: { params: Promise<{ recordingContentHash: string }> }
) {
  try {
    const session = await auth.api.getSession({ headers: request.headers });
    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const { recordingContentHash } = await params;

    let body: unknown;
    try {
      body = await request.json();
    } catch {
      return NextResponse.json({ error: "Request body must be valid JSON" }, { status: 400 });
    }

    const { rating, reason } = (body ?? {}) as {
      rating?: unknown;
      reason?: unknown;
    };

    const situation = await resolveLyricsSituation(recordingContentHash);
    const validation = validateFeedbackSubmission(rating, reason ?? null, situation);
    if (!validation.valid) {
      return NextResponse.json({ error: validation.error }, { status: 400 });
    }

    const resolvedRating = rating as FeedbackRating;
    const resolvedReason =
      resolvedRating === "sad" ? (reason as FeedbackReason) : null;

    await db
      .insert(lyricsFeedback)
      .values({
        id: nanoid(),
        userId: Number(session.user.id),
        recordingContentHash,
        rating: resolvedRating,
        reason: resolvedReason,
      })
      .onConflictDoUpdate({
        target: [lyricsFeedback.userId, lyricsFeedback.recordingContentHash],
        set: {
          rating: resolvedRating,
          reason: resolvedReason,
          updatedAt: new Date(),
        },
      });

    return NextResponse.json<LyricsFeedbackResponse>({
      feedback: { rating: resolvedRating, reason: resolvedReason },
    });
  } catch (error) {
    console.error("Error saving lyrics feedback:", error);
    return NextResponse.json({ error: "Failed to save lyrics feedback" }, { status: 500 });
  }
}

export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ recordingContentHash: string }> }
) {
  try {
    const session = await auth.api.getSession({ headers: request.headers });
    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const { recordingContentHash } = await params;

    await db
      .delete(lyricsFeedback)
      .where(
        and(
          eq(lyricsFeedback.userId, Number(session.user.id)),
          eq(lyricsFeedback.recordingContentHash, recordingContentHash)
        )
      );

    return NextResponse.json({ success: true });
  } catch (error) {
    console.error("Error deleting lyrics feedback:", error);
    return NextResponse.json({ error: "Failed to delete lyrics feedback" }, { status: 500 });
  }
}