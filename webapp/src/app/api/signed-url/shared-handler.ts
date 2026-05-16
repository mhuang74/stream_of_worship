import { NextResponse } from "next/server";
import { createR2ClientFromEnv, SignedUrlOptions } from "@/lib/r2/client";

interface SignedUrlParams {
  key?: string;
  hashPrefix?: string;
  renderJobId?: string;
  fileType?: string;
  expiresInSeconds?: number;
  contentDisposition?: string;
}

export async function generateSignedUrlResponse(
  params: SignedUrlParams
): Promise<NextResponse> {
  if (!params.key && !params.hashPrefix && !params.renderJobId) {
    return NextResponse.json(
      {
        error:
          "Must provide one of: key (full R2 path), hashPrefix (for source files), or renderJobId (for rendered outputs)",
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

  if (params.key) {
    result = await r2Client.generateSignedUrl(params.key, fileType, options);
  } else if (params.renderJobId) {
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
