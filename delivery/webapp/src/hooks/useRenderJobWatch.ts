"use client";

import { useEffect, useRef } from "react";

/** How often the songset editor re-reads its in-flight render job. */
export const RENDER_JOB_POLL_INTERVAL_MS = 10_000;

/** The slice of GET /api/render-jobs/[id] the watcher consumes. */
export interface RenderJobSnapshot {
  id: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  errorMessage: string | null;
  mp3R2Key: string | null;
  mp4R2Key: string | null;
  chaptersR2Key: string | null;
  estimatedTotalSeconds: number | null;
}

interface UseRenderJobWatchOptions {
  /** The job to watch; `null` disables the poll. */
  jobId: string | null;
  /** False when nothing is in flight (the local state already says so). */
  enabled: boolean;
  onCompleted: (job: RenderJobSnapshot) => void;
  onFailed: (job: RenderJobSnapshot) => void;
  onCancelled: () => void;
  /**
   * The poll answered 401/403: the session is permanently expired and the poll
   * can never succeed. Distinct from onFailed — the job did not fail.
   */
  onAuthExpired: () => void;
}

/**
 * Polls one render job until it reaches a terminal status.
 *
 * The render worker writes the database directly, so completion is only
 * observable by asking. Polls once immediately (so a page opened right after
 * submit does not sit blank for an interval), then every
 * RENDER_JOB_POLL_INTERVAL_MS. Every terminal outcome stops the poll: a job
 * that already failed will not succeed on the next tick, and a 401 can never
 * start working.
 */
export function useRenderJobWatch({
  jobId,
  enabled,
  onCompleted,
  onFailed,
  onCancelled,
  onAuthExpired,
}: UseRenderJobWatchOptions): void {
  // Latest-callback refs: the interval is keyed on the job alone, so a parent
  // re-render (a toast, a local state patch) never restarts the poll.
  const onCompletedRef = useRef(onCompleted);
  const onFailedRef = useRef(onFailed);
  const onCancelledRef = useRef(onCancelled);
  const onAuthExpiredRef = useRef(onAuthExpired);
  useEffect(() => {
    onCompletedRef.current = onCompleted;
    onFailedRef.current = onFailed;
    onCancelledRef.current = onCancelled;
    onAuthExpiredRef.current = onAuthExpired;
  });

  useEffect(() => {
    if (!enabled || !jobId) return;

    let timer: number | undefined;
    let disposed = false;

    const stopPolling = () => {
      if (timer !== undefined) {
        window.clearInterval(timer);
        timer = undefined;
      }
    };

    const poll = async () => {
      try {
        const response = await fetch(`/api/render-jobs/${jobId}`);

        // Transient (offline venue, dropped request): the next tick retries.
        // Permanent failures are terminal, or the poll would ask a job it can
        // never read forever.
        if (response.status === 401 || response.status === 403) {
          stopPolling();
          if (disposed) return;
          onAuthExpiredRef.current();
          return;
        }
        if (response.status === 404 || response.status === 410) {
          stopPolling();
          if (disposed) return;
          // The job is gone (deleted songset, purged row): report it with the
          // shape the callers expect rather than inventing a body.
          onFailedRef.current({
            id: jobId,
            status: "failed",
            errorMessage: null,
            mp3R2Key: null,
            mp4R2Key: null,
            chaptersR2Key: null,
            estimatedTotalSeconds: null,
          });
          return;
        }
        if (!response.ok) return;

        const job = (await response.json()) as RenderJobSnapshot;
        if (disposed) return;

        if (job.status === "completed") {
          stopPolling();
          onCompletedRef.current(job);
        } else if (job.status === "failed") {
          stopPolling();
          onFailedRef.current(job);
        } else if (job.status === "cancelled") {
          stopPolling();
          onCancelledRef.current();
        }
      } catch {
        // Keep polling — a single failed request says nothing about the job.
      }
    };

    void poll();
    timer = window.setInterval(poll, RENDER_JOB_POLL_INTERVAL_MS);

    return () => {
      disposed = true;
      stopPolling();
    };
  }, [jobId, enabled]);
}
