import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { embedQuery, QUERY_MODEL } from "@/lib/embedding";
import {
  semanticSearchSongs,
  findTopMatchingLines,
  rrfRerank,
} from "@/lib/db/songs";
import {
  parseAlbumFilterValues,
  parseAlbumValues,
  parseBpmRangeParams,
  parseKeysParam,
} from "@/lib/db/search-helpers";
import { z } from "zod";
import type { AlbumFilter } from "@/lib/search/album-filter";

const AlbumFilterSchema = z.object({
  albumName: z.string(),
  albumSeries: z.string().nullable().optional(),
});

const RequestSchema = z.object({
  query: z.string().min(1, "query must not be empty"),
  limit: z.number().int().min(1).max(50).default(20),
  albums: z.array(z.union([z.string(), AlbumFilterSchema])).optional(),
  keys: z.array(z.string()).optional(),
  bpmRange: z.union([z.string(), z.array(z.string())]).optional(),
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
    const albumValues = parsed.data.albums ?? [];
    const albumFilters = parseAlbumFilterValues(
      albumValues.filter((album): album is AlbumFilter => typeof album !== "string").map((album) => ({
        albumName: album.albumName,
        albumSeries: album.albumSeries ?? null,
      }))
    );
    const albums = parseAlbumValues(albumValues.filter((album): album is string => typeof album === "string"));
    const keys = parseKeysParam(parsed.data.keys?.join(",") ?? null);
    const bpmRangeRaw = parsed.data.bpmRange;
    const bpmRangeParams = bpmRangeRaw
      ? Array.isArray(bpmRangeRaw) ? bpmRangeRaw : [bpmRangeRaw]
      : [];
    const bpmRange = parseBpmRangeParams(bpmRangeParams);
    const semanticOptions = albumFilters || albums || keys || bpmRange
      ? { albumFilters, albums, keys, bpmRange }
      : undefined;

    let queryEmbedding: number[];
    try {
      queryEmbedding = await embedQuery(query);
    } catch {
      return NextResponse.json(
        { error: "Semantic search unavailable. Try Search mode." },
        { status: 503 }
      );
    }

    const overfetchLimit = limit * 2;
    const songs = await semanticSearchSongs(
      queryEmbedding,
      QUERY_MODEL,
      overfetchLimit,
      ["published", "review"],
      ...(semanticOptions ? [semanticOptions] as const : []),
    );

    const snippets = await findTopMatchingLines(
      queryEmbedding,
      songs.map((s) => s.id)
    );

    const rerankedSongs = rrfRerank(songs, snippets);

    const trimmed = rerankedSongs.slice(0, limit);

    const songsWithSnippets = trimmed.map((s) => {
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      const { rrfScore, ...rest } = s;
      return {
        ...rest,
        matchingSnippet: snippets.get(s.id)?.[0]?.lineText ?? null,
        whyThisMatch: snippets.get(s.id)?.map((l) => l.lineText) ?? [],
      };
    });

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
