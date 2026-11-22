import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { describeGlueError, retryGlueProcessing } from "@/lib/glue/jobs";

export const dynamic = "force-dynamic";

/**
 * Free staging recovery from a verified raw provider response: no upload, no
 * compose, no quota, no new take identity. Refused unless the durable
 * raw-response metadata exists.
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
    const status = await retryGlueProcessing({
      jobId: id,
      userId: Number(session.user.id),
    });
    if (status.phase === "ready") {
      return NextResponse.json(
        { jobId: id, status: "ready", charged: false },
        { status: 200 }
      );
    }
    return NextResponse.json(
      { jobId: id, status: "staging", charged: false },
      { status: 202 }
    );
  } catch (error) {
    const { status, body } = describeGlueError(error);
    return NextResponse.json(body, { status });
  }
}
