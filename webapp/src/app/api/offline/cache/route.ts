import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { db } from "@/db";
import { renderJobs } from "@/db/schema";
import { eq, and } from "drizzle-orm";
import { createR2ClientFromEnv } from "@/lib/r2/client";

/**
 * GET /api/offline/cache?renderJobId=<id>
 *
 * Returns signed download URLs for a completed render job's artifacts so the
 * client can fetch and store them in Cache Storage for offline playback.
 *
 * Requires authentication; verifies the caller owns the render job.
 */
export async function GET(request: NextRequest) {
  try {
    const session = await auth.api.getSession({ headers: request.headers });

    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const renderJobId = request.nextUrl.searchParams.get("renderJobId");

    if (!renderJobId) {
      return NextResponse.json(
        { error: "renderJobId query parameter is required" },
        { status: 400 }
      );
    }

    const job = await db.query.renderJobs.findFirst({
      where: and(
        eq(renderJobs.id, renderJobId),
        eq(renderJobs.userId, Number(session.user.id))
      ),
    });

    if (!job) {
      return NextResponse.json(
        { error: "Render job not found" },
        { status: 404 }
      );
    }

    if (job.status !== "completed") {
      return NextResponse.json(
        { error: "Render job is not completed" },
        { status: 409 }
      );
    }

    if (!job.mp3R2Key && !job.mp4R2Key) {
      return NextResponse.json(
        { error: "Render job has no artifacts" },
        { status: 404 }
      );
    }

    let r2Client: ReturnType<typeof createR2ClientFromEnv>;
    try {
      r2Client = createR2ClientFromEnv();
    } catch {
      return NextResponse.json(
        { error: "R2 storage not configured" },
        { status: 503 }
      );
    }

    // Generate signed URLs for each available artifact (1-hour expiry for caching).
    const expiresInSeconds = 3600;

    const [mp3Result, mp4Result, chaptersResult] = await Promise.all([
      job.mp3R2Key
        ? r2Client.generateSignedUrl(job.mp3R2Key, "audio", { expiresInSeconds })
        : null,
      job.mp4R2Key
        ? r2Client.generateSignedUrl(job.mp4R2Key, "video", { expiresInSeconds })
        : null,
      job.chaptersR2Key
        ? r2Client.generateSignedUrl(job.chaptersR2Key, "json", { expiresInSeconds })
        : null,
    ]);

    return NextResponse.json({
      renderJobId: job.id,
      mp3Url: mp3Result?.url ?? null,
      mp4Url: mp4Result?.url ?? null,
      chaptersUrl: chaptersResult?.url ?? null,
      expiresAt: (mp3Result ?? mp4Result)?.expiresAt ?? null,
    });
  } catch (error) {
    console.error("Error generating offline cache URLs:", error);
    return NextResponse.json(
      { error: "Failed to generate offline cache URLs" },
      { status: 500 }
    );
  }
}

/**
 * DELETE /api/offline/cache?renderJobId=<id>
 *
 * Verifies ownership and returns the render job's R2 keys so the client can
 * remove the corresponding entries from its local Cache Storage.
 * (Actual cache deletion happens client-side.)
 */
export async function DELETE(request: NextRequest) {
  try {
    const session = await auth.api.getSession({ headers: request.headers });

    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const renderJobId = request.nextUrl.searchParams.get("renderJobId");

    if (!renderJobId) {
      return NextResponse.json(
        { error: "renderJobId query parameter is required" },
        { status: 400 }
      );
    }

    const job = await db.query.renderJobs.findFirst({
      where: and(
        eq(renderJobs.id, renderJobId),
        eq(renderJobs.userId, Number(session.user.id))
      ),
    });

    if (!job) {
      return NextResponse.json(
        { error: "Render job not found" },
        { status: 404 }
      );
    }

    // Return the job id so the client knows which cache entries to purge.
    return NextResponse.json({ renderJobId: job.id, invalidated: true });
  } catch (error) {
    console.error("Error processing cache invalidation:", error);
    return NextResponse.json(
      { error: "Failed to process cache invalidation" },
      { status: 500 }
    );
  }
}
