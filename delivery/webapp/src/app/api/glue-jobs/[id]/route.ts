import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { describeGlueError, getGlueJobStatus } from "@/lib/glue/jobs";

export const dynamic = "force-dynamic";

/** One job's durable status (no percentage field). */
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const session = await auth.api.getSession({ headers: request.headers });
    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const { id } = await params;
    const status = await getGlueJobStatus(id, Number(session.user.id));
    return NextResponse.json(status);
  } catch (error) {
    const { status, body } = describeGlueError(error);
    return NextResponse.json(body, { status });
  }
}
