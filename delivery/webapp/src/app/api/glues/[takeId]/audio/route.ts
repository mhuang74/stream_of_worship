import { NextRequest, NextResponse } from "next/server";
import { and, eq, gt, isNotNull } from "drizzle-orm";
import { auth } from "@/lib/auth";
import { db } from "@/db";
import { glueTakes } from "@/db/schema";
import { createR2ClientFromEnv, type R2Client } from "@/lib/r2/client";

// Signed-URL expiry for the proxied audio fetch, matching the Recording audio
// proxy: the minted URL is scoped to this single request and never reaches the
// browser.
const PROXY_SIGNED_URL_EXPIRES_IN_SECONDS = 60;

/**
 * GET /api/glues/[takeId]/audio
 *
 * Same-origin audition proxy for one AI-transition take (spec §5.1/§5.4).
 * The client supplies only the selected take id; the immutable `r2_key` is
 * read from the owner-scoped `glue_takes` row, so another user's take id and
 * a forged key are both impossible. Only an effective ready take with staged
 * seconds is playable. The proxy streams R2 rather than buffering, forwards
 * `Range`, and mirrors the upstream 200/206 status and lengths.
 */
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ takeId: string }> }
) {
  const session = await auth.api.getSession({ headers: request.headers });
  if (!session?.user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { takeId } = await params;
  const userId = Number(session.user.id);
  if (!Number.isFinite(userId)) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }

  // Owner scoping is part of the predicate, not a post-read check: a foreign
  // take id must be indistinguishable from a missing one.
  const [take] = await db
    .select({
      id: glueTakes.id,
      status: glueTakes.status,
      r2Key: glueTakes.r2Key,
      seconds: glueTakes.seconds,
    })
    .from(glueTakes)
    .where(
      and(
        eq(glueTakes.id, takeId),
        eq(glueTakes.userId, userId),
        eq(glueTakes.status, "ready"),
        isNotNull(glueTakes.r2Key),
        gt(glueTakes.seconds, 0)
      )
    )
    .limit(1);

  if (
    !take ||
    take.status !== "ready" ||
    !take.r2Key ||
    typeof take.seconds !== "number" ||
    !(take.seconds > 0)
  ) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }

  let r2Client: R2Client;
  try {
    r2Client = createR2ClientFromEnv();
  } catch {
    return NextResponse.json(
      { error: "R2 storage not configured" },
      { status: 503 }
    );
  }

  const { url: signedUrl } = await r2Client.generateSignedUrl(
    take.r2Key,
    "audio",
    { expiresInSeconds: PROXY_SIGNED_URL_EXPIRES_IN_SECONDS }
  );

  const rangeHeader = request.headers.get("range");
  const r2Headers: Record<string, string> = {};
  if (rangeHeader) r2Headers.Range = rangeHeader;

  let r2Response: Response;
  try {
    r2Response = await fetch(signedUrl, { headers: r2Headers });
  } catch (error) {
    console.error("Error fetching glue audio from R2:", error);
    return NextResponse.json(
      { error: "Failed to fetch from storage" },
      { status: 502 }
    );
  }

  if (!r2Response.ok && r2Response.status !== 206) {
    return NextResponse.json(
      { error: "Failed to fetch from storage" },
      { status: 502 }
    );
  }

  const responseHeaders = new Headers();
  responseHeaders.set("Content-Type", "audio/wav");
  responseHeaders.set("Accept-Ranges", "bytes");
  // The staged WAV is immutable per take id; private caching may outlive this
  // response by an hour.
  responseHeaders.set("Cache-Control", "private, max-age=3600");

  const contentLength = r2Response.headers.get("content-length");
  if (contentLength) responseHeaders.set("Content-Length", contentLength);

  const contentRange = r2Response.headers.get("content-range");
  if (contentRange) responseHeaders.set("Content-Range", contentRange);

  const status = r2Response.status === 206 ? 206 : 200;
  return new NextResponse(r2Response.body, { status, headers: responseHeaders });
}
