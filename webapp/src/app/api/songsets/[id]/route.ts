import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { getSongset, updateSongset, deleteSongset } from "@/lib/db/songsets";
import { z } from "zod";

const updateSongsetSchema = z.object({
  name: z.string().min(1).max(255).optional(),
  description: z.string().max(1000).optional(),
});

export async function GET(
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

    const songset = await getSongset(params.id, session.user.id);

    if (!songset) {
      return NextResponse.json({ error: "Songset not found" }, { status: 404 });
    }

    return NextResponse.json(songset);
  } catch (error) {
    console.error("Error getting songset:", error);
    return NextResponse.json(
      { error: "Failed to get songset" },
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
    const parsed = updateSongsetSchema.safeParse(body);

    if (!parsed.success) {
      return NextResponse.json(
        { error: "Invalid input", details: parsed.error.errors },
        { status: 400 }
      );
    }

    const songset = await updateSongset(params.id, session.user.id, parsed.data);

    if (!songset) {
      return NextResponse.json({ error: "Songset not found" }, { status: 404 });
    }

    return NextResponse.json(songset);
  } catch (error) {
    console.error("Error updating songset:", error);
    return NextResponse.json(
      { error: "Failed to update songset" },
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

    const deleted = await deleteSongset(params.id, session.user.id);

    if (!deleted) {
      return NextResponse.json({ error: "Songset not found" }, { status: 404 });
    }

    return NextResponse.json({ success: true });
  } catch (error) {
    console.error("Error deleting songset:", error);
    return NextResponse.json(
      { error: "Failed to delete songset" },
      { status: 500 }
    );
  }
}
