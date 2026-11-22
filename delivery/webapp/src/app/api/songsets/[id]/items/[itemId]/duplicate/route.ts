import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { SongsetRenderValidationError, duplicateSongsetItem } from "@/lib/db/songsets";

/**
 * POST /api/songsets/[id]/items/[itemId]/duplicate
 *
 * Insert a copy of one Songset Item immediately below itself in the same
 * Songset, carrying the Song, Recording, Transition settings, and Song
 * Arrangement verbatim. A Song may therefore occupy several Items. Responses:
 * 401 unauthenticated; 404 unowned/missing item; 409
 * `code: "SONGSET_FULL" | "SONGSET_TOO_LONG"`; 201 the created item in
 * `getSongsetEditorData`'s detail shape.
 */
export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string; itemId: string }> }
) {
  try {
    const session = await auth.api.getSession({
      headers: request.headers,
    });

    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const { id, itemId } = await params;
    const item = await duplicateSongsetItem(itemId, id, Number(session.user.id));

    if (!item) {
      return NextResponse.json({ error: "Item not found" }, { status: 404 });
    }

    return NextResponse.json(item, { status: 201 });
  } catch (error) {
    if (error instanceof SongsetRenderValidationError) {
      return NextResponse.json(
        { error: error.message, code: error.code },
        { status: 409 }
      );
    }

    console.error("Error duplicating songset item:", error);
    return NextResponse.json({ error: "Failed to duplicate item" }, { status: 500 });
  }
}
