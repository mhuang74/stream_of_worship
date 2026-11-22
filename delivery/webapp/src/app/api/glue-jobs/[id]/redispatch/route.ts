import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { describeGlueError, redispatchGlueJob } from "@/lib/glue/jobs";

export const dynamic = "force-dynamic";

/**
 * Free redispatch of a `dispatch-failed` job: the SAME job, take identity, and
 * reservation (released → reserved without a new ledger row). Never a new job,
 * never a charge.
 */
export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const session = await auth.api.getSession({ headers: request.headers });
    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const { id } = await params;
    await redispatchGlueJob({ jobId: id, userId: Number(session.user.id) });
    return NextResponse.json(
      { jobId: id, status: "queued", charged: false },
      { status: 202 }
    );
  } catch (error) {
    const { status, body } = describeGlueError(error);
    return NextResponse.json(body, { status });
  }
}
