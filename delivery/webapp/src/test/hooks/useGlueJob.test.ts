import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useGlueJob } from "@/hooks/useGlueJob";
import type { GlueJobStatus } from "@/lib/glue/client";

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  readonly url: string;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  close(): void {
    this.closed = true;
  }

  emit(status: GlueJobStatus): void {
    this.onmessage?.({ data: JSON.stringify(status) });
  }

  fail(): void {
    this.onerror?.();
  }
}

function status(overrides: Partial<GlueJobStatus> = {}): GlueJobStatus {
  return {
    id: "job-1",
    songsetId: "songset-1",
    fromItemId: "item-a",
    toItemId: "item-b",
    descriptorKey: "descriptor-1",
    phase: "queued",
    active: true,
    takeMode: "new-take",
    createdAt: "2026-11-22T10:00:00.000Z",
    startedAt: "2026-11-22T10:00:00.000Z",
    finishedAt: null,
    elapsedSeconds: 4,
    attemptId: "attempt-1",
    providerContacted: false,
    selectedTakeId: null,
    resultingTakeId: null,
    errorCode: null,
    errorDetail: null,
    retryKind: null,
    quota: { limit: 5, used: 1, remaining: 4, resetAt: null },
    ...overrides,
  };
}

interface FetchCalls {
  discovery: number;
  job: number;
}

function installFetch(initialStatuses: GlueJobStatus[] = []): FetchCalls {
  const calls: FetchCalls = { discovery: 0, job: 0 };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("?active=1")) {
        calls.discovery += 1;
        const active = initialStatuses.filter((entry) => entry.active);
        return {
          ok: true,
          json: async () => ({
            active,
            latestByFromItemId: Object.fromEntries(
              initialStatuses.map((entry) => [entry.fromItemId, entry])
            ),
          }),
        };
      }
      calls.job += 1;
      return { ok: true, json: async () => status() };
    })
  );
  return calls;
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.useRealTimers();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("useGlueJob", () => {
  it("discovers on load, subscribes, and exposes the real phase and elapsed seconds", async () => {
    const calls = installFetch();
    const { result } = renderHook(() =>
      useGlueJob({ jobId: "job-1", songsetId: "songset-1", fromItemId: "item-a" })
    );

    await waitFor(() => expect(calls.discovery).toBe(1));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    expect(FakeEventSource.instances[0].url).toBe("/api/glue-jobs/job-1/events");

    act(() => FakeEventSource.instances[0].emit(status({ phase: "composing" })));
    await waitFor(() => expect(result.current.phase).toBe("composing"));
    expect(result.current.elapsedSeconds).toBe(4);
    expect(result.current.terminal).toBe(false);
    expect(result.current.status).not.toHaveProperty("percent");
  });

  it("stops the stream on a terminal phase", async () => {
    installFetch();
    const { result } = renderHook(() =>
      useGlueJob({ jobId: "job-1", songsetId: "songset-1", fromItemId: "item-a" })
    );
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));

    act(() =>
      FakeEventSource.instances[0].emit(
        status({ phase: "ready", active: false, providerContacted: true })
      )
    );
    await waitFor(() => expect(result.current.terminal).toBe(true));
    expect(result.current.phase).toBe("ready");
    expect(FakeEventSource.instances[0].closed).toBe(true);
  });

  it("restart reopens the stream for the same job after a terminal phase", async () => {
    installFetch();
    const { result } = renderHook(() =>
      useGlueJob({ jobId: "job-1", songsetId: "songset-1", fromItemId: "item-a" })
    );
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));

    act(() =>
      FakeEventSource.instances[0].emit(
        status({ phase: "failed", active: false, retryKind: "free" })
      )
    );
    await waitFor(() => expect(result.current.terminal).toBe(true));
    expect(FakeEventSource.instances[0].closed).toBe(true);

    act(() => result.current.restart());
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(2));
    expect(FakeEventSource.instances[1].url).toBe("/api/glue-jobs/job-1/events");
    // The retried job keeps the rail's last known phase until the stream speaks.
    expect(result.current.phase).toBe("failed");

    act(() => FakeEventSource.instances[1].emit(status({ phase: "queued" })));
    await waitFor(() => expect(result.current.phase).toBe("queued"));
  });

  it("reconnects to the same job and discovers a take that completes between streams", async () => {
    vi.useFakeTimers();
    const jobs = [status({ phase: "composing" })];
    const calls = installFetch(jobs);
    const { result } = renderHook(() =>
      useGlueJob({ jobId: "job-1", songsetId: "songset-1", fromItemId: "item-a" })
    );

    await act(async () => {
      await Promise.resolve();
    });
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(calls.discovery).toBe(1);

    act(() => FakeEventSource.instances[0].fail());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });

    expect(calls.discovery).toBe(2);
    expect(FakeEventSource.instances).toHaveLength(2);
    expect(FakeEventSource.instances[0].closed).toBe(true);
    expect(FakeEventSource.instances[1].url).toBe("/api/glue-jobs/job-1/events");

    act(() => FakeEventSource.instances[1].fail());
    jobs[0] = status({ phase: "ready", active: false, resultingTakeId: "take-1" });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });

    expect(calls.discovery).toBe(3);
    expect(FakeEventSource.instances).toHaveLength(2);
    expect(FakeEventSource.instances[1].closed).toBe(true);
    expect(result.current.terminal).toBe(true);
    expect(result.current.status).toMatchObject({
      id: "job-1",
      phase: "ready",
      resultingTakeId: "take-1",
    });
  });

  it("discovers again when the tab becomes visible", async () => {
    const calls = installFetch();
    renderHook(() =>
      useGlueJob({ jobId: "job-1", songsetId: "songset-1", fromItemId: "item-a" })
    );
    await waitFor(() => expect(calls.discovery).toBe(1));

    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await waitFor(() => expect(calls.discovery).toBe(2));
  });

  it("polls the job endpoint when EventSource is unavailable", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("EventSource", undefined);
    const calls = installFetch();

    renderHook(() =>
      useGlueJob({ jobId: "job-1", songsetId: "songset-1", fromItemId: "item-a" })
    );
    for (let tick = 0; tick < 3; tick += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
    }

    expect(calls.job).toBeGreaterThanOrEqual(1);
  });

  it("does not watch when disabled or missing a target", () => {
    const calls = installFetch();
    renderHook(() =>
      useGlueJob({
        jobId: null,
        songsetId: "songset-1",
        fromItemId: "item-a",
      })
    );
    expect(calls.discovery).toBe(0);
    expect(FakeEventSource.instances).toHaveLength(0);
  });
});
