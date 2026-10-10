import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { listDiscoverySongs } from "@/lib/db/discovery";

/** GET /api/discovery?limit=&offset= — aggregate "what others are playing" feed
 * (issue #253). Defaults limit 20 (cap 100), offset 0. Anonymous-safe: response
 * carries no user ids, usernames, or songset names. */
export async function GET(request: NextRequest) {
  try {
    const session = await auth.api.getSession({
      headers: request.headers,
    });

    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const searchParams = request.nextUrl.searchParams;
    const rawLimit = parseInt(searchParams.get("limit") ?? "20");
    const limit = Math.min(isNaN(rawLimit) ? 20 : rawLimit, 100);
    const rawOffset = parseInt(searchParams.get("offset") ?? "0");
    const offset = isNaN(rawOffset) ? 0 : Math.max(0, rawOffset);

    const result = await listDiscoverySongs(Number(session.user.id), limit, offset);

    return NextResponse.json(result);
  } catch (error) {
    console.error("Error listing discovery songs:", error);
    return NextResponse.json(
      { error: "Failed to load discovery songs" },
      { status: 500 }
    );
  }
}
