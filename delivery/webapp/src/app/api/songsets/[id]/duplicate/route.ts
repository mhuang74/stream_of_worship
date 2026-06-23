import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { duplicateSongset } from "@/lib/db/songsets";

export async function POST(
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
    const body = await request.json();
    const name = body.name as string;
    const description = body.description as string | null;

    const duplicated = await duplicateSongset(id, Number(session.user.id), name, description);

    if (!duplicated) {
      return NextResponse.json({ error: "Songset not found" }, { status: 404 });
    }

    return NextResponse.json(duplicated, { status: 201 });
  } catch (error) {
    console.error("Error duplicating songset:", error);
    return NextResponse.json(
      { error: "Failed to duplicate songset" },
      { status: 500 }
    );
  }
}
