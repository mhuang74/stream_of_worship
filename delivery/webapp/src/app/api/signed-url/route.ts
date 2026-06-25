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
  // When true, mint the MP4 with the 4-hour Cast-playback expiry so the
  // logged-in phone can hand the resulting R2 presigned URL to the TV receiver
  // (TV only hits R2, never the webapp). Omitting or setting false keeps the
  // default 1-hour window used for phone-only preview fetches.
  cast: z.boolean().optional(),
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
    const params = {
      hashPrefix: searchParams.get("hashPrefix") || undefined,
      renderJobId: searchParams.get("renderJobId") || undefined,
      fileType: searchParams.get("fileType") || undefined,
      expiresInSeconds: searchParams.get("expiresInSeconds")
        ? parseInt(searchParams.get("expiresInSeconds")!, 10)
        : undefined,
      contentDisposition: searchParams.get("contentDisposition") || undefined,
      cast: searchParams.get("cast") === "true" ? true : searchParams.get("cast") === "false" ? false : undefined,
    };

    const parseResult = signedUrlRequestSchema.safeParse(params);
    if (!parseResult.success) {
      return NextResponse.json(
        {
          error: "Invalid query parameters",
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
