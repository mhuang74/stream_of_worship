import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { createR2ClientFromEnv } from "@/lib/r2/client";
import { z } from "zod";

const previewRequestSchema = z.object({
  fromHash: z.string().min(1).optional(),
  toHash: z.string().min(1).optional(),
  settings: z
    .object({
      gapBeats: z.number().min(0).max(8),
      crossfadeEnabled: z.boolean(),
      crossfadeDurationSeconds: z.number().min(0).max(10),
      keyShiftSemitones: z.number().int().min(-6).max(6),
      tempoRatio: z.number().min(0.5).max(2.0),
    })
    .optional(),
});

// Returns a signed URL for the "to" song (or "from" song as fallback),
// which the client plays via the global audio player to preview the transition.
export async function POST(request: NextRequest) {
  try {
    const session = await auth.api.getSession({ headers: request.headers });
    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    let body: unknown;
    try {
      body = await request.json();
    } catch {
      return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
    }

    const parsed = previewRequestSchema.safeParse(body);
    if (!parsed.success) {
      return NextResponse.json(
        { error: "Invalid request body", details: parsed.error.issues },
        { status: 400 }
      );
    }

    const { fromHash, toHash } = parsed.data;

    if (!fromHash && !toHash) {
      return NextResponse.json(
        { error: "At least one of fromHash or toHash is required" },
        { status: 400 }
      );
    }

    // Prefer the "to" song for preview so users hear what they're transitioning into
    const previewHash = toHash || fromHash!;

    const r2Client = createR2ClientFromEnv();
    const result = await r2Client.getAudioSignedUrl(previewHash, {
      expiresInSeconds: 3600,
    });

    return NextResponse.json({
      url: result.url,
      expiresAt: result.expiresAt.toISOString(),
      previewHash,
    });
  } catch (error) {
    console.error("Error generating transition preview URL:", error);

    if (error instanceof Error && error.message.includes("R2 credentials not configured")) {
      return NextResponse.json({ error: "R2 storage not configured" }, { status: 503 });
    }

    return NextResponse.json({ error: "Failed to generate preview URL" }, { status: 500 });
  }
}
