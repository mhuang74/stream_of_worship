import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import {
  glueJobStreamSnapshot,
  isGlueJobTerminalPhase,
  type GlueJobStatus,
} from "@/lib/glue/jobs";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const POLL_INTERVAL_MS = 1_000;

function sseHeaders(): HeadersInit {
  return {
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-cache, no-transform",
    Connection: "keep-alive",
    // Vercel/nginx would otherwise buffer the stream.
    "X-Accel-Buffering": "no",
  };
}

/**
 * One JSON snapshot per second as `data: <json>\n\n` (spec §2.4, §6.3). The
 * stream closes as soon as a terminal phase is observed, and never carries a
 * percentage: the editor shows the real phase plus elapsed seconds. The client
 * falls back to discovery (`GET .../glue-jobs?active=1`) whenever the stream
 * cannot open or drops.
 */
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const session = await auth.api.getSession({ headers: request.headers });
  if (!session?.user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { id } = await params;
  const userId = Number(session.user.id);

  const initial = await glueJobStreamSnapshot(id, userId);
  if (initial === null) {
    return NextResponse.json(
      { code: "GLUE_NOT_FOUND", message: "AI transition not found.", jobId: id },
      { status: 404 }
    );
  }

  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      let finished = false;
      const finish = () => {
        if (finished) return;
        finished = true;
        try {
          controller.close();
        } catch {
          // Already closed by the runtime/abort path.
        }
      };
      const onAbort = () => finish();
      request.signal.addEventListener("abort", onAbort);

      const emit = (snapshot: GlueJobStatus) => {
        controller.enqueue(
          encoder.encode(`data: ${JSON.stringify(snapshot)}\n\n`)
        );
      };

      try {
        let snapshot: GlueJobStatus | null = initial;
        while (!finished) {
          emit(snapshot);
          if (isGlueJobTerminalPhase(snapshot.phase)) {
            finish();
            break;
          }
          const { promise, resolve } = Promise.withResolvers<void>();
          setTimeout(resolve, POLL_INTERVAL_MS);
          await promise;
          if (finished) break;
          const next = await glueJobStreamSnapshot(id, userId).catch(() => null);
          if (next === null) {
            // A deleted job (or a persistent read failure) ends the stream; the
            // client reconnects with discovery and shows the durable state.
            finish();
            break;
          }
          snapshot = next;
        }
      } finally {
        request.signal.removeEventListener("abort", onAbort);
        finish();
      }
    },
    cancel() {
      // The consumer went away; the loop's next iteration observes the aborted
      // signal and stops.
    },
  });

  return new Response(stream, { status: 200, headers: sseHeaders() });
}
