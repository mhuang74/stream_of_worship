import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { getRenderJob, cancelRenderJob } from "@/lib/render/job-manager";

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const session = await auth.api.getSession({
      headers: request.headers,
    });

    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const { id } = await params;
    const job = await getRenderJob(id, Number(session.user.id));

    if (!job) {
      return NextResponse.json(
        { error: "Render job not found" },
        { status: 404 }
      );
    }

    return NextResponse.json(job);
  } catch (error) {
    console.error("Error getting render job:", error);
    return NextResponse.json(
      { error: "Failed to get render job" },
      { status: 500 }
    );
  }
}

export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const session = await auth.api.getSession({
      headers: request.headers,
    });

    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const { id } = await params;
    const job = await cancelRenderJob(id, Number(session.user.id));

    if (!job) {
      return NextResponse.json(
        { error: "Render job not found" },
        { status: 404 }
      );
    }

    return NextResponse.json(job);
  } catch (error) {
    console.error("Error cancelling render job:", error);
    
    if (error instanceof Error && error.message.includes("Cannot cancel")) {
      return NextResponse.json(
        { error: error.message },
        { status: 400 }
      );
    }
    
    return NextResponse.json(
      { error: "Failed to cancel render job" },
      { status: 500 }
    );
  }
}
