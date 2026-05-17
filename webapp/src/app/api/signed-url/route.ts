import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { generateSignedUrlResponse } from "./shared-handler";
import { z } from "zod";

const signedUrlRequestSchema = z.object({
  hashPrefix: z.string().min(1).optional(),
  renderJobId: z.string().min(1).optional(),
  fileType: z.enum(["audio", "video", "lrc", "json"]).optional(),
  expiresInSeconds: z.number().int().min(60).max(86400).optional(),
  contentDisposition: z.string().optional(),
});

export async function POST(request: NextRequest) {
  try {
    const session = await auth.api.getSession({
      headers: request.headers,
    });

    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

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
          details: parseResult.error.issues,
        },
        { status: 400 }
      );
    }

    return await generateSignedUrlResponse(Number(session.user.id), parseResult.data);
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

export async function GET(request: NextRequest) {
  try {
    const session = await auth.api.getSession({
      headers: request.headers,
    });

    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const searchParams = request.nextUrl.searchParams;
    const hashPrefix = searchParams.get("hashPrefix") || undefined;
    const renderJobId = searchParams.get("renderJobId") || undefined;
    const fileTypeRaw = searchParams.get("fileType");
    const allowedFileTypes = ["audio", "video", "lrc", "json"] as const;
    if (fileTypeRaw !== null && !allowedFileTypes.includes(fileTypeRaw as typeof allowedFileTypes[number])) {
      return NextResponse.json(
        { error: "Invalid fileType. Must be one of: audio, video, lrc, json" },
        { status: 400 }
      );
    }
    const fileType = fileTypeRaw as "audio" | "video" | "lrc" | "json" | undefined;
    const expiresInSecondsRaw = searchParams.get("expiresInSeconds");
    const expiresInSeconds = expiresInSecondsRaw
      ? parseInt(expiresInSecondsRaw, 10)
      : undefined;
    const contentDisposition = searchParams.get("contentDisposition") || undefined;

    if (expiresInSeconds !== undefined) {
      if (isNaN(expiresInSeconds) || expiresInSeconds < 60 || expiresInSeconds > 86400) {
        return NextResponse.json(
          { error: "expiresInSeconds must be between 60 and 86400" },
          { status: 400 }
        );
      }
    }

    return await generateSignedUrlResponse(Number(session.user.id), {
      hashPrefix,
      renderJobId,
      fileType,
      expiresInSeconds,
      contentDisposition,
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
