import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";

import { auth } from "@/lib/auth";
import { getGlueQuote, GlueQuoteError } from "@/lib/glue/quote";

// Credits and quota are per-saved-state facts for the same URL (a beat change
// keeps `fromItemId`), so no cache layer may answer a later request with the
// previous beat count's numbers.
export const dynamic = "force-dynamic";

const querySchema = z.object({
  target: z.enum(["junction", "intro", "outro"]).optional(),
  fromItemId: z.string().min(1).optional(),
  takeMode: z.enum(["initial", "new-take"]).default("initial"),
}).refine((query) => query.target === "intro" || query.target === "outro" || query.fromItemId);

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const session = await auth.api.getSession({ headers: request.headers });
    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const parsed = querySchema.safeParse({
      fromItemId: request.nextUrl.searchParams.get("fromItemId") ?? undefined,
      target: request.nextUrl.searchParams.get("target") ?? undefined,
      takeMode: request.nextUrl.searchParams.get("takeMode") ?? undefined,
    });
    if (!parsed.success) {
      return NextResponse.json(
        { code: "GLUE_INVALID_REQUEST", message: "Invalid quote request", details: parsed.error.issues },
        { status: 400 }
      );
    }

    const { id } = await params;
    if ((parsed.data.target === "intro" || parsed.data.target === "outro") &&
        process.env.SOW_SONGSET_BOUNDARIES_ENABLED !== "1") {
      return NextResponse.json({ code: "GLUE_NOT_CONFIGURED" }, { status: 409 });
    }
    const quote = await getGlueQuote({
      songsetId: id,
      userId: Number(session.user.id),
      fromItemId: parsed.data.fromItemId,
      ...(parsed.data.target ? { target: parsed.data.target } : {}),
      takeMode: parsed.data.takeMode,
    });
    if (quote === null) {
      return NextResponse.json(
        { error: "Songset or item not found" },
        { status: 404 }
      );
    }

    return NextResponse.json(quote);
  } catch (error) {
    if (error instanceof GlueQuoteError) {
      return NextResponse.json({ code: error.code, message: error.message }, { status: error.status });
    }
    console.error("Error quoting AI transition:", error);
    return NextResponse.json(
      { code: "GLUE_QUOTE_FAILED", message: "Failed to quote AI transition" },
      { status: 500 }
    );
  }
}
