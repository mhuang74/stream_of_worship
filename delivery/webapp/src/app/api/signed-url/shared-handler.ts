import { NextResponse } from "next/server";
import { createR2ClientFromEnv, SignedUrlOptions } from "@/lib/r2/client";
import { db } from "@/db";
import { recordings, renderJobs } from "@/db/schema";
import { and, eq } from "drizzle-orm";

interface SignedUrlParams {
  hashPrefix?: string;
  renderJobId?: string;
  fileType?: string;
  expiresInSeconds?: number;
  contentDisposition?: string;
}

export async function generateSignedUrlResponse(
  userId: number,
  params: SignedUrlParams
): Promise<NextResponse> {
  if (!params.hashPrefix && !params.renderJobId) {
    return NextResponse.json(
      {
        error:
          "Must provide one of: hashPrefix (for published source files) or renderJobId (for your rendered outputs)",
      },
      { status: 400 }
    );
  }

  const r2Client = createR2ClientFromEnv();

  const options: SignedUrlOptions = {
    expiresInSeconds: params.expiresInSeconds || 3600,
    contentDisposition: params.contentDisposition,
  };

  let result;
  const fileType = params.fileType || "audio";

  if (params.renderJobId) {
    const renderJob = await db.query.renderJobs.findFirst({
      where: and(
        eq(renderJobs.id, params.renderJobId),
        eq(renderJobs.userId, userId)
      ),
    });

    if (!renderJob) {
      return NextResponse.json(
        { error: "Render job not found" },
        { status: 404 }
      );
    }

    if (fileType === "video") {
      result = await r2Client.getVideoSignedUrl(params.renderJobId, options);
    } else if (fileType === "audio") {
      result = await r2Client.getRenderedAudioSignedUrl(
        params.renderJobId,
        options
      );
    } else if (fileType === "json") {
      result = await r2Client.getChaptersSignedUrl(
        params.renderJobId,
        options
      );
    } else {
      return NextResponse.json(
        {
          error:
            "For renderJobId, fileType must be 'audio', 'video', or 'json'",
        },
        { status: 400 }
      );
    }
  } else if (params.hashPrefix) {
    const recording = await db.query.recordings.findFirst({
      where: and(
        eq(recordings.hashPrefix, params.hashPrefix),
        eq(recordings.visibilityStatus, "published")
      ),
    });

    if (!recording) {
      return NextResponse.json(
        { error: "Recording not found" },
        { status: 404 }
      );
    }

    if (fileType === "audio") {
      result = await r2Client.getAudioSignedUrl(params.hashPrefix, options);
    } else if (fileType === "lrc") {
      result = await r2Client.getLrcSignedUrl(params.hashPrefix, options);
    } else {
      return NextResponse.json(
        {
          error: "For hashPrefix, fileType must be 'audio' or 'lrc'",
        },
        { status: 400 }
      );
    }
  } else {
    return NextResponse.json(
      { error: "Invalid request parameters" },
      { status: 400 }
    );
  }

  return NextResponse.json({
    url: result.url,
    expiresAt: result.expiresAt.toISOString(),
    cacheControl: result.cacheControl,
  });
}
