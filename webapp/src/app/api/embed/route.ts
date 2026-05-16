// POST /api/embed — generate a 1024-dim bge-m3 embedding for a query text
// Uses Node.js runtime (fastembed requires native ONNX bindings)
import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { generateEmbedding, EMBEDDING_MODEL_VERSION, EMBEDDING_DIMENSIONS } from "@/lib/embed/client";
import { z } from "zod";

export const runtime = "nodejs";

const RequestSchema = z.object({
  text: z.string().min(1, "text must not be empty").max(8192, "text too long"),
});

export async function POST(request: NextRequest) {
  try {
    const session = await auth.api.getSession({ headers: request.headers });
    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    let body: unknown;
    try {
      body = await request.json();
    } catch {
      return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
    }

    const parsed = RequestSchema.safeParse(body);
    if (!parsed.success) {
      return NextResponse.json(
        { error: parsed.error.issues[0]?.message ?? "Invalid request" },
        { status: 400 }
      );
    }

    const embedding = await generateEmbedding(parsed.data.text);

    if (embedding.length !== EMBEDDING_DIMENSIONS) {
      return NextResponse.json(
        { error: `Expected ${EMBEDDING_DIMENSIONS}-dim vector, got ${embedding.length}` },
        { status: 500 }
      );
    }

    return NextResponse.json({
      embedding,
      dimensions: embedding.length,
      model: EMBEDDING_MODEL_VERSION,
    });
  } catch (error) {
    console.error("Error generating embedding:", error);
    const message = error instanceof Error ? error.message : "Failed to generate embedding";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
