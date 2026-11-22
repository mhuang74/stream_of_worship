import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { describeGlueError, resumeGlueJob } from "@/lib/glue/jobs";

export const dynamic = "force-dynamic";

/**
 * Free resume of a pre-contact failure, or of a persisted successful upload
 * whose compose was definitely not sent. Ambiguous provider states are refused;
 * the client must make an explicit paid `Try another` request instead.
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
    const status = await resumeGlueJob({ jobId: id, userId: Number(session.user.id) });
    return NextResponse.json(
      { jobId: id, status: status.phase, charged: false },
      { status: 202 }
    );
  } catch (error) {
    const { status, body } = describeGlueError(error);
    return NextResponse.json(body, { status });
  }
}
