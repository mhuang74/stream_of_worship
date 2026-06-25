import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { db } from "@/db";
import { songsetShares, renderJobs } from "@/db/schema";
import { eq, and } from "drizzle-orm";
import {
  CAST_PLAYBACK_EXPIRES_IN_SECONDS,
  DEFAULT_EXPIRES_IN_SECONDS,
  createR2ClientFromEnv,
} from "@/lib/r2/client";
import { getSongsetPublicView } from "@/lib/db/songsets";
import { getClientIp, hashIp, enforceRateLimit } from "@/lib/rate-limit";

const NO_CACHE_HEADERS = {
  "Cache-Control": "no-store, no-cache, must-revalidate",
  Pragma: "no-cache",
};

// Per-token + per-IP rate limits on the public (no-auth) GET endpoint so
// anonymous callers cannot harvest fresh 4-hour MP4 URLs / songset metadata
// without bound (R2 egress cost + metadata scraping amplifiers).
const SHARE_RATE_LIMIT_RPM = 60;

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ token: string }> }
) {
  try {
    const { token } = await params;

    // Rate limit (per-token + per-IP). Upstash is used when configured;
    // otherwise the in-memory token-bucket fallback applies so a missing env
    // var never silently unlocks unbounded anonymous writes.
    const ipHash = await hashIp(getClientIp(request));
    const ipAllowed = await enforceRateLimit(
      `ip:${ipHash}`,
      "sow:share-ip",
      SHARE_RATE_LIMIT_RPM,
    );
    if (!ipAllowed) {
      return NextResponse.json(
        { error: "Rate limit exceeded" },
        { status: 429, headers: NO_CACHE_HEADERS },
      );
    }
    const tokenAllowed = await enforceRateLimit(
      `share:${token}`,
      "sow:share-token",
      SHARE_RATE_LIMIT_RPM,
    );
    if (!tokenAllowed) {
      return NextResponse.json(
        { error: "Rate limit exceeded" },
        { status: 429, headers: NO_CACHE_HEADERS },
      );
    }

    const share = await db.query.songsetShares.findFirst({
      where: eq(songsetShares.token, token),
    });

    if (!share) {
      return NextResponse.json({ error: "Share not found" }, { status: 404, headers: NO_CACHE_HEADERS });
    }

    if (share.revokedAt) {
      return NextResponse.json({ error: "This share link has been revoked" }, { status: 410, headers: NO_CACHE_HEADERS });
    }

    if (share.expiresAt && share.expiresAt < new Date()) {
      return NextResponse.json({ error: "This share link has expired" }, { status: 410, headers: NO_CACHE_HEADERS });
    }

    const shareType = share.renderJobId !== null ? "renderJob" : "songset";

    const songsetView = await getSongsetPublicView(share.songsetId);

    if (!songsetView) {
      return NextResponse.json({ error: "Share not found" }, { status: 404, headers: NO_CACHE_HEADERS });
    }

    let selectedRenderJobId: string | null = null;

    if (shareType === "renderJob" && share.renderJobId) {
      selectedRenderJobId = share.renderJobId;
    } else {
      selectedRenderJobId = songsetView.lastCompletedRenderJobId;
    }

    let playbackJob: typeof renderJobs.$inferSelect | null = null;
    if (selectedRenderJobId) {
      const job = await db.query.renderJobs.findFirst({
        where: and(
          eq(renderJobs.id, selectedRenderJobId),
          eq(renderJobs.songsetId, share.songsetId)
        ),
      });
      if (job && job.status === "completed") {
        playbackJob = job;
      } else {
        selectedRenderJobId = null;
      }
    }

    let isStale = false;
    let staleStatus: string | null = null;

    if (playbackJob?.completedAt && songsetView.updatedAt > playbackJob.completedAt) {
      isStale = true;
      staleStatus = "Playback may reflect an earlier render than the current song list";
    }

    let mp3Url: string | null = null;
    let mp4Url: string | null = null;
    let chaptersUrl: string | null = null;
    let mp3SizeBytes: number | null = null;
    let mp4SizeBytes: number | null = null;

    if (playbackJob) {
      try {
        const r2Client = createR2ClientFromEnv();
        // The share MP4 is intended for Cast / TV-share playback: the receiver
        // fetches it directly from R2 for up to 4h (full set + setup slack).
        // MP3/chapters stay on the default 1h window since they are phone-only.
        const mp3Expires = DEFAULT_EXPIRES_IN_SECONDS;
        const mp4Expires = CAST_PLAYBACK_EXPIRES_IN_SECONDS;

        const [mp3Result, mp4Result, chaptersResult, mp3Size, mp4Size] = await Promise.all([
          playbackJob.mp3R2Key
            ? r2Client.generateSignedUrl(playbackJob.mp3R2Key, "audio", { expiresInSeconds: mp3Expires })
            : null,
          playbackJob.mp4R2Key
            ? r2Client.generateSignedUrl(playbackJob.mp4R2Key, "video", { expiresInSeconds: mp4Expires })
            : null,
          playbackJob.chaptersR2Key
            ? r2Client.generateSignedUrl(playbackJob.chaptersR2Key, "json", { expiresInSeconds: mp3Expires })
            : null,
          playbackJob.mp3R2Key ? r2Client.getObjectSize(playbackJob.mp3R2Key) : null,
          playbackJob.mp4R2Key ? r2Client.getObjectSize(playbackJob.mp4R2Key) : null,
        ]);

        mp3Url = mp3Result?.url ?? null;
        mp4Url = mp4Result?.url ?? null;
        chaptersUrl = chaptersResult?.url ?? null;
        mp3SizeBytes = mp3Size;
        mp4SizeBytes = mp4Size;
      } catch (e) {
        // R2 not configured or error. Surface the root cause so an operator
        // can distinguish "no render job" (content-side) from "R2 unreachable
        // for the render job" (transient outage / misconfigured creds / wrong
        // bucket) — otherwise the controller UI surfaces the misleading
        // "No video available for this share" error and the Go/No-Go checklist
        // cannot diagnose an R2 reachability failure at runtime.
        console.error("share/[token]: R2 mint failure", { token, error: e });
      }
    }

    return NextResponse.json(
      {
        token,
        shareType,
        songset: {
          id: songsetView.id,
          name: songsetView.name,
          description: songsetView.description,
          totalDurationSeconds: songsetView.totalDurationSeconds,
          renderState: songsetView.renderState,
          latestRenderJobId: songsetView.latestRenderJobId,
          lastCompletedRenderJobId: songsetView.lastCompletedRenderJobId,
        },
        items: songsetView.items,
        playback: {
          selectedRenderJobId,
          isStale,
          staleStatus,
          mp3Url,
          mp4Url,
          chaptersUrl,
          mp3SizeBytes,
          mp4SizeBytes,
        },
        allowDownload: share.allowDownload,
        createdAt: share.createdAt,
        expiresAt: share.expiresAt,
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
