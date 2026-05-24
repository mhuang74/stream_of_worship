// NOTE: This SSE route has maxDuration: 60 on Vercel (serverless function timeout).
// For renders that take longer than ~60s, the SSE connection will drop.
// The client (RenderProgress.tsx) uses REST polling as the primary transport
// and treats SSE as an optional enhancement. Do NOT rely on SSE for critical
// progress updates on Vercel deployments.

import { NextRequest } from "next/server";
import { auth } from "@/lib/auth";
import { getRenderJob, failRenderJob, RenderPhase } from "@/lib/render/job-manager";

const STALE_JOB_THRESHOLD_MINUTES = 15;

export interface SSEEvent {
  phase: RenderPhase;
  phaseIndex: number;
  totalPhases: number;
  estimatedTotalSeconds: number;
  elapsedSeconds: number;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  errorMessage?: string;
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
        let closed = false;

        const safeEnqueue = (data: Uint8Array) => {
          if (closed) return;
          try {
            controller.enqueue(data);
          } catch {
            closed = true;
          }
        };

        const safeClose = () => {
          if (closed) return;
          closed = true;
          try {
            controller.close();
          } catch {
            // already closed
          }
        };

        // Send initial event with current state
        const initialEvent: SSEEvent = {
          phase: job.phase ?? "preparing",
          phaseIndex: job.phaseIndex ?? 0,
          totalPhases: job.totalPhases ?? 5,
          estimatedTotalSeconds: job.estimatedTotalSeconds ?? 0,
          elapsedSeconds: job.startedAt
            ? (Date.now() - job.startedAt.getTime()) / 1000
            : 0,
          status: job.status,
        };

        safeEnqueue(
          encoder.encode(`data: ${JSON.stringify(initialEvent)}\n\n`)
        );

        // Set up polling to check for updates
        const MAX_DURATION_MS = 30 * 60 * 1000;
        const startTime = Date.now();
        let polling = true;

        async function poll() {
          if (!polling || closed) return;
          if (Date.now() - startTime > MAX_DURATION_MS) {
            safeEnqueue(
              encoder.encode(`data: ${JSON.stringify({ error: "Connection timed out" })}\n\n`)
            );
            safeClose();
            return;
          }

          const updatedJob = await getRenderJob(id, Number(session!.user.id));

          if (!polling || closed) return;

          if (!updatedJob) {
            safeEnqueue(
              encoder.encode(`data: ${JSON.stringify({ error: "Job not found" })}\n\n`)
            );
            safeClose();
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
              estimatedTotalSeconds:
                updatedJob.status === "completed"
                  ? updatedJob.startedAt
                    ? (Date.now() - updatedJob.startedAt.getTime()) / 1000
                    : 0
                  : updatedJob.estimatedTotalSeconds ?? 0,
              elapsedSeconds: updatedJob.startedAt
                ? (Date.now() - updatedJob.startedAt.getTime()) / 1000
                : 0,
              status: updatedJob.status,
              ...(updatedJob.status === "failed" && updatedJob.errorMessage
                ? { errorMessage: updatedJob.errorMessage }
                : {}),
            };

            safeEnqueue(
              encoder.encode(`data: ${JSON.stringify(finalEvent)}\n\n`)
            );
            safeClose();
            return;
          }

          // Check for stale running job
          if (updatedJob.status === "running" && updatedJob.updatedAt) {
            const staleMinutes = (Date.now() - updatedJob.updatedAt.getTime()) / 60000;
            if (staleMinutes > STALE_JOB_THRESHOLD_MINUTES) {
              await failRenderJob(
                id,
                Number(session!.user.id),
                `Job timed out (no progress for ${Math.round(staleMinutes)} minutes)`
              );
              const failedJob = await getRenderJob(id, Number(session!.user.id));
              if (failedJob) {
                const finalEvent: SSEEvent = {
                  phase: failedJob.phase ?? "preparing",
                  phaseIndex: failedJob.phaseIndex ?? 0,
                  totalPhases: failedJob.totalPhases ?? 5,
                  estimatedTotalSeconds: failedJob.estimatedTotalSeconds ?? 0,
                  elapsedSeconds: failedJob.startedAt
                    ? (Date.now() - failedJob.startedAt.getTime()) / 1000
                    : 0,
                  status: "failed",
                  errorMessage: failedJob.errorMessage ?? "Job timed out",
                };
                safeEnqueue(encoder.encode(`data: ${JSON.stringify(finalEvent)}\n\n`));
                safeClose();
              }
              return;
            }
          }

          // Send progress update
          const event: SSEEvent = {
            phase: updatedJob.phase ?? "preparing",
            phaseIndex: updatedJob.phaseIndex ?? 0,
            totalPhases: updatedJob.totalPhases ?? 5,
            estimatedTotalSeconds: updatedJob.estimatedTotalSeconds ?? 0,
            elapsedSeconds: updatedJob.startedAt
              ? (Date.now() - updatedJob.startedAt.getTime()) / 1000
              : 0,
            status: updatedJob.status,
          };

          safeEnqueue(
            encoder.encode(`data: ${JSON.stringify(event)}\n\n`)
          );

          // Schedule next poll after current one completes
          if (polling && !closed) {
            setTimeout(poll, 1000);
          }
        }

        // Clean up on client disconnect
        request.signal.addEventListener("abort", () => {
          polling = false;
          safeClose();
        });

        // Start the first poll
        poll();
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
