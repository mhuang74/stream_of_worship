import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { getEmbeddingForRecording, semanticSearchSongs } from "@/lib/db/search";
import { z } from "zod";

const RequestSchema = z.object({
  recordingId: z.string().min(1, "recordingId must not be empty"),
  limit: z.number().int().min(1).max(50).default(20),
});

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
      return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
    }

    const parsed = RequestSchema.safeParse(body);
    if (!parsed.success) {
      return NextResponse.json(
        { error: parsed.error.issues[0]?.message ?? "Invalid request" },
        { status: 400 }
      );
    }

    const { recordingId, limit } = parsed.data;

    const embedding = await getEmbeddingForRecording(recordingId);
    if (!embedding) {
      return NextResponse.json(
        { error: "No embedding found for the specified recording" },
        { status: 400 }
      );
    }

    const songs = await semanticSearchSongs(embedding, limit);

    return NextResponse.json({ songs, recordingId, total: songs.length });
  } catch (error) {
    console.error("Error in semantic search:", error);
    const message = error instanceof Error ? error.message : "Semantic search failed";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
