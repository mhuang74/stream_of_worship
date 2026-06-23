import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { db } from "@/db";
import { renderJobs } from "@/db/schema";
import { eq, and } from "drizzle-orm";
import { createR2ClientFromEnv } from "@/lib/r2/client";

/**
 * GET /api/render-jobs/[id]/artifact-sizes
 * Returns the byte sizes of the render job's MP3 and MP4 artifacts via HEAD requests.
 * Used by the ShareDialog to determine which "Send File" app buttons to enable.
 */
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const session = await auth.api.getSession({ headers: request.headers });
    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const { id } = await params;
    const userId = Number(session.user.id);

    const job = await db.query.renderJobs.findFirst({
      where: and(eq(renderJobs.id, id), eq(renderJobs.userId, userId)),
    });

    if (!job) {
      return NextResponse.json({ error: "Render job not found" }, { status: 404 });
    }

    if (job.status !== "completed") {
      return NextResponse.json({ error: "Render job is not completed" }, { status: 409 });
    }

    let r2Client: ReturnType<typeof createR2ClientFromEnv>;
    try {
      r2Client = createR2ClientFromEnv();
    } catch {
      return NextResponse.json({ error: "R2 storage not configured" }, { status: 503 });
    }

    const [mp3SizeBytes, mp4SizeBytes] = await Promise.all([
      job.mp3R2Key ? r2Client.getObjectSize(job.mp3R2Key) : null,
      job.mp4R2Key ? r2Client.getObjectSize(job.mp4R2Key) : null,
    ]);

    return NextResponse.json({
      renderJobId: id,
      mp3SizeBytes,
      mp4SizeBytes,
    });
  } catch (error) {
    console.error("Error fetching artifact sizes:", error);
    return NextResponse.json({ error: "Failed to fetch artifact sizes" }, { status: 500 });
  }
}
