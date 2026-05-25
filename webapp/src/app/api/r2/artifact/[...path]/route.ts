import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { db } from "@/db";
import { renderJobs } from "@/db/schema";
import { eq, and } from "drizzle-orm";
import { createR2ClientFromEnv } from "@/lib/r2/client";

const ALLOWED_FILES = ["output.mp3", "output.mp4", "chapters.json"] as const;
const CONTENT_TYPES: Record<string, string> = {
  "output.mp3": "audio/mpeg",
  "output.mp4": "video/mp4",
  "chapters.json": "application/json",
};

const FILE_TYPES: Record<string, "audio" | "video" | "json"> = {
  "output.mp3": "audio",
  "output.mp4": "video",
  "chapters.json": "json",
};

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  const session = await auth.api.getSession({ headers: request.headers });
  if (!session?.user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { path } = await params;
  if (path.length !== 2) {
    return NextResponse.json({ error: "Invalid path" }, { status: 400 });
  }
  const [renderJobId, filename] = path;

  if (!ALLOWED_FILES.includes(filename as (typeof ALLOWED_FILES)[number])) {
    return NextResponse.json({ error: "Invalid file" }, { status: 400 });
  }

  const job = await db.query.renderJobs.findFirst({
    where: and(
      eq(renderJobs.id, renderJobId),
      eq(renderJobs.userId, Number(session.user.id))
    ),
    with: {
      songset: {
        columns: {
          name: true,
        },
      },
    },
  });
  if (!job) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }

  if (job.status !== "completed") {
    return NextResponse.json({ error: "Render not complete" }, { status: 409 });
  }

  const r2Key = `renders/${renderJobId}/${filename}`;

  let r2Client: ReturnType<typeof createR2ClientFromEnv>;
  try {
    r2Client = createR2ClientFromEnv();
  } catch {
    return NextResponse.json({ error: "R2 storage not configured" }, { status: 503 });
  }

  const fileType = FILE_TYPES[filename];
  const { url: signedUrl } = await r2Client.generateSignedUrl(r2Key, fileType, {
    expiresInSeconds: 60,
  });

  const rangeHeader = request.headers.get("range");
  const r2Headers: HeadersInit = {};
  if (rangeHeader) {
    r2Headers["Range"] = rangeHeader;
  }

  const r2Response = await fetch(signedUrl, { headers: r2Headers });

  if (!r2Response.ok && r2Response.status !== 206) {
    return NextResponse.json({ error: "Failed to fetch from storage" }, { status: 502 });
  }

  const responseHeaders = new Headers();
  responseHeaders.set("Content-Type", CONTENT_TYPES[filename]);
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

  const searchParams = request.nextUrl.searchParams;
  if (searchParams.get("download") === "1") {
    const songsetName = job.songset?.name || "worship";
    const ext = filename.split(".").pop();
    const safeName = songsetName.toLowerCase().replace(/[^a-z0-9]/g, "-");
    responseHeaders.set(
      "Content-Disposition",
      `attachment; filename="${safeName}.${ext}"`
    );
  }

  const status = r2Response.status === 206 ? 206 : 200;
  return new NextResponse(r2Response.body, {
    status,
    headers: responseHeaders,
  });
}
