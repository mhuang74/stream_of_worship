import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { cancelGlueJob, describeGlueError } from "@/lib/glue/jobs";

export const dynamic = "force-dynamic";

/**
 * Cancel before provider contact. After `provider_contacted=true` the atomic
 * conditional update matches nothing and the route answers
 * `409 GLUE_PAID_NOT_CANCELLABLE` without changing state.
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
    const result = await cancelGlueJob({ jobId: id, userId: Number(session.user.id) });
    return NextResponse.json(result, { status: 200 });
  } catch (error) {
    const { status, body } = describeGlueError(error);
    return NextResponse.json(body, { status });
  }
}
