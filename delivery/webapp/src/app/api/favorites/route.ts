import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { addFavorite, getFavoriteSongIds } from "@/lib/db/favorites";
import { z } from "zod";

const addFavoriteSchema = z.object({ songId: z.string().min(1) });

/**
 * GET /api/favorites → { songIds } — the current user's favorite song ids,
 * newest first. Lightweight; consumed by browse surfaces to render heart state.
 * POST /api/favorites { songId } → 201 — adds a favorite (idempotent).
 */
export async function GET(request: NextRequest) {
  try {
    const session = await auth.api.getSession({ headers: request.headers });
    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    const songIds = await getFavoriteSongIds(Number(session.user.id));
    return NextResponse.json({ songIds });
  } catch (error) {
    console.error("Error listing favorites:", error);
    return NextResponse.json({ error: "Failed to list favorites" }, { status: 500 });
  }
}

export async function POST(request: NextRequest) {
  try {
    const session = await auth.api.getSession({ headers: request.headers });
    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const body = await request.json();
    const parsed = addFavoriteSchema.safeParse(body);
    if (!parsed.success) {
      return NextResponse.json(
        { error: "Invalid input", details: parsed.error.issues },
        { status: 400 }
      );
    }

    await addFavorite(Number(session.user.id), parsed.data.songId);
    return NextResponse.json({ success: true }, { status: 201 });
  } catch (error) {
    console.error("Error adding favorite:", error);
    return NextResponse.json({ error: "Failed to add favorite" }, { status: 500 });
  }
}
