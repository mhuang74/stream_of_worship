import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { createRenderJob, failRenderJob } from "@/lib/render/job-manager";
import { dispatchToRenderWorker } from "@/lib/render/dispatcher";
import { z } from "zod";

const createRenderJobSchema = z.object({
  songsetId: z.string().min(1),
  template: z.enum(["dark", "gradient_warm", "gradient_blue"]).optional(),
  resolution: z.enum(["720p", "1080p"]).optional(),
  audioEnabled: z.boolean().optional(),
  videoEnabled: z.boolean().optional(),
  fontSizePreset: z.enum(["S", "M", "L", "XL"]).optional(),
  includeTitleCard: z.boolean().optional(),
  titleCardDurationSeconds: z.number().min(5).max(30).optional(),
});

export async function POST(request: NextRequest) {
  try {
    const session = await auth.api.getSession({
      headers: request.headers,
    });

    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const body = await request.json();
    const parsed = createRenderJobSchema.safeParse(body);

    if (!parsed.success) {
      return NextResponse.json(
        { error: "Invalid input", details: parsed.error.issues },
        { status: 400 }
      );
    }

    const job = await createRenderJob(Number(session.user.id), parsed.data);

    try {
      await dispatchToRenderWorker({
        jobId: job.id,
        songsetId: job.songsetId,
        userId: Number(session.user.id),
      });
    } catch (dispatchError) {
      console.error("Failed to dispatch render job to worker:", dispatchError);
      try {
        await failRenderJob(job.id, Number(session.user.id), "Failed to dispatch render job to worker");
      } catch (cleanupError) {
        console.error("Failed to mark orphaned job as failed:", cleanupError);
      }
      return NextResponse.json(
        { error: "Failed to dispatch render job" },
        { status: 500 }
      );
    }

    return NextResponse.json(job, { status: 201 });
  } catch (error) {
    console.error("Error creating render job:", error);
    
    if (error instanceof Error && error.message.includes("Songset not found")) {
      return NextResponse.json(
        { error: "Songset not found or access denied" },
        { status: 404 }
      );
    }
    
    return NextResponse.json(
      { error: "Failed to create render job" },
      { status: 500 }
    );
  }
}
