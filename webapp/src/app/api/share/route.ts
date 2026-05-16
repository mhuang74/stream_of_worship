import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { db } from "@/db";
import { songsetShares, renderJobs } from "@/db/schema";
import { eq, and, isNull, count } from "drizzle-orm";
import { nanoid } from "nanoid";

const MAX_ACTIVE_SHARES = 20;

/**
 * POST /api/share
 * Create a new share token for a completed render job.
 * Max 20 active shares per user.
 */
export async function POST(request: NextRequest) {
  try {
    const session = await auth.api.getSession({ headers: request.headers });
    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const userId = Number(session.user.id);

    let body: unknown;
    try {
      body = await request.json();
    } catch {
      return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
    }

    if (!body || typeof body !== "object") {
      return NextResponse.json({ error: "Invalid request body" }, { status: 400 });
    }

    const { renderJobId, allowDownload = false } = body as {
      renderJobId?: unknown;
      allowDownload?: unknown;
    };

    if (!renderJobId || typeof renderJobId !== "string") {
      return NextResponse.json({ error: "renderJobId is required" }, { status: 400 });
    }

    const job = await db.query.renderJobs.findFirst({
      where: and(eq(renderJobs.id, renderJobId), eq(renderJobs.userId, userId)),
    });

    if (!job) {
      return NextResponse.json({ error: "Render job not found" }, { status: 404 });
    }

    if (job.status !== "completed") {
      return NextResponse.json({ error: "Render job is not completed" }, { status: 409 });
    }

    // Enforce max 20 active shares per user
    const [activeCount] = await db
      .select({ value: count() })
      .from(songsetShares)
      .where(and(eq(songsetShares.createdByUserId, userId), isNull(songsetShares.revokedAt)));

    if ((activeCount?.value ?? 0) >= MAX_ACTIVE_SHARES) {
      return NextResponse.json(
        { error: "Maximum of 20 active shares reached. Revoke some to create new ones." },
        { status: 422 }
      );
    }

    const token = nanoid(24);
    const normalizedAllowDownload = allowDownload === true;

    await db.insert(songsetShares).values({
      token,
      songsetId: job.songsetId,
      renderJobId,
      createdByUserId: userId,
      allowDownload: normalizedAllowDownload,
      createdAt: new Date(),
    });

    const baseUrl = process.env.NEXT_PUBLIC_BASE_URL ?? "";
    const shareUrl = `${baseUrl}/share/${token}`;

    return NextResponse.json({ token, shareUrl, renderJobId, allowDownload: normalizedAllowDownload }, { status: 201 });
  } catch (error) {
    console.error("Error creating share token:", error);
    return NextResponse.json({ error: "Failed to create share token" }, { status: 500 });
  }
}

/**
 * GET /api/share?renderJobId=<id>
 * List the authenticated user's active share tokens.
 */
export async function GET(request: NextRequest) {
  try {
    const session = await auth.api.getSession({ headers: request.headers });
    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const userId = Number(session.user.id);
    const renderJobId = request.nextUrl.searchParams.get("renderJobId");

    const conditions: Parameters<typeof and>[0][] = [
      eq(songsetShares.createdByUserId, userId),
      isNull(songsetShares.revokedAt),
    ];

    if (renderJobId) {
      conditions.push(eq(songsetShares.renderJobId, renderJobId));
    }

    const shares = await db
      .select()
      .from(songsetShares)
      .where(and(...(conditions as [Parameters<typeof and>[0], ...Parameters<typeof and>[0][]])));

    const baseUrl = process.env.NEXT_PUBLIC_BASE_URL ?? "";
    const sharesWithUrls = shares.map((s) => ({
      ...s,
      shareUrl: `${baseUrl}/share/${s.token}`,
    }));

    return NextResponse.json({ shares: sharesWithUrls });
  } catch (error) {
    console.error("Error listing shares:", error);
    return NextResponse.json({ error: "Failed to list shares" }, { status: 500 });
  }
}
