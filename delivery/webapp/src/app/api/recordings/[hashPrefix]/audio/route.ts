import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { db } from "@/db";
import { recordings } from "@/db/schema";
import { and, eq, inArray, isNull } from "drizzle-orm";
import { createR2ClientFromEnv, type R2Client } from "@/lib/r2/client";

// Signed-URL expiry for the proxied audio fetch. 60 seconds keeps the minted
// URL scoped to this single request; the browser never sees it.
const PROXY_SIGNED_URL_EXPIRES_IN_SECONDS = 60;

/**
 * GET /api/recordings/[hashPrefix]/audio
 *
 * Same-origin audio proxy for the Song Arrangement Editor's browser-side Web
 * Audio preview (#254 / #285). Signed R2 URLs alone cannot be fetched by the
 * browser here because the bucket is not CORS-configured for browser
 * fetch/Web Audio, so the webapp fetches R2 server-side and streams the body
 * through, never buffering the MP3 in Next.js.
 *
 * Visibility matches /api/signed-url (shared-handler.ts): the Recording must
 * exist with visibilityStatus in ["published","review"] and not be deleted.
 * Range requests are forwarded so the preview transport can seek; the
 * upstream status (200/206), Content-Length, Content-Range, Accept-Ranges,
 * Content-Type and a private-cache Cache-Control are mirrored back.
 */
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ hashPrefix: string }> }
) {
  const session = await auth.api.getSession({ headers: request.headers });
  if (!session?.user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { hashPrefix } = await params;

  const recording = await db.query.recordings.findFirst({
    where: and(
      eq(recordings.hashPrefix, hashPrefix),
      inArray(recordings.visibilityStatus, ["published", "review"]),
      isNull(recordings.deletedAt)
    ),
  });

  if (!recording) {
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

  // Key pattern from r2_client.py: {hashPrefix}/audio.mp3
  const { url: signedUrl } = await r2Client.getAudioSignedUrl(hashPrefix, {
    expiresInSeconds: PROXY_SIGNED_URL_EXPIRES_IN_SECONDS,
  });

  const rangeHeader = request.headers.get("range");
  const r2Headers: Record<string, string> = {};
  if (rangeHeader) {
    r2Headers.Range = rangeHeader;
  }

  let r2Response: Response;
  try {
    r2Response = await fetch(signedUrl, { headers: r2Headers });
  } catch (error) {
    console.error("Error fetching recording audio from R2:", error);
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
  responseHeaders.set("Content-Type", "audio/mpeg");
  responseHeaders.set("Accept-Ranges", "bytes");
  responseHeaders.set("Cache-Control", "private, max-age=3600");

  const contentLength = r2Response.headers.get("content-length");
  if (contentLength) {
    responseHeaders.set("Content-Length", contentLength);
  }

  const contentRange = r2Response.headers.get("content-range");
  if (contentRange) {
    responseHeaders.set("Content-Range", contentRange);
  }

  // Pass the upstream body straight through as a stream — do not buffer it.
  const status = r2Response.status === 206 ? 206 : 200;
  return new NextResponse(r2Response.body, {
    status,
    headers: responseHeaders,
  });
}
