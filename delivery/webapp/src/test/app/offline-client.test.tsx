import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithLocale as render } from "@/test/render";
import { OfflineClient } from "@/app/offline/OfflineClient";
import type { OfflineSongsetRecord } from "@/lib/offline/offline-index";
import { probeConnectivity, setConnectivityProbe } from "@/hooks/useConnectivity";
const { mockListOfflineRecords, mockRemoveOfflineSongset, mockMatchCachedArtifact } =
  vi.hoisted(() => ({
    mockListOfflineRecords: vi.fn<() => Promise<unknown>>(),
    mockRemoveOfflineSongset: vi.fn<() => Promise<void>>(),
    mockMatchCachedArtifact: vi.fn<() => Promise<unknown>>(),
  }));

vi.mock("@/lib/offline/offline-index", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/offline/offline-index")>();
  return {
    ...actual,
    listOfflineRecords: mockListOfflineRecords,
    removeOfflineSongset: mockRemoveOfflineSongset,
  };
});

vi.mock("@/lib/offline/artifact-cache", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/offline/artifact-cache")>();
  return {
    ...actual,
    matchCachedArtifact: mockMatchCachedArtifact,
  };
});

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), loading: vi.fn() },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    refresh: vi.fn(),
  }),
}));

function makeRecord(overrides: Partial<OfflineSongsetRecord> = {}): OfflineSongsetRecord {
  return {
    songsetId: "songset-1",
    renderJobId: "render-job-1",
    songsetName: "Sunday Worship",
    cachedMp3: true,
    cachedMp4: true,
    cachedChapters: true,
    cachedAt: "2024-01-15T11:00:00Z",
    chapterContentHashes: [],
    ...overrides,
  };
}

function songsetResponse(latestRenderJobId: string) {
  return {
    ok: true,
    json: () => Promise.resolve({ latestRenderJobId }),
  };
}

describe("OfflineClient staleness comparison fetch loop (issue #211 follow-up)", () => {
  const mockedFetch = vi.fn();
  let realFetch: typeof globalThis.fetch | undefined;

  beforeEach(() => {
    vi.clearAllMocks();
    mockMatchCachedArtifact.mockResolvedValue(true);
    mockRemoveOfflineSongset.mockResolvedValue(undefined);
    realFetch = globalThis.fetch;
    globalThis.fetch = mockedFetch as unknown as typeof globalThis.fetch;
  });

  afterEach(() => {
    setConnectivityProbe(null);
    if (realFetch) globalThis.fetch = realFetch;
  });

  async function confirmOnline(): Promise<void> {
    const probe = vi.fn<() => Promise<boolean>>();
    probe.mockResolvedValue(true);
    setConnectivityProbe(probe);
    await probeConnectivity();
  }

  it("fetches each songset exactly once when the server matches the cached renderJobId", async () => {
    mockedFetch.mockImplementation((url: string) => {
      if (url === "/api/songsets/songset-1") return Promise.resolve(songsetResponse("render-job-1"));
      return Promise.reject(new Error(`unexpected fetch: ${url}`));
    });
    mockListOfflineRecords.mockResolvedValue([makeRecord()]);
    await confirmOnline();

    render(<OfflineClient />);

    await waitFor(() => {
      expect(mockedFetch).toHaveBeenCalledWith("/api/songsets/songset-1");
    });
    // Settle: pre-fix the effect refires on every rows identity change and
    // keeps fetching; post-fix the count pins at one.
    const { promise, resolve } = Promise.withResolvers<void>();
    setTimeout(resolve, 100);
    await promise;
    expect(mockedFetch.mock.calls.length).toBe(1);
    // Server matches the cache → no Update affordance.
    expect(screen.queryByRole("button", { name: "Update" })).not.toBeInTheDocument();
  });

  it("does not refetch a row already found stale and surfaces the Update affordance once", async () => {
    mockedFetch.mockImplementation((url: string) => {
      if (url === "/api/songsets/songset-1") return Promise.resolve(songsetResponse("render-job-2"));
      return Promise.reject(new Error(`unexpected fetch: ${url}`));
    });
    mockListOfflineRecords.mockResolvedValue([makeRecord()]);
    await confirmOnline();

    render(<OfflineClient />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Update" })).toBeInTheDocument();
    });
    const { promise, resolve } = Promise.withResolvers<void>();
    setTimeout(resolve, 100);
    await promise;
    // One comparison pass; the stale row is not refetched on the re-run.
    expect(mockedFetch.mock.calls.length).toBe(1);
  });
});
