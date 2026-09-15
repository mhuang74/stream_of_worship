import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { db } from "@/db";
import { renderJobs, songsetItems, recordings } from "@/db/schema";
import { eq, and, asc, isNotNull } from "drizzle-orm";

/**
 * GET /api/offline/cache?renderJobId=<id>
 *
 * Returns proxy URLs for a completed render job's artifacts so the
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

    // Per-chapter recording contentHashes, position-aligned with the
    // chapters manifest: entry i is songset item i's recording contentHash
    // (the Lyrics Feedback key); null when an item has no recording. A
    // left join keeps the array length equal to the item count so hashes
    // never shift across items. Lets the offline index persist the hashes
    // so lyrics feedback survives offline.
    const hashRows = await db
      .select({ contentHash: recordings.contentHash })
      .from(songsetItems)
      .leftJoin(recordings, eq(songsetItems.recordingHashPrefix, recordings.hashPrefix))
      .where(eq(songsetItems.songsetId, job.songsetId))
      .orderBy(asc(songsetItems.position));

    return NextResponse.json({
      renderJobId: job.id,
      mp3Url: job.mp3R2Key ? `/api/r2/artifact/${renderJobId}/output.mp3` : null,
      mp4Url: job.mp4R2Key ? `/api/r2/artifact/${renderJobId}/output.mp4` : null,
      chaptersUrl: job.chaptersR2Key ? `/api/r2/artifact/${renderJobId}/chapters.json` : null,
      chapterContentHashes: hashRows.map((row) => row.contentHash ?? null),
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
