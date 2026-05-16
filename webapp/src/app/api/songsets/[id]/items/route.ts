import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import {
  addSongsetItem,
  updateSongsetItem,
  deleteSongsetItem,
} from "@/lib/db/songsets";
import { z } from "zod";

const createSongsetItemSchema = z.object({
  songId: z.string().min(1),
  recordingHashPrefix: z.string().optional(),
  position: z.number().int().min(0),
  gapBeats: z.number().optional(),
  crossfadeEnabled: z.number().int().min(0).max(1).optional(),
  crossfadeDurationSeconds: z.number().optional(),
  keyShiftSemitones: z.number().int().min(-12).max(12).optional(),
  tempoRatio: z.number().positive().optional(),
});

const updateSongsetItemSchema = z.object({
  songId: z.string().min(1).optional(),
  recordingHashPrefix: z.string().optional(),
  position: z.number().int().min(0).optional(),
  gapBeats: z.number().optional(),
  crossfadeEnabled: z.number().int().min(0).max(1).optional(),
  crossfadeDurationSeconds: z.number().optional(),
  keyShiftSemitones: z.number().int().min(-12).max(12).optional(),
  tempoRatio: z.number().positive().optional(),
});

export async function POST(
  request: NextRequest,
  { params }: { params: { id: string } }
) {
  try {
    const session = await auth.api.getSession({
      headers: request.headers,
    });

    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const body = await request.json();
    const parsed = createSongsetItemSchema.safeParse(body);

    if (!parsed.success) {
      return NextResponse.json(
        { error: "Invalid input", details: parsed.error.issues },
        { status: 400 }
      );
    }

    const item = await addSongsetItem(params.id, Number(session.user.id), parsed.data);

    if (!item) {
      return NextResponse.json({ error: "Songset not found" }, { status: 404 });
    }

    return NextResponse.json(item, { status: 201 });
  } catch (error) {
    console.error("Error adding songset item:", error);
    return NextResponse.json(
      { error: "Failed to add songset item" },
      { status: 500 }
    );
  }
}

export async function PATCH(
  request: NextRequest,
  { params }: { params: { id: string } }
) {
  try {
    const session = await auth.api.getSession({
      headers: request.headers,
    });

    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const body = await request.json();
    const parsed = updateSongsetItemSchema.safeParse(body);

    if (!parsed.success) {
      return NextResponse.json(
        { error: "Invalid input", details: parsed.error.issues },
        { status: 400 }
      );
    }

    if (!body.itemId) {
      return NextResponse.json(
        { error: "itemId is required" },
        { status: 400 }
      );
    }

    const item = await updateSongsetItem(
      body.itemId,
      params.id,
      Number(session.user.id),
      parsed.data
    );

    if (!item) {
      return NextResponse.json(
        { error: "Songset item not found" },
        { status: 404 }
      );
    }

    return NextResponse.json(item);
  } catch (error) {
    console.error("Error updating songset item:", error);
    return NextResponse.json(
      { error: "Failed to update songset item" },
      { status: 500 }
    );
  }
}

export async function DELETE(
  request: NextRequest,
  { params }: { params: { id: string } }
) {
  try {
    const session = await auth.api.getSession({
      headers: request.headers,
    });

    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const searchParams = request.nextUrl.searchParams;
    const itemId = searchParams.get("itemId");

    if (!itemId) {
      return NextResponse.json(
        { error: "itemId is required" },
        { status: 400 }
      );
    }

    const deleted = await deleteSongsetItem(itemId, params.id, Number(session.user.id));

    if (!deleted) {
      return NextResponse.json(
        { error: "Songset item not found" },
        { status: 404 }
      );
    }

    return NextResponse.json({ success: true });
  } catch (error) {
    console.error("Error deleting songset item:", error);
    return NextResponse.json(
      { error: "Failed to delete songset item" },
      { status: 500 }
    );
  }
}
