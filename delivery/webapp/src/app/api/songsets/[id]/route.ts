import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { getSongsetEditorData, updateSongset, deleteSongset } from "@/lib/db/songsets";
import { z } from "zod";

const updateSongsetSchema = z.object({
  name: z.string().min(1).max(255).optional(),
  description: z.string().max(1000).optional(),
  boundary: z.object({
    target: z.enum(["intro", "outro"]),
    enabled: z.boolean().optional(),
    beats: z.union([z.literal(2), z.literal(4), z.literal(8), z.literal(16)]).optional(),
    takeId: z.string().uuid().optional(),
  }).strict().refine(({ enabled, beats, takeId }) =>
    takeId ? enabled === undefined && beats === undefined : enabled !== undefined || beats !== undefined
  ).optional(),
});

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const session = await auth.api.getSession({
      headers: request.headers,
    });

    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const { id } = await params;
    const songset = await getSongsetEditorData(id, Number(session.user.id));

    if (!songset) {
      return NextResponse.json({ error: "Songset not found" }, { status: 404 });
    }

    // This body carries per-saved-state Glue facts (selected take, junction
    // state, duration). A service worker or the browser HTTP cache answering a
    // later request with the previous write's body showed a take that had just
    // been cleared, so no cache layer may store it.
    return NextResponse.json(songset, {
      headers: { "Cache-Control": "no-store" },
    });
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
  { params }: { params: Promise<{ id: string }> }
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
        { error: "Invalid input", details: parsed.error.issues },
        { status: 400 }
      );
    }

    const { id } = await params;
    if (parsed.data.boundary) {
      const { target, ...patch } = parsed.data.boundary;
      if (process.env.SOW_SONGSET_BOUNDARIES_ENABLED !== "1" && patch.enabled !== false) {
        return NextResponse.json({ code: "GLUE_NOT_CONFIGURED" }, { status: 409 });
      }
      const { updateSongsetBoundary } = await import("@/lib/glue/boundaries");
      const result = await updateSongsetBoundary(id, Number(session.user.id), target, patch);
      if (result === "not-found") return NextResponse.json({ error: "Songset not found" }, { status: 404 });
      if (result === "stale") {
        return NextResponse.json({ code: "GLUE_STALE_SNAPSHOT", error: "The saved boundary changed." }, { status: 409 });
      }
      return NextResponse.json(await getSongsetEditorData(id, Number(session.user.id)), {
        headers: { "Cache-Control": "no-store" },
      });
    }
    const songset = await updateSongset(id, Number(session.user.id), parsed.data);

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
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const session = await auth.api.getSession({
      headers: request.headers,
    });

    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const { id } = await params;
    const deleted = await deleteSongset(id, Number(session.user.id));

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
