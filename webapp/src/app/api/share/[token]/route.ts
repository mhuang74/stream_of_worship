import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { db } from "@/db";
import { songsetShares, renderJobs, songsets } from "@/db/schema";
import { eq, and } from "drizzle-orm";
import { createR2ClientFromEnv } from "@/lib/r2/client";

const NO_CACHE_HEADERS = {
  "Cache-Control": "no-store, no-cache, must-revalidate",
  Pragma: "no-cache",
};

/**
 * GET /api/share/[token]
 * Public endpoint: validate token and return share info with signed playback URLs.
 */
export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ token: string }> }
) {
  try {
    const { token } = await params;

    const share = await db.query.songsetShares.findFirst({
      where: eq(songsetShares.token, token),
    });

    if (!share) {
      return NextResponse.json({ error: "Share not found" }, { status: 404, headers: NO_CACHE_HEADERS });
    }

    if (share.revokedAt) {
      return NextResponse.json({ error: "Share has been revoked" }, { status: 410, headers: NO_CACHE_HEADERS });
    }

    if (share.expiresAt && share.expiresAt < new Date()) {
      return NextResponse.json({ error: "Share has expired" }, { status: 410, headers: NO_CACHE_HEADERS });
    }

    const job = await db.query.renderJobs.findFirst({
      where: eq(renderJobs.id, share.renderJobId),
    });

    if (!job || job.status !== "completed") {
      return NextResponse.json(
        { error: "Render artifacts not available" },
        { status: 404, headers: NO_CACHE_HEADERS }
      );
    }

    const songset = await db.query.songsets.findFirst({
      where: eq(songsets.id, share.songsetId),
    });

    let mp3Url: string | null = null;
    let mp4Url: string | null = null;
    let chaptersUrl: string | null = null;
    let mp3SizeBytes: number | null = null;
    let mp4SizeBytes: number | null = null;

    try {
      const r2Client = createR2ClientFromEnv();
      const expiresInSeconds = 3600;

      const [mp3Result, mp4Result, chaptersResult, mp3Size, mp4Size] = await Promise.all([
        job.mp3R2Key
          ? r2Client.generateSignedUrl(job.mp3R2Key, "audio", { expiresInSeconds })
          : null,
        job.mp4R2Key
          ? r2Client.generateSignedUrl(job.mp4R2Key, "video", { expiresInSeconds })
          : null,
        job.chaptersR2Key
          ? r2Client.generateSignedUrl(job.chaptersR2Key, "json", { expiresInSeconds })
          : null,
        job.mp3R2Key ? r2Client.getObjectSize(job.mp3R2Key) : null,
        job.mp4R2Key ? r2Client.getObjectSize(job.mp4R2Key) : null,
      ]);

      mp3Url = mp3Result?.url ?? null;
      mp4Url = mp4Result?.url ?? null;
      chaptersUrl = chaptersResult?.url ?? null;
      mp3SizeBytes = mp3Size;
      mp4SizeBytes = mp4Size;
    } catch {
      // R2 not configured — return basic info without URLs
    }

    return NextResponse.json(
      {
        token,
        songsetId: share.songsetId,
        songsetName: songset?.name ?? null,
        renderJobId: share.renderJobId,
        allowDownload: share.allowDownload,
        mp3Url,
        mp4Url,
        chaptersUrl,
        mp3SizeBytes,
        mp4SizeBytes,
        createdAt: share.createdAt,
      },
      { headers: NO_CACHE_HEADERS }
    );
  } catch (error) {
    console.error("Error fetching share:", error);
    return NextResponse.json(
      { error: "Failed to fetch share" },
      { status: 500, headers: NO_CACHE_HEADERS }
    );
  }
}

/**
 * DELETE /api/share/[token]
 * Authenticated: revoke a share token (owner only).
 */
export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ token: string }> }
) {
  try {
    const session = await auth.api.getSession({ headers: request.headers });
    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const { token } = await params;
    const userId = Number(session.user.id);

    const share = await db.query.songsetShares.findFirst({
      where: and(
        eq(songsetShares.token, token),
        eq(songsetShares.createdByUserId, userId)
      ),
    });

    if (!share) {
      return NextResponse.json({ error: "Share not found" }, { status: 404 });
    }

    if (share.revokedAt) {
      return NextResponse.json({ error: "Share already revoked" }, { status: 409 });
    }

    await db
      .update(songsetShares)
      .set({ revokedAt: new Date() })
      .where(eq(songsetShares.token, token));

    return NextResponse.json({ success: true });
  } catch (error) {
    console.error("Error revoking share:", error);
    return NextResponse.json({ error: "Failed to revoke share" }, { status: 500 });
  }
}
