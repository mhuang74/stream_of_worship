import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { fullTextSearchSongs } from "@/lib/db/search";
import {
  parseAlbumFilterParams,
  parseKeysParam,
  parseBpmRangeParam,
} from "@/lib/db/search-helpers";

export async function GET(request: NextRequest) {
  try {
    const session = await auth.api.getSession({
      headers: request.headers,
    });

    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const searchParams = request.nextUrl.searchParams;
    const query = searchParams.get("q");

    if (!query || query.trim().length === 0) {
      return NextResponse.json(
        { error: "Search query is required" },
        { status: 400 }
      );
    }

    const limit = Math.min(
      parseInt(searchParams.get("limit") ?? "50"),
      100
    );
    const offset = parseInt(searchParams.get("offset") ?? "0");

    // Default to published + review for browse; respect explicit client override
    const visibilityParam = searchParams.get("visibilityStatus");
    const visibilityStatus: string | string[] = visibilityParam
      ? (visibilityParam.includes(",") ? visibilityParam.split(",") : visibilityParam)
      : ["published", "review"];

    const keys = parseKeysParam(searchParams.get("keys"));
    const bpmRange = parseBpmRangeParam(searchParams.get("bpmRange"));
    const { albumFilters, albumNames: albums } = parseAlbumFilterParams(searchParams);

    const result = await fullTextSearchSongs(query, limit, offset, visibilityStatus, {
      albums,
      albumFilters,
      keys,
      bpmRange,
    });

    return NextResponse.json(result);
  } catch (error) {
    console.error("Error searching songs:", error);
    return NextResponse.json(
      { error: "Failed to search songs" },
      { status: 500 }
    );
  }
}
