import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { db } from "@/db";
import { userLrcOverrides } from "@/db/schema";
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

    const override = await db.query.userLrcOverrides.findFirst({
      where: and(
        eq(userLrcOverrides.userId, Number(session.user.id)),
        eq(userLrcOverrides.recordingContentHash, recordingContentHash)
      ),
    });

    return NextResponse.json({ lrcContent: override?.lrcContent ?? null });
  } catch (error) {
    console.error("Error fetching lyric override:", error);
    return NextResponse.json({ error: "Failed to fetch lyric override" }, { status: 500 });
  }
}

export async function POST(request: NextRequest) {
  try {
    const session = await auth.api.getSession({ headers: request.headers });
    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const body = await request.json();
    const { recordingContentHash, lrcContent } = body;

    if (!recordingContentHash || typeof lrcContent !== "string") {
      return NextResponse.json(
        { error: "recordingContentHash and lrcContent are required" },
        { status: 400 }
      );
    }

    await db
      .insert(userLrcOverrides)
      .values({
        id: nanoid(),
        userId: Number(session.user.id),
        recordingContentHash,
        lrcContent,
      })
      .onConflictDoUpdate({
        target: [userLrcOverrides.userId, userLrcOverrides.recordingContentHash],
        set: {
          lrcContent,
          updatedAt: new Date(),
        },
      });

    return NextResponse.json({ success: true });
  } catch (error) {
    console.error("Error saving lyric override:", error);
    return NextResponse.json({ error: "Failed to save lyric override" }, { status: 500 });
  }
}

export async function DELETE(request: NextRequest) {
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

    await db
      .delete(userLrcOverrides)
      .where(
        and(
          eq(userLrcOverrides.userId, Number(session.user.id)),
          eq(userLrcOverrides.recordingContentHash, recordingContentHash)
        )
      );

    return NextResponse.json({ success: true });
  } catch (error) {
    console.error("Error deleting lyric override:", error);
    return NextResponse.json({ error: "Failed to delete lyric override" }, { status: 500 });
  }
}
