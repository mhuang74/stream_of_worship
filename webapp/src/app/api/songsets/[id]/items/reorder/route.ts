import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { db } from "@/db";
import { songsetItems, songsets } from "@/db/schema";
import { and, eq } from "drizzle-orm";
import { z } from "zod";

const reorderSchema = z.object({
  updates: z.array(
    z.object({
      itemId: z.string().min(1),
      position: z.number().int().min(0),
    })
  ),
});

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;
    const session = await auth.api.getSession({
      headers: request.headers,
    });

    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const body = await request.json();
    const parsed = reorderSchema.safeParse(body);

    if (!parsed.success) {
      return NextResponse.json(
        { error: "Invalid input", details: parsed.error.issues },
        { status: 400 }
      );
    }

    const { updates } = parsed.data;
    const userId = Number(session.user.id);

    const songset = await db.query.songsets.findFirst({
      where: and(eq(songsets.id, id), eq(songsets.userId, userId)),
    });

    if (!songset) {
      return NextResponse.json(
        { error: "Songset not found" },
        { status: 404 }
      );
    }

    const now = new Date();

    for (const update of updates) {
      const item = await db.query.songsetItems.findFirst({
        where: eq(songsetItems.id, update.itemId),
        with: {
          songset: true,
        },
      });

      if (!item || item.songsetId !== id || item.songset.userId !== userId) {
        return NextResponse.json(
          { error: `Item ${update.itemId} not found or access denied` },
          { status: 403 }
        );
      }

      await db
        .update(songsetItems)
        .set({ position: update.position })
        .where(eq(songsetItems.id, update.itemId));
    }

    await db
      .update(songsets)
      .set({ updatedAt: now })
      .where(eq(songsets.id, id));

    return NextResponse.json({ success: true });
  } catch (error) {
    console.error("Error reordering songset items:", error);
    return NextResponse.json(
      { error: "Failed to reorder items" },
      { status: 500 }
    );
  }
}
