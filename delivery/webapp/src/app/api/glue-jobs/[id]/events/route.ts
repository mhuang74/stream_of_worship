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
// Vercel counts streaming time toward the 60-second function limit.
const STREAM_BUDGET_MS = 50_000;

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
 * stream closes on a terminal phase or when its request budget expires. The
 * client falls back to discovery (`GET .../glue-jobs?active=1`) whenever the
 * stream cannot open or drops.
 */
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { signal } = request;
  const { promise: stopped, resolve: stop } = Promise.withResolvers<null>();
  let pollTimer: ReturnType<typeof setTimeout> | undefined;
  let streamController: ReadableStreamDefaultController<Uint8Array> | undefined;
  let finished = false;

  function finish() {
    if (finished) return;
    finished = true;
    clearTimeout(deadlineTimer);
    clearTimeout(pollTimer);
    signal.removeEventListener("abort", finish);
    stop(null);
    streamController?.close();
  }

  // Start before authentication and the first read so either can spend the budget.
  const deadlineTimer = setTimeout(finish, STREAM_BUDGET_MS);
  signal.addEventListener("abort", finish);
  if (signal.aborted) finish();

  const prepare = async () => {
    const session = await auth.api.getSession({ headers: request.headers });
    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const { id } = await params;
    if (finished) return null;

    const userId = Number(session.user.id);
    const readSnapshot = () => glueJobStreamSnapshot(id, userId);
    const initial = await readSnapshot();
    if (initial === null) {
      return NextResponse.json(
        { code: "GLUE_NOT_FOUND", message: "AI transition not found.", jobId: id },
        { status: 404 }
      );
    }
    return { initial, readSnapshot };
  };

  const prepared = finished
    ? null
    : await Promise.race([prepare(), stopped]).catch((error) => {
        finish();
        throw error;
      });
  if (finished || prepared === null) {
    // No snapshot was available before disconnect or expiry. EOF lets the
    // existing EventSource watcher run discovery and reconnect.
    return new Response(null, { headers: sseHeaders() });
  }
  if (prepared instanceof Response) {
    finish();
    return prepared;
  }

  const { initial, readSnapshot } = prepared;
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      streamController = controller;

      const emit = (snapshot: GlueJobStatus) => {
        controller.enqueue(
          encoder.encode(`data: ${JSON.stringify(snapshot)}\n\n`)
        );
        if (isGlueJobTerminalPhase(snapshot.phase)) {
          finish();
          return;
        }
        pollTimer = setTimeout(async () => {
          const next = await readSnapshot().catch(() => null);
          if (finished) return;
          if (next === null) {
            // A deleted job (or a persistent read failure) ends the stream; the
            // client reconnects with discovery and shows the durable state.
            finish();
            return;
          }
          emit(next);
        }, POLL_INTERVAL_MS);
      };

      emit(initial);
    },
    cancel() {
      streamController = undefined;
      finish();
    },
  });

  return new Response(stream, { status: 200, headers: sseHeaders() });
}
