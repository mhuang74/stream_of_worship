import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { removeFavorite } from "@/lib/db/favorites";

/**
 * DELETE /api/favorites/:songId — removes the song from the current user's
 * favorites (idempotent).
 */
export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ songId: string }> }
) {
  try {
    const session = await auth.api.getSession({ headers: request.headers });
    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const { songId } = await params;
    await removeFavorite(Number(session.user.id), songId);
    return NextResponse.json({ success: true });
  } catch (error) {
    console.error("Error removing favorite:", error);
    return NextResponse.json({ error: "Failed to remove favorite" }, { status: 500 });
  }
}
