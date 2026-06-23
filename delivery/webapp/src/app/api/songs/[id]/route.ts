import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { getSong } from "@/lib/db/songs";

interface RouteParams {
  params: Promise<{ id: string }>;
}

export async function GET(request: NextRequest, { params }: RouteParams) {
  try {
    const session = await auth.api.getSession({
      headers: request.headers,
    });

    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const { id } = await params;
    const song = await getSong(id);

    if (!song) {
      return NextResponse.json({ error: "Song not found" }, { status: 404 });
    }

    return NextResponse.json(song);
  } catch (error) {
    console.error("Error getting song:", error);
    return NextResponse.json(
      { error: "Failed to get song" },
      { status: 500 }
    );
  }
}
