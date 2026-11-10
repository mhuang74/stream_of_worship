import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import {
  SongsetRenderValidationError,
  updateSongArrangement,
} from "@/lib/db/songsets";
import { MAX_ARRANGEMENT_STEPS } from "@/lib/arrangements";
import { z } from "zod";

// Wire contract (spec §2.2): exactly the persisted fields, 1–64 Steps,
// nonnegative integer section indices (null only for the Songset-local
// LRC-inferred Intro), finite timestamps. `strictObject` rejects extra fields
// so the stored JSON can never drift from the schema. `manualBounds` is
// `z.literal(true)` so false/1/"true" are 400s; the Intro/manual
// cross-field rules stay in validateSongArrangement (already called by the
// update path). Zod counts UTF-16 units, so the label cap counts code points
// explicitly to stay identical to arrangements.ts and the worker's Python
// len().
const arrangementStepSchema = z.strictObject({
  sectionIndex: z.number().int().nonnegative().nullable(),
  label: z
    .string()
    .min(1)
    .refine((label) => [...label].length <= 100, {
      message: "Label must be at most 100 characters",
    }),
  startSeconds: z.number(),
  endSeconds: z.number(),
  manualBounds: z.literal(true).optional(),
});

const songArrangementSchema = z.strictObject({
  version: z.literal(1),
  steps: z.array(arrangementStepSchema).min(1).max(MAX_ARRANGEMENT_STEPS),
});

const putArrangementSchema = z.strictObject({
  arrangement: songArrangementSchema.nullable(),
});

/**
 * PUT /api/songsets/[id]/items/[itemId]/arrangement
 *
 * Save (body `{ arrangement: SongArrangement }`) or reset (`{ arrangement:
 * null }`) one item's arrangement. Responses: 401 unauthenticated; 400
 * malformed schema/JSON and over-limit (`code: "SONGSET_TOO_LONG"`); 404
 * unowned/missing item; 409 `code: "ARRANGEMENT_ANALYSIS_UNAVAILABLE" |
 * "ARRANGEMENT_STALE"`; 200 the updated item in `getSongsetEditorData`'s
 * detail shape. A reset needs no Recording analysis.
 */
export async function PUT(
  request: NextRequest,
  { params }: { params: Promise<{ id: string; itemId: string }> }
) {
  try {
    const session = await auth.api.getSession({
      headers: request.headers,
    });

    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    let body: unknown;
    try {
      body = await request.json();
    } catch {
      return NextResponse.json({ error: "Invalid input" }, { status: 400 });
    }

    const parsed = putArrangementSchema.safeParse(body);

    if (!parsed.success) {
      return NextResponse.json(
        { error: "Invalid input", details: parsed.error.issues },
        { status: 400 }
      );
    }

    const { id, itemId } = await params;
    const item = await updateSongArrangement(
      itemId,
      id,
      Number(session.user.id),
      parsed.data.arrangement
    );

    if (!item) {
      return NextResponse.json(
        { error: "Songset item not found" },
        { status: 404 }
      );
    }

    return NextResponse.json(item);
  } catch (error) {
    if (error instanceof SongsetRenderValidationError) {
      if (error.code === "SONGSET_TOO_LONG") {
        return NextResponse.json(
          { error: error.message, code: error.code },
          { status: 400 }
        );
      }
      if (error.code === "SONGSET_NOT_FOUND") {
        return NextResponse.json({ error: error.message }, { status: 404 });
      }
      return NextResponse.json(
        { error: error.message, code: error.code },
        { status: 409 }
      );
    }

    console.error("Error saving song arrangement:", error);
    return NextResponse.json(
      { error: "Failed to save song arrangement" },
      { status: 500 }
    );
  }
}
