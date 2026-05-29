import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { embedQuery, QUERY_MODEL } from "@/lib/embedding";
import {
  semanticSearchSongs,
  findTopMatchingLines,
} from "@/lib/db/songs";
import { z } from "zod";

const RequestSchema = z.object({
  query: z.string().min(1, "query must not be empty"),
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

    let queryEmbedding: number[];
    try {
      queryEmbedding = await embedQuery(query);
    } catch {
      return NextResponse.json(
        { error: "Semantic search unavailable. Try Search mode." },
        { status: 503 }
      );
    }

    const songs = await semanticSearchSongs(queryEmbedding, limit, QUERY_MODEL);

    const snippets = await findTopMatchingLines(
      queryEmbedding,
      songs.map((s) => s.id)
    );

    const songsWithSnippets = songs.map((s) => ({
      ...s,
      matchingSnippet: snippets.get(s.id)?.[0]?.lineText ?? null,
      whyThisMatch: snippets.get(s.id)?.map((l) => l.lineText) ?? [],
    }));

    return NextResponse.json({
      songs: songsWithSnippets,
      query,
      total: songsWithSnippets.length,
    });
  } catch (error) {
    console.error("Error in semantic search:", error);
    const message =
      error instanceof Error ? error.message : "Semantic search failed";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
