import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";

import { auth } from "@/lib/auth";
import {
  createGlueJob,
  describeGlueError,
  listGlueJobsForSongset,
} from "@/lib/glue/jobs";

export const dynamic = "force-dynamic";

// Create or replay one durable AI-transition request (spec §2.3, §2.4). The
// body carries only the client's intent and snapshot; descriptors, WAVs, seeds,
// credits, and cache keys are always recomputed server-side, so any extra field
// is rejected.
const createGlueJobSchema = z
  .object({
    fromItemId: z.string().min(1),
    clientRequestId: z.string().uuid(),
    expectedFromUpdatedAt: z.string().datetime({ offset: true }),
    expectedToUpdatedAt: z.string().datetime({ offset: true }),
    takeMode: z.enum(["initial", "new-take"]),
  })
  .strict();

function unauthorized(): NextResponse {
  return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
}

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const session = await auth.api.getSession({ headers: request.headers });
    if (!session?.user) return unauthorized();

    const { id } = await params;
    const parsed = createGlueJobSchema.safeParse(await request.json());
    if (!parsed.success) {
      return NextResponse.json(
        {
          code: "GLUE_INVALID_REQUEST",
          message: "Invalid AI transition request.",
          detail: parsed.error.issues.map((issue) => issue.path.join(".")).join(","),
        },
        { status: 400 }
      );
    }

    const outcome = await createGlueJob({
      songsetId: id,
      userId: Number(session.user.id),
      ...parsed.data,
    });

    if (outcome.kind === "ready") {
      return NextResponse.json(
        { status: "ready", takeId: outcome.takeId, cached: true },
        { status: 200 }
      );
    }
    return NextResponse.json(
      { jobId: outcome.job.id, status: outcome.job.phase },
      { status: 202 }
    );
  } catch (error) {
    const { status, body } = describeGlueError(error);
    return NextResponse.json(body, { status });
  }
}

/** Discovery after a reload, reconnect, or visibility resume. */
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const session = await auth.api.getSession({ headers: request.headers });
    if (!session?.user) return unauthorized();

    if (request.nextUrl.searchParams.get("active") !== "1") {
      return NextResponse.json(
        {
          code: "GLUE_INVALID_REQUEST",
          message: "Only active job discovery is supported.",
        },
        { status: 400 }
      );
    }

    const { id } = await params;
    const payload = await listGlueJobsForSongset(id, Number(session.user.id));
    return NextResponse.json(payload);
  } catch (error) {
    const { status, body } = describeGlueError(error);
    return NextResponse.json(body, { status });
  }
}
