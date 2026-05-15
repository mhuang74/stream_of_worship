import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { createR2ClientFromEnv, SignedUrlOptions } from "@/lib/r2/client";
import { z } from "zod";

const signedUrlRequestSchema = z.object({
  key: z.string().min(1).optional(),
  hashPrefix: z.string().min(1).optional(),
  renderJobId: z.string().min(1).optional(),
  fileType: z.enum(["audio", "video", "lrc", "json"]).optional(),
  expiresInSeconds: z.number().int().min(60).max(86400).optional(),
  contentDisposition: z.string().optional(),
});

export async function POST(request: NextRequest) {
  try {
    // Check authentication
    const session = await auth.api.getSession({
      headers: request.headers,
    });

    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    // Parse and validate request body
    let body: unknown;
    try {
      body = await request.json();
    } catch {
      return NextResponse.json(
        { error: "Invalid JSON body" },
        { status: 400 }
      );
    }

    const parseResult = signedUrlRequestSchema.safeParse(body);
    if (!parseResult.success) {
      return NextResponse.json(
        {
          error: "Invalid request body",
          details: parseResult.error.errors,
        },
        { status: 400 }
      );
    }

    const params = parseResult.data;

    // Validate that at least one identifier is provided
    if (!params.key && !params.hashPrefix && !params.renderJobId) {
      return NextResponse.json(
        {
          error:
            "Must provide one of: key (full R2 path), hashPrefix (for source files), or renderJobId (for rendered outputs)",
        },
        { status: 400 }
      );
    }

    // Create R2 client
    const r2Client = createR2ClientFromEnv();

    // Build options
    const options: SignedUrlOptions = {
      expiresInSeconds: params.expiresInSeconds || 3600, // Default 1 hour
      contentDisposition: params.contentDisposition,
    };

    // Generate signed URL based on identifier type
    let result;
    const fileType = params.fileType || "audio";

    if (params.key) {
      // Direct key provided
      result = await r2Client.generateSignedUrl(params.key, fileType, options);
    } else if (params.renderJobId) {
      // Render job ID provided - generate URL for rendered output
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
      // Hash prefix provided - generate URL for source files
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
      // This should never happen due to validation above
      return NextResponse.json(
        { error: "Invalid request parameters" },
        { status: 400 }
      );
    }

    // Return signed URL with metadata
    return NextResponse.json({
      url: result.url,
      expiresAt: result.expiresAt.toISOString(),
      cacheControl: result.cacheControl,
    });
  } catch (error) {
    console.error("Error generating signed URL:", error);

    // Handle specific error types
    if (error instanceof Error) {
      if (error.message.includes("R2 credentials not configured")) {
        return NextResponse.json(
          { error: "R2 storage not configured" },
          { status: 503 }
        );
      }
    }

    return NextResponse.json(
      { error: "Failed to generate signed URL" },
      { status: 500 }
    );
  }
}

export async function GET(request: NextRequest) {
  try {
    // Check authentication
    const session = await auth.api.getSession({
      headers: request.headers,
    });

    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    // Parse query parameters
    const searchParams = request.nextUrl.searchParams;
    const key = searchParams.get("key") || undefined;
    const hashPrefix = searchParams.get("hashPrefix") || undefined;
    const renderJobId = searchParams.get("renderJobId") || undefined;
    const fileType = searchParams.get("fileType") as
      | "audio"
      | "video"
      | "lrc"
      | "json"
      | undefined;
    const expiresInSeconds = searchParams.get("expiresInSeconds")
      ? parseInt(searchParams.get("expiresInSeconds")!)
      : undefined;
    const contentDisposition = searchParams.get("contentDisposition") || undefined;

    // Validate that at least one identifier is provided
    if (!key && !hashPrefix && !renderJobId) {
      return NextResponse.json(
        {
          error:
            "Must provide one of: key (full R2 path), hashPrefix (for source files), or renderJobId (for rendered outputs)",
        },
        { status: 400 }
      );
    }

    // Validate expiresInSeconds if provided
    if (expiresInSeconds !== undefined) {
      if (isNaN(expiresInSeconds) || expiresInSeconds < 60 || expiresInSeconds > 86400) {
        return NextResponse.json(
          { error: "expiresInSeconds must be between 60 and 86400" },
          { status: 400 }
        );
      }
    }

    // Create R2 client
    const r2Client = createR2ClientFromEnv();

    // Build options
    const options: SignedUrlOptions = {
      expiresInSeconds: expiresInSeconds || 3600,
      contentDisposition,
    };

    // Generate signed URL based on identifier type
    let result;
    const resolvedFileType = fileType || "audio";

    if (key) {
      // Direct key provided
      result = await r2Client.generateSignedUrl(key, resolvedFileType, options);
    } else if (renderJobId) {
      // Render job ID provided
      if (resolvedFileType === "video") {
        result = await r2Client.getVideoSignedUrl(renderJobId, options);
      } else if (resolvedFileType === "audio") {
        result = await r2Client.getRenderedAudioSignedUrl(renderJobId, options);
      } else if (resolvedFileType === "json") {
        result = await r2Client.getChaptersSignedUrl(renderJobId, options);
      } else {
        return NextResponse.json(
          {
            error:
              "For renderJobId, fileType must be 'audio', 'video', or 'json'",
          },
          { status: 400 }
        );
      }
    } else if (hashPrefix) {
      // Hash prefix provided
      if (resolvedFileType === "audio") {
        result = await r2Client.getAudioSignedUrl(hashPrefix, options);
      } else if (resolvedFileType === "lrc") {
        result = await r2Client.getLrcSignedUrl(hashPrefix, options);
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

    // Return signed URL with metadata
    return NextResponse.json({
      url: result.url,
      expiresAt: result.expiresAt.toISOString(),
      cacheControl: result.cacheControl,
    });
  } catch (error) {
    console.error("Error generating signed URL:", error);

    if (error instanceof Error) {
      if (error.message.includes("R2 credentials not configured")) {
        return NextResponse.json(
          { error: "R2 storage not configured" },
          { status: 503 }
        );
      }
    }

    return NextResponse.json(
      { error: "Failed to generate signed URL" },
      { status: 500 }
    );
  }
}
