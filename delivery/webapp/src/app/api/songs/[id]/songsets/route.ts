import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { findSongsetsContainingSong } from "@/lib/db/songsets";

interface RouteParams {
  params: Promise<{ id: string }>;
}

export async function GET(request: NextRequest, { params }: RouteParams) {
  try {
    const session = await auth.api.getSession({ headers: request.headers });
    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const { id: rawSongId } = await params;
    const songId = rawSongId.trim();
    if (!songId) {
      return NextResponse.json({ error: "Invalid song id" }, { status: 400 });
    }

    const rawOrigin = request.nextUrl.searchParams.get("origin");
    const originSongsetId = rawOrigin?.trim() || null;

    const songsets = await findSongsetsContainingSong(
      songId,
      Number(session.user.id),
      originSongsetId
    );

    return NextResponse.json({ songsets });
  } catch (error) {
    console.error("Error finding containing songsets:", error);
    return NextResponse.json(
      { error: "Failed to find containing songsets" },
      { status: 500 }
    );
  }
}
