import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { db } from "@/db";
import { lyricMarks } from "@/db/schema";
import { eq, and } from "drizzle-orm";
import { nanoid } from "nanoid";

export async function GET(request: NextRequest) {
  try {
    const session = await auth.api.getSession({ headers: request.headers });
    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const recordingContentHash = request.nextUrl.searchParams.get("recordingContentHash");
    if (!recordingContentHash) {
      return NextResponse.json(
        { error: "recordingContentHash query parameter is required" },
        { status: 400 }
      );
    }

    const marks = await db
      .select({ timestampSeconds: lyricMarks.timestampSeconds })
      .from(lyricMarks)
      .where(
        and(
          eq(lyricMarks.userId, Number(session.user.id)),
          eq(lyricMarks.recordingContentHash, recordingContentHash)
        )
      );

    return NextResponse.json({ marks: marks.map((m) => m.timestampSeconds) });
  } catch (error) {
    console.error("Error fetching lyric marks:", error);
    return NextResponse.json({ error: "Failed to fetch lyric marks" }, { status: 500 });
  }
}

export async function POST(request: NextRequest) {
  try {
    const session = await auth.api.getSession({ headers: request.headers });
    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const body = await request.json();
    const { recordingContentHash, timestampSeconds } = body;

    if (!recordingContentHash || typeof timestampSeconds !== "number") {
      return NextResponse.json(
        { error: "recordingContentHash and timestampSeconds are required" },
        { status: 400 }
      );
    }

    await db
      .insert(lyricMarks)
      .values({
        id: nanoid(),
        userId: Number(session.user.id),
        recordingContentHash,
        timestampSeconds,
      })
      .onConflictDoNothing();

    return NextResponse.json({ success: true }, { status: 201 });
  } catch (error) {
    console.error("Error creating lyric mark:", error);
    return NextResponse.json({ error: "Failed to create lyric mark" }, { status: 500 });
  }
}

export async function DELETE(request: NextRequest) {
  try {
    const session = await auth.api.getSession({ headers: request.headers });
    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const recordingContentHash = request.nextUrl.searchParams.get("recordingContentHash");
    const timestampSecondsStr = request.nextUrl.searchParams.get("timestampSeconds");

    if (!recordingContentHash || !timestampSecondsStr) {
      return NextResponse.json(
        { error: "recordingContentHash and timestampSeconds are required" },
        { status: 400 }
      );
    }

    const timestampSeconds = parseFloat(timestampSecondsStr);
    if (isNaN(timestampSeconds)) {
      return NextResponse.json(
        { error: "timestampSeconds must be a valid number" },
        { status: 400 }
      );
    }

    await db
      .delete(lyricMarks)
      .where(
        and(
          eq(lyricMarks.userId, Number(session.user.id)),
          eq(lyricMarks.recordingContentHash, recordingContentHash),
          eq(lyricMarks.timestampSeconds, timestampSeconds)
        )
      );

    return NextResponse.json({ success: true });
  } catch (error) {
    console.error("Error deleting lyric mark:", error);
    return NextResponse.json({ error: "Failed to delete lyric mark" }, { status: 500 });
  }
}
