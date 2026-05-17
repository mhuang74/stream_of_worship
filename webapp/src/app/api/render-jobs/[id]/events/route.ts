import { NextRequest } from "next/server";
import { auth } from "@/lib/auth";
import { getRenderJob, RenderPhase } from "@/lib/render/job-manager";

export interface SSEEvent {
  phase: RenderPhase;
  phaseIndex: number;
  totalPhases: number;
  percentComplete: number;
  estimatedSecondsLeft: number;
  elapsedSeconds: number;
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const session = await auth.api.getSession({
      headers: request.headers,
    });

    if (!session?.user) {
      return new Response(
        JSON.stringify({ error: "Unauthorized" }),
        {
          status: 401,
          headers: { "Content-Type": "application/json" },
        }
      );
    }

    const { id } = await params;
    const job = await getRenderJob(id, Number(session.user.id));

    if (!job) {
      return new Response(
        JSON.stringify({ error: "Render job not found" }),
        {
          status: 404,
          headers: { "Content-Type": "application/json" },
        }
      );
    }

    // Check if job is in a terminal state
    if (job.status === "completed" || job.status === "failed" || job.status === "cancelled") {
      return new Response(
        JSON.stringify({ error: "Job is no longer active" }),
        {
          status: 410,
          headers: { "Content-Type": "application/json" },
        }
      );
    }

    // Create SSE stream
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        // Send initial event with current state
        const initialEvent: SSEEvent = {
          phase: job.phase ?? "preparing",
          phaseIndex: job.phaseIndex ?? 0,
          totalPhases: job.totalPhases ?? 5,
          percentComplete: job.percentComplete ?? 0,
          estimatedSecondsLeft: job.estimatedSecondsLeft ?? 0,
          elapsedSeconds: job.elapsedSeconds ?? 0,
        };

        controller.enqueue(
          encoder.encode(`data: ${JSON.stringify(initialEvent)}\n\n`)
        );

        // Set up polling interval to check for updates
        const MAX_DURATION_MS = 30 * 60 * 1000;
        const startTime = Date.now();

        const intervalId = setInterval(async () => {
          try {
            if (Date.now() - startTime > MAX_DURATION_MS) {
              controller.enqueue(
                encoder.encode(`data: ${JSON.stringify({ error: "Connection timed out" })}\n\n`)
              );
              controller.close();
              clearInterval(intervalId);
              return;
            }

            const updatedJob = await getRenderJob(id, Number(session.user.id));

            if (!updatedJob) {
              // Job was deleted
              controller.enqueue(
                encoder.encode(`data: ${JSON.stringify({ error: "Job not found" })}\n\n`)
              );
              controller.close();
              clearInterval(intervalId);
              return;
            }

            // Check if job reached terminal state
            if (
              updatedJob.status === "completed" ||
              updatedJob.status === "failed" ||
              updatedJob.status === "cancelled"
            ) {
              const finalEvent: SSEEvent = {
                phase: updatedJob.phase ?? "completed",
                phaseIndex: updatedJob.phaseIndex ?? updatedJob.totalPhases ?? 5,
                totalPhases: updatedJob.totalPhases ?? 5,
                percentComplete: updatedJob.status === "completed" ? 100 : updatedJob.percentComplete,
                estimatedSecondsLeft: 0,
                elapsedSeconds: updatedJob.elapsedSeconds ?? 0,
              };

              controller.enqueue(
                encoder.encode(`data: ${JSON.stringify(finalEvent)}\n\n`)
              );
              controller.close();
              clearInterval(intervalId);
              return;
            }

            // Send progress update
            const event: SSEEvent = {
              phase: updatedJob.phase ?? "preparing",
              phaseIndex: updatedJob.phaseIndex ?? 0,
              totalPhases: updatedJob.totalPhases ?? 5,
              percentComplete: updatedJob.percentComplete ?? 0,
              estimatedSecondsLeft: updatedJob.estimatedSecondsLeft ?? 0,
              elapsedSeconds: updatedJob.elapsedSeconds ?? 0,
            };

            controller.enqueue(
              encoder.encode(`data: ${JSON.stringify(event)}\n\n`)
            );
          } catch (error) {
            console.error("Error polling job status:", error);
            clearInterval(intervalId);
            controller.close();
          }
        }, 1000); // Poll every second

        // Clean up on client disconnect
        request.signal.addEventListener("abort", () => {
          clearInterval(intervalId);
          controller.close();
        });
      },
    });

    return new Response(stream, {
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        Connection: "keep-alive",
      },
    });
  } catch (error) {
    console.error("Error setting up SSE stream:", error);
    return new Response(
      JSON.stringify({ error: "Failed to set up SSE stream" }),
      {
        status: 500,
        headers: { "Content-Type": "application/json" },
      }
    );
  }
}
