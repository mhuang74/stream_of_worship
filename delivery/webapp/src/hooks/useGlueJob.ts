"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  fetchGlueJob,
  isGlueTerminalPhase,
  watchGlueJob,
  type GlueApiError,
  type GlueJobStatus,
  type GlueQuota,
  type GlueRetryKind,
} from "@/lib/glue/client";

// One AI-transition job, observed through the SSE stream with discovery
// fallback (spec §2.4). The hook exposes the server's real phase and elapsed
// seconds; it never derives or fabricates a percentage.

export interface UseGlueJobOptions {
  /** `null` disables watching (no job in flight for this Junction). */
  jobId: string | null;
  songsetId: string;
  fromItemId: string | null;
  enabled?: boolean;
}

export interface UseGlueJobResult {
  status: GlueJobStatus | null;
  phase: string | null;
  active: boolean;
  elapsedSeconds: number;
  terminal: boolean;
  providerContacted: boolean;
  retryKind: GlueRetryKind | null;
  errorCode: string | null;
  errorDetail: string | null;
  quota: GlueQuota | null;
  streamError: GlueApiError | null;
  refresh: () => Promise<void>;
  /** Reopens the stream for the current job (after an in-place retry). */
  restart: () => void;
}

export function useGlueJob({
  jobId,
  songsetId,
  fromItemId,
  enabled = true,
}: UseGlueJobOptions): UseGlueJobResult {
  const [status, setStatus] = useState<GlueJobStatus | null>(null);
  const [streamError, setStreamError] = useState<GlueApiError | null>(null);
  const [watchEpoch, setWatchEpoch] = useState(0);
  // Which job the current stream belongs to; a restart of the same id must not
  // blank the phase the rail is already showing.
  const subscribedJobIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (!enabled || jobId === null || fromItemId === null) return;

    let disposed = false;
    // A different job starts from a clean slate; the previous job's terminal
    // status must not leak into the next Junction's UI. The reset cannot be
    // render-derived: the stale status is exactly what must not render.
    const jobChanged = subscribedJobIdRef.current !== jobId;
    subscribedJobIdRef.current = jobId;
    if (jobChanged) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- clears the previous junction's job status before the new subscription starts
      setStatus(null);
      setStreamError(null);
    }
    const stop = watchGlueJob(
      { jobId, songsetId, fromItemId },
      {
        onStatus: (next) => {
          if (disposed) return;
          setStatus(next);
        },
        onError: (error) => {
          if (disposed) return;
          setStreamError(error);
        },
      }
    );

    return () => {
      disposed = true;
      stop();
    };
  }, [enabled, jobId, songsetId, fromItemId, watchEpoch]);

  const restart = useCallback(() => setWatchEpoch((value) => value + 1), []);

  const refresh = useCallback(async () => {
    if (jobId === null) return;
    const result = await fetchGlueJob(jobId);
    if (result.ok) {
      setStatus(result.data);
      setStreamError(null);
    } else {
      setStreamError(result.error);
    }
  }, [jobId]);

  const phase = status?.phase ?? null;
  return {
    status,
    phase,
    active: status?.active ?? false,
    elapsedSeconds: status?.elapsedSeconds ?? 0,
    terminal: phase !== null && isGlueTerminalPhase(phase),
    providerContacted: status?.providerContacted ?? false,
    retryKind: status?.retryKind ?? null,
    errorCode: status?.errorCode ?? null,
    errorDetail: status?.errorDetail ?? null,
    quota: status?.quota ?? null,
    streamError,
    refresh,
    restart,
  };
}
