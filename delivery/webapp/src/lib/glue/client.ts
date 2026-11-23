// Browser-facing AI-transition client (spec §2.4, §6.3). Every credit, phase,
// and quota value comes from the server; this module only fetches, subscribes,
// and classifies failures. It never computes or fabricates a percentage.

import type { GlueQuota } from "@/lib/glue/quota";

export type { GlueQuota };

export type GlueTakeMode = "initial" | "new-take";
export type GlueRetryKind = "free" | "paid" | "none";

export const GLUE_TERMINAL_PHASES = [
  "ready",
  "failed",
  "cancelled",
  "dispatch-failed",
] as const;

export function isGlueTerminalPhase(phase: string): boolean {
  return (GLUE_TERMINAL_PHASES as readonly string[]).includes(phase);
}

/** Identical shape to the server route serialization. */
export interface GlueJobStatus {
  id: string;
  songsetId: string;
  fromItemId: string;
  toItemId: string;
  phase: string;
  active: boolean;
  takeMode: GlueTakeMode;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  elapsedSeconds: number;
  attemptId: string;
  providerContacted: boolean;
  selectedTakeId: string | null;
  resultingTakeId: string | null;
  errorCode: string | null;
  errorDetail: string | null;
  retryKind: GlueRetryKind | null;
  quota: GlueQuota;
}

export interface ActiveGlueJobsPayload {
  active: GlueJobStatus[];
  latestByFromItemId: Record<string, GlueJobStatus>;
}

export interface GlueApiError {
  httpStatus: number;
  code: string;
  message: string;
  jobId?: string;
  providerStatus?: number;
  detail?: string;
}

export type GlueApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: GlueApiError };

export interface CreateGlueJobRequest {
  fromItemId: string;
  clientRequestId: string;
  expectedFromUpdatedAt: string;
  expectedToUpdatedAt: string;
  takeMode: GlueTakeMode;
}

export type CreateGlueJobResponse =
  | { status: "ready"; takeId: string; cached: true }
  | { jobId: string; status: "queued" };

function normalizeApiError(body: unknown, httpStatus: number): GlueApiError {
  if (typeof body === "object" && body !== null) {
    const record = body as Record<string, unknown>;
    const code = typeof record.code === "string" ? record.code : "GLUE_ERROR";
    const message =
      typeof record.message === "string"
        ? record.message
        : typeof record.error === "string"
          ? record.error
          : "The AI transition request failed.";
    return {
      httpStatus,
      code,
      message,
      ...(typeof record.jobId === "string" ? { jobId: record.jobId } : {}),
      ...(typeof record.providerStatus === "number"
        ? { providerStatus: record.providerStatus }
        : {}),
      ...(typeof record.detail === "string" ? { detail: record.detail } : {}),
    };
  }
  return {
    httpStatus,
    code: "GLUE_ERROR",
    message: "The AI transition request failed.",
  };
}

async function glueRequest<T>(url: string, init?: RequestInit): Promise<GlueApiResult<T>> {
  try {
    // Glue endpoints answer with saved state that changes under a stable URL
    // (job phase, credits for the same Junction and beat count). A default
    // browser-cached GET would replay the previous body — a stale phase or the
    // previous beat count's credits — so reads always revalidate. Mutations
    // keep their explicit methods.
    const response = await fetch(url, { cache: "no-store", ...init });
    const body: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      return { ok: false, error: normalizeApiError(body, response.status) };
    }
    return { ok: true, data: body as T };
  } catch {
    return {
      ok: false,
      error: {
        httpStatus: 0,
        code: "GLUE_NETWORK",
        message: "Could not reach the server. Try again.",
      },
    };
  }
}

function postJson(url: string): Promise<GlueApiResult<unknown>> {
  return glueRequest(url, { method: "POST" });
}

export function createGlueJobRequest(
  songsetId: string,
  body: CreateGlueJobRequest
): Promise<GlueApiResult<CreateGlueJobResponse>> {
  return glueRequest<CreateGlueJobResponse>(
    `/api/songsets/${encodeURIComponent(songsetId)}/glue-jobs`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }
  );
}

export function fetchGlueJob(jobId: string): Promise<GlueApiResult<GlueJobStatus>> {
  return glueRequest<GlueJobStatus>(`/api/glue-jobs/${encodeURIComponent(jobId)}`);
}

export function fetchActiveGlueJobs(
  songsetId: string
): Promise<GlueApiResult<ActiveGlueJobsPayload>> {
  return glueRequest<ActiveGlueJobsPayload>(
    `/api/songsets/${encodeURIComponent(songsetId)}/glue-jobs?active=1`
  );
}

export function cancelGlueJobRequest(
  jobId: string
): Promise<GlueApiResult<{ jobId: string; status: "cancelled" }>> {
  return postJson(`/api/glue-jobs/${encodeURIComponent(jobId)}/cancel`) as Promise<
    GlueApiResult<{ jobId: string; status: "cancelled" }>
  >;
}

export function redispatchGlueJobRequest(
  jobId: string
): Promise<GlueApiResult<{ jobId: string; status: string; charged: false }>> {
  return postJson(`/api/glue-jobs/${encodeURIComponent(jobId)}/redispatch`) as Promise<
    GlueApiResult<{ jobId: string; status: string; charged: false }>
  >;
}

export function retryGlueProcessingRequest(
  jobId: string
): Promise<GlueApiResult<{ jobId: string; status: string; charged: false }>> {
  return postJson(`/api/glue-jobs/${encodeURIComponent(jobId)}/retry-processing`) as Promise<
    GlueApiResult<{ jobId: string; status: string; charged: false }>
  >;
}

export function resumeGlueJobRequest(
  jobId: string
): Promise<GlueApiResult<{ jobId: string; status: string; charged: false }>> {
  return postJson(`/api/glue-jobs/${encodeURIComponent(jobId)}/resume`) as Promise<
    GlueApiResult<{ jobId: string; status: string; charged: false }>
  >;
}

export interface GlueJobWatchOptions {
  jobId: string;
  songsetId: string;
  /** Discovery maps a job back to its Junction through this id. */
  fromItemId: string;
}

export interface GlueJobWatchHandlers {
  onStatus: (status: GlueJobStatus) => void;
  onDiscovery?: (payload: ActiveGlueJobsPayload) => void;
  onError?: (error: GlueApiError | null) => void;
}

const RECONNECT_DELAY_MS = 1_000;
const POLL_INTERVAL_MS = 1_000;

/**
 * Subscribes to one job's SSE stream with a discovery fallback: discovery runs
 * on load, after a dropped stream, and when the tab becomes visible again. If
 * the runtime has no EventSource, the same discovery loop polls once per second.
 * Terminal phases stop every timer and the stream.
 */
export function watchGlueJob(
  options: GlueJobWatchOptions,
  handlers: GlueJobWatchHandlers
): () => void {
  let stopped = false;
  let source: EventSource | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let lastStatus: GlueJobStatus | null = null;

  const terminal = () => lastStatus !== null && isGlueTerminalPhase(lastStatus.phase);

  const stopStream = () => {
    if (source !== null) {
      source.close();
      source = null;
    }
  };

  const stopTimers = () => {
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (pollTimer !== null) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  };

  const deliver = (status: GlueJobStatus) => {
    lastStatus = status;
    handlers.onStatus(status);
    if (isGlueTerminalPhase(status.phase)) {
      stopStream();
      stopTimers();
    }
  };

  const discover = async (): Promise<GlueJobStatus | null> => {
    const result = await fetchActiveGlueJobs(options.songsetId);
    if (!result.ok) {
      handlers.onError?.(result.error);
      return null;
    }
    handlers.onDiscovery?.(result.data);
    return (
      result.data.active.find((job) => job.id === options.jobId) ??
      result.data.latestByFromItemId[options.fromItemId] ??
      null
    );
  };

  const scheduleReconnect = () => {
    if (stopped || terminal() || reconnectTimer !== null) return;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      void (async () => {
        const status = await discover();
        if (status !== null) deliver(status);
        if (!stopped && !terminal()) openStream();
      })();
    }, RECONNECT_DELAY_MS);
  };

  const startPolling = () => {
    if (pollTimer !== null || stopped || terminal()) return;
    pollTimer = setInterval(() => {
      void (async () => {
        const result = await fetchGlueJob(options.jobId);
        if (result.ok) deliver(result.data);
        else handlers.onError?.(result.error);
      })();
    }, POLL_INTERVAL_MS);
  };

  const openStream = () => {
    if (stopped || terminal()) return;
    if (typeof EventSource === "undefined") {
      startPolling();
      return;
    }
    stopStream();
    const stream = new EventSource(
      `/api/glue-jobs/${encodeURIComponent(options.jobId)}/events`
    );
    source = stream;
    stream.onmessage = (event) => {
      try {
        const status = JSON.parse(event.data) as GlueJobStatus;
        deliver(status);
      } catch {
        // A malformed frame is dropped; the next frame or reconnect wins.
      }
    };
    stream.onerror = () => {
      stopStream();
      if (stopped || terminal()) return;
      handlers.onError?.(null);
      scheduleReconnect();
    };
  };

  const resume = async () => {
    if (stopped) return;
    const status = await discover();
    if (status !== null) deliver(status);
    if (!stopped && !terminal()) openStream();
  };

  const onVisibilityChange = () => {
    if (typeof document !== "undefined" && document.visibilityState === "visible") {
      void resume();
    }
  };

  if (typeof document !== "undefined") {
    document.addEventListener("visibilitychange", onVisibilityChange);
  }
  void resume();

  return () => {
    stopped = true;
    stopStream();
    stopTimers();
    if (typeof document !== "undefined") {
      document.removeEventListener("visibilitychange", onVisibilityChange);
    }
  };
}
