import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, renderHook } from "@testing-library/react";
import {
  RENDER_JOB_POLL_INTERVAL_MS,
  useRenderJobWatch,
  type RenderJobSnapshot,
} from "@/hooks/useRenderJobWatch";

function job(overrides: Partial<RenderJobSnapshot> = {}): RenderJobSnapshot {
  return {
    id: "job-1",
    status: "running",
    errorMessage: null,
    mp3R2Key: null,
    mp4R2Key: null,
    chaptersR2Key: null,
    estimatedTotalSeconds: 300,
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

/** Lets the immediate poll and its promise chain settle. */
async function flush() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

/** One interval tick plus everything it kicks off. */
async function tick() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(RENDER_JOB_POLL_INTERVAL_MS);
  });
  await flush();
}

describe("useRenderJobWatch", () => {
  const onCompleted = vi.fn();
  const onFailed = vi.fn();
  const onCancelled = vi.fn();
  const onAuthExpired = vi.fn();

  const renderWatch = (props: { jobId?: string | null; enabled?: boolean } = {}) =>
    renderHook(({ jobId = "job-1", enabled = true }) =>
      useRenderJobWatch({
        jobId,
        enabled,
        onCompleted,
        onFailed,
        onCancelled,
        onAuthExpired,
      }),
      { initialProps: props }
    );

  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("polls immediately on mount, without waiting an interval", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse(job())));
    vi.stubGlobal("fetch", fetchMock);

    renderWatch();
    await flush();

    expect(fetchMock).toHaveBeenCalledWith("/api/render-jobs/job-1");
    expect(onCompleted).not.toHaveBeenCalled();
  });

  it("keeps polling a running job every interval", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse(job())));
    vi.stubGlobal("fetch", fetchMock);

    renderWatch();
    await flush();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await tick();
    expect(fetchMock).toHaveBeenCalledTimes(2);

    await tick();
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("reports completion once and stops polling", async () => {
    const completed = job({
      status: "completed",
      mp3R2Key: "renders/job-1/output.mp3",
      mp4R2Key: "renders/job-1/output.mp4",
    });
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse(completed)));
    vi.stubGlobal("fetch", fetchMock);

    renderWatch();
    await flush();

    expect(onCompleted).toHaveBeenCalledTimes(1);
    expect(onCompleted).toHaveBeenCalledWith(completed);

    await tick();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(onCompleted).toHaveBeenCalledTimes(1);
  });

  it("reports the failing job once and stops polling", async () => {
    const failed = job({ status: "failed", errorMessage: "FFmpeg crashed" });
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse(failed)));
    vi.stubGlobal("fetch", fetchMock);

    renderWatch();
    await flush();

    expect(onFailed).toHaveBeenCalledTimes(1);
    expect(onFailed).toHaveBeenCalledWith(failed);

    await tick();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(onFailed).toHaveBeenCalledTimes(1);
  });

  it("reports a cancellation once and stops polling", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse(job({ status: "cancelled" }))));
    vi.stubGlobal("fetch", fetchMock);

    renderWatch();
    await flush();

    expect(onCancelled).toHaveBeenCalledTimes(1);
    expect(onFailed).not.toHaveBeenCalled();

    await tick();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("reports an expired session and stops polling", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse({}, 401)));
    vi.stubGlobal("fetch", fetchMock);

    renderWatch();
    await flush();

    expect(onAuthExpired).toHaveBeenCalledTimes(1);
    expect(onFailed).not.toHaveBeenCalled();

    await tick();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("reports a missing job as a failure and stops polling", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse({}, 404)));
    vi.stubGlobal("fetch", fetchMock);

    renderWatch();
    await flush();

    expect(onFailed).toHaveBeenCalledTimes(1);
    expect(onFailed).toHaveBeenCalledWith({
      id: "job-1",
      status: "failed",
      errorMessage: null,
      mp3R2Key: null,
      mp4R2Key: null,
      chaptersR2Key: null,
      estimatedTotalSeconds: null,
    });

    await tick();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("keeps polling through a server error", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse({}, 500)));
    vi.stubGlobal("fetch", fetchMock);

    renderWatch();
    await flush();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await tick();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(onFailed).not.toHaveBeenCalled();
  });

  it("keeps polling through a rejected request", async () => {
    const fetchMock = vi.fn(() => Promise.reject(new TypeError("Failed to fetch")));
    vi.stubGlobal("fetch", fetchMock);

    renderWatch();
    await flush();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await tick();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(onAuthExpired).not.toHaveBeenCalled();
    expect(onFailed).not.toHaveBeenCalled();
  });

  it("never fetches when disabled", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse(job())));
    vi.stubGlobal("fetch", fetchMock);

    renderWatch({ enabled: false });
    await flush();
    await tick();

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("never fetches without a job id", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse(job())));
    vi.stubGlobal("fetch", fetchMock);

    renderWatch({ jobId: null });
    await flush();
    await tick();

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("stops polling on unmount", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse(job())));
    vi.stubGlobal("fetch", fetchMock);

    const view = renderWatch();
    await flush();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    view.unmount();
    await tick();

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not restart the interval when a callback identity changes", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse(job())));
    vi.stubGlobal("fetch", fetchMock);

    const view = renderHook(
      ({ name }: { name: string }) =>
        useRenderJobWatch({
          jobId: "job-1",
          enabled: true,
          onCompleted: () => name,
          onFailed: () => name,
          onCancelled: () => name,
          onAuthExpired: () => name,
        }),
      { initialProps: { name: "a" } }
    );
    await flush();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    view.rerender({ name: "b" });
    await flush();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await tick();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
