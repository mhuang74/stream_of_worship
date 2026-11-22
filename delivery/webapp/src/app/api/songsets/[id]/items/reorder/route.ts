import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { reorderSongsetItems } from "@/lib/db/songsets";
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
    const result = await reorderSongsetItems(id, Number(session.user.id), updates);

    if (result === "songset-not-found") {
      return NextResponse.json(
        { error: "Songset not found" },
        { status: 404 }
      );
    }

    if (result === "item-not-found") {
      return NextResponse.json(
        { error: "One or more items not found or access denied" },
        { status: 403 }
      );
    }

    return NextResponse.json({ success: true });
  } catch (error) {
    console.error("Error reordering songset items:", error);
    return NextResponse.json(
      { error: "Failed to reorder items" },
      { status: 500 }
    );
  }
}
