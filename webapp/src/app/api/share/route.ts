import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { db } from "@/db";
import { songsetShares, renderJobs, songsets } from "@/db/schema";
import { eq, and, isNull, count, or, gt } from "drizzle-orm";
import { nanoid } from "nanoid";
import { resolvePublicOrigin } from "@/lib/share";

const MAX_ACTIVE_SHARES = 20;

const activeShareConditions = (userId: number) =>
  and(
    eq(songsetShares.createdByUserId, userId),
    isNull(songsetShares.revokedAt),
    or(isNull(songsetShares.expiresAt), gt(songsetShares.expiresAt, new Date()))
  );

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

    const { songsetId, renderJobId, allowDownload = false } = body as {
      songsetId?: unknown;
      renderJobId?: unknown;
      allowDownload?: unknown;
    };

    const hasSongsetId = songsetId && typeof songsetId === "string";
    const hasRenderJobId = renderJobId && typeof renderJobId === "string";

    if (hasSongsetId && hasRenderJobId) {
      return NextResponse.json(
        { error: "Provide either songsetId or renderJobId, not both" },
        { status: 400 }
      );
    }

    if (!hasSongsetId && !hasRenderJobId) {
      return NextResponse.json(
        { error: "Either songsetId or renderJobId is required" },
        { status: 400 }
      );
    }

    const origin = resolvePublicOrigin(request);
    if (!origin) {
      return NextResponse.json({ error: "Cannot determine public origin" }, { status: 500 });
    }

    const normalizedAllowDownload = allowDownload === true;

    if (hasSongsetId) {
      const songset = await db.query.songsets.findFirst({
        where: and(eq(songsets.id, songsetId as string), eq(songsets.userId, userId)),
      });

      if (!songset) {
        return NextResponse.json({ error: "Songset not found" }, { status: 404 });
      }

      const existingShare = await db.query.songsetShares.findFirst({
        where: and(
          eq(songsetShares.songsetId, songsetId as string),
          isNull(songsetShares.revokedAt),
          or(isNull(songsetShares.expiresAt), gt(songsetShares.expiresAt, new Date()))
        ),
      });

      if (existingShare) {
        const shareUrl = `${origin}/share/${existingShare.token}`;
        return NextResponse.json({
          token: existingShare.token,
          shareUrl,
          songsetId: existingShare.songsetId,
          renderJobId: existingShare.renderJobId,
          allowDownload: existingShare.allowDownload,
        });
      }

      const [activeCount] = await db
        .select({ value: count() })
        .from(songsetShares)
        .where(activeShareConditions(userId));

      if ((activeCount?.value ?? 0) >= MAX_ACTIVE_SHARES) {
        return NextResponse.json(
          { error: "Maximum of 20 active shares reached. Revoke some to create new ones." },
          { status: 422 }
        );
      }

      const token = nanoid(24);

      await db.insert(songsetShares).values({
        token,
        songsetId: songsetId as string,
        renderJobId: null,
        createdByUserId: userId,
        allowDownload: normalizedAllowDownload,
        createdAt: new Date(),
      });

      const shareUrl = `${origin}/share/${token}`;

      return NextResponse.json(
        { token, shareUrl, songsetId: songsetId as string, renderJobId: null, allowDownload: normalizedAllowDownload },
        { status: 201 }
      );
    }

    const job = await db.query.renderJobs.findFirst({
      where: and(eq(renderJobs.id, renderJobId as string), eq(renderJobs.userId, userId)),
    });

    if (!job) {
      return NextResponse.json({ error: "Render job not found" }, { status: 404 });
    }

    if (job.status !== "completed") {
      return NextResponse.json({ error: "Render job is not completed" }, { status: 409 });
    }

    const [activeCount] = await db
      .select({ value: count() })
      .from(songsetShares)
      .where(activeShareConditions(userId));

    if ((activeCount?.value ?? 0) >= MAX_ACTIVE_SHARES) {
      return NextResponse.json(
        { error: "Maximum of 20 active shares reached. Revoke some to create new ones." },
        { status: 422 }
      );
    }

    const token = nanoid(24);

    await db.insert(songsetShares).values({
      token,
      songsetId: job.songsetId,
      renderJobId: renderJobId as string,
      createdByUserId: userId,
      allowDownload: normalizedAllowDownload,
      createdAt: new Date(),
    });

    const shareUrl = `${origin}/share/${token}`;

    return NextResponse.json(
      { token, shareUrl, songsetId: job.songsetId, renderJobId: renderJobId as string, allowDownload: normalizedAllowDownload },
      { status: 201 }
    );
  } catch (error) {
    console.error("Error creating share token:", error);
    return NextResponse.json({ error: "Failed to create share token" }, { status: 500 });
  }
}

export async function GET(request: NextRequest) {
  try {
    const session = await auth.api.getSession({ headers: request.headers });
    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const userId = Number(session.user.id);
    const songsetId = request.nextUrl.searchParams.get("songsetId");
    const renderJobId = request.nextUrl.searchParams.get("renderJobId");

    const origin = resolvePublicOrigin(request);

    const conditions: Parameters<typeof and>[0][] = [
      eq(songsetShares.createdByUserId, userId),
      isNull(songsetShares.revokedAt),
      or(isNull(songsetShares.expiresAt), gt(songsetShares.expiresAt, new Date())),
    ];

    if (songsetId) {
      const songset = await db.query.songsets.findFirst({
        where: and(eq(songsets.id, songsetId), eq(songsets.userId, userId)),
      });
      if (!songset) {
        return NextResponse.json({ error: "Songset not found" }, { status: 404 });
      }
      conditions.push(eq(songsetShares.songsetId, songsetId));
    }

    if (renderJobId) {
      const job = await db.query.renderJobs.findFirst({
        where: and(eq(renderJobs.id, renderJobId), eq(renderJobs.userId, userId)),
      });
      if (!job) {
        return NextResponse.json({ error: "Render job not found" }, { status: 404 });
      }
      conditions.push(eq(songsetShares.renderJobId, renderJobId));
    }

    const shares = await db
      .select()
      .from(songsetShares)
      .where(and(...(conditions as [Parameters<typeof and>[0], ...Parameters<typeof and>[0][]])));

    const sharesWithUrls = shares.map((s) => ({
      ...s,
      shareUrl: origin ? `${origin}/share/${s.token}` : null,
    }));

    return NextResponse.json({ shares: sharesWithUrls });
  } catch (error) {
    console.error("Error listing shares:", error);
    return NextResponse.json({ error: "Failed to list shares" }, { status: 500 });
  }
}
