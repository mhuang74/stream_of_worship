import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { listSongs } from "@/lib/db/songs";
import {
  parseAlbumFilterParams,
  parseKeysParam,
  parseBpmRangeParam,
} from "@/lib/db/search-helpers";
import type { AlbumFilter } from "@/lib/search/album-filter";

export async function GET(request: NextRequest) {
  try {
    const session = await auth.api.getSession({
      headers: request.headers,
    });

    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const searchParams = request.nextUrl.searchParams;
    const limit = Math.min(
      parseInt(searchParams.get("limit") ?? "50"),
      100
    );
    const offset = parseInt(searchParams.get("offset") ?? "0");

    // Parse filters
    const filters: {
      albumNames?: string[];
      albumFilters?: AlbumFilter[];
      albumSeries?: string;
      composer?: string;
      lyricist?: string;
      visibilityStatus?: string | string[];
      keys?: string[];
      bpmRange?: "slow" | "moderate" | "fast";
    } = {};

    const { albumFilters, albumNames } = parseAlbumFilterParams(searchParams);
    if (albumFilters) filters.albumFilters = albumFilters;
    if (albumNames) filters.albumNames = albumNames;

    const albumSeries = searchParams.get("albumSeries");
    if (albumSeries && !albumFilters) filters.albumSeries = albumSeries;

    const composer = searchParams.get("composer");
    if (composer) filters.composer = composer;

    const lyricist = searchParams.get("lyricist");
    if (lyricist) filters.lyricist = lyricist;

    const keys = parseKeysParam(searchParams.get("keys"));
    if (keys) filters.keys = keys;

    const bpmRange = parseBpmRangeParam(searchParams.get("bpmRange"));
    if (bpmRange) filters.bpmRange = bpmRange;

    // Default to published + review for browse; respect explicit client override
    const visibilityParam = searchParams.get("visibilityStatus");
    const visibilityStatus: string | string[] = visibilityParam
      ? (visibilityParam.includes(",") ? visibilityParam.split(",") : visibilityParam)
      : ["published", "review"];
    filters.visibilityStatus = visibilityStatus;

    const result = await listSongs(limit, offset, filters);

    return NextResponse.json(result);
  } catch (error) {
    console.error("Error listing songs:", error);
    return NextResponse.json(
      { error: "Failed to list songs" },
      { status: 500 }
    );
  }
}
