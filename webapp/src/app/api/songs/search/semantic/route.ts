// POST /api/songs/search/semantic — natural language song search via pgvector
import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { generateEmbedding } from "@/lib/embed/client";
import { semanticSearchSongs } from "@/lib/db/songs";
import { z } from "zod";

export const runtime = "nodejs";

const RequestSchema = z.object({
  query: z.string().min(1, "query must not be empty").max(500, "query too long"),
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

    const { query, limit } = parsed.data;

    const embedding = await generateEmbedding(query);
    const songs = await semanticSearchSongs(embedding, limit);

    return NextResponse.json({ songs, query, total: songs.length });
  } catch (error) {
    console.error("Error in semantic search:", error);
    const message = error instanceof Error ? error.message : "Semantic search failed";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
