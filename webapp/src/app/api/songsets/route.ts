import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { listSongsets, createSongset } from "@/lib/db/songsets";
import { z } from "zod";

const createSongsetSchema = z.object({
  name: z.string().min(1).max(255),
  description: z.string().max(1000).optional(),
});

export async function GET(request: NextRequest) {
  try {
    const session = await auth.api.getSession({
      headers: request.headers,
    });

    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const searchParams = request.nextUrl.searchParams;
    const limit = Math.min(
      parseInt(searchParams.get("limit") ?? "50"),
      100
    );
    const offset = parseInt(searchParams.get("offset") ?? "0");

    const result = await listSongsets(Number(session.user.id), limit, offset);

    return NextResponse.json(result);
  } catch (error) {
    console.error("Error listing songsets:", error);
    return NextResponse.json(
      { error: "Failed to list songsets" },
      { status: 500 }
    );
  }
}

export async function POST(request: NextRequest) {
  try {
    const session = await auth.api.getSession({
      headers: request.headers,
    });

    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const body = await request.json();
    const parsed = createSongsetSchema.safeParse(body);

    if (!parsed.success) {
      return NextResponse.json(
        { error: "Invalid input", details: parsed.error.issues },
        { status: 400 }
      );
    }

    const songset = await createSongset(Number(session.user.id), parsed.data);

    return NextResponse.json(songset, { status: 201 });
  } catch (error) {
    console.error("Error creating songset:", error);
    return NextResponse.json(
      { error: "Failed to create songset" },
      { status: 500 }
    );
  }
}
