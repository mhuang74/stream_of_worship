import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, waitFor, fireEvent } from "@testing-library/react";
import { renderWithLocale as render } from "@/test/render";
import { WorshipClient } from "@/app/worship/WorshipClient";
import type { OfflineSongsetRecord } from "@/lib/offline/offline-index";
import { probeConnectivity, setConnectivityProbe } from "@/hooks/useConnectivity";
const { mockListOfflineRecords, mockRemoveOfflineSongset, mockMatchCachedArtifact, mockDownloadOfflineArtifacts } =
  vi.hoisted(() => ({
    mockListOfflineRecords: vi.fn<() => Promise<unknown>>(),
    mockRemoveOfflineSongset: vi.fn<() => Promise<void>>(),
    mockMatchCachedArtifact: vi.fn<() => Promise<unknown>>(),
    mockDownloadOfflineArtifacts: vi.fn<() => Promise<void>>(),
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

vi.mock("@/lib/offline/download-offline", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/offline/download-offline")>();
  return {
    ...actual,
    downloadOfflineArtifacts: mockDownloadOfflineArtifacts,
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

function songsetListResponse(total: number, songsets: object[]) {
  return {
    ok: true,
    json: () => Promise.resolve({ songsets, total }),
  };
}

function makeApiSongset(overrides: Record<string, unknown> = {}) {
  return {
    id: "songset-2",
    name: "Evening Prayer",
    description: null,
    createdAt: "2024-02-01T10:00:00Z",
    updatedAt: "2024-02-01T10:00:00Z",
    renderState: "fresh",
    itemCount: 3,
    durationSeconds: 600,
    latestRenderJobId: "render-job-2",
    lastFailedRenderJobId: null,
    lastCompletedRenderJobId: "render-job-2",
    lastCompletedRenderHasVideo: true,
    renderErrorMessage: null,
    failedAt: null,
    themes: [],
    ...overrides,
  };
}

describe("WorshipClient (issue #211 follow-up descope)", () => {
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

  it("renders only downloaded songsets in the default Ready view when offline", async () => {
    mockListOfflineRecords.mockResolvedValue([makeRecord()]);

    render(<WorshipClient />);

    await waitFor(() => {
      expect(screen.getByText("Sunday Worship")).toBeInTheDocument();
    });
    expect(screen.queryByText("Evening Prayer")).not.toBeInTheDocument();
    expect(mockedFetch).not.toHaveBeenCalled();
  });

  it("fetch-merges all rendered songsets online; Ready filter shows downloaded, All shows everything", async () => {
    mockedFetch.mockImplementation((url: string) => {
      if (url === "/api/songsets?limit=100&offset=0") {
        return Promise.resolve(
          songsetListResponse(2, [
            makeApiSongset({ id: "songset-1", name: "Sunday Worship", latestRenderJobId: "render-job-1", lastCompletedRenderJobId: "render-job-1" }),
            makeApiSongset(),
          ])
        );
      }
      return Promise.reject(new Error(`unexpected fetch: ${url}`));
    });
    mockListOfflineRecords.mockResolvedValue([makeRecord()]);
    await confirmOnline();

    render(<WorshipClient />);

    // Ready (default) view: only the downloaded set shows.
    await waitFor(() => {
      expect(mockedFetch).toHaveBeenCalledWith("/api/songsets?limit=100&offset=0");
    });
    await waitFor(() => {
      expect(screen.getByText("Sunday Worship")).toBeInTheDocument();
    });
    expect(screen.queryByText("Evening Prayer")).not.toBeInTheDocument();
    // All filter reveals the undownloaded rendered set.
    fireEvent.click(screen.getByRole("button", { name: "All" }));
    await waitFor(() => {
      expect(screen.getByText("Evening Prayer")).toBeInTheDocument();
    });
    // Ready filter again hides it.
    fireEvent.click(screen.getByRole("button", { name: "Ready for Offline Worship" }));
    expect(screen.queryByText("Evening Prayer")).not.toBeInTheDocument();
  });

  it("marks a downloaded row stale when the cached renderJobId trails the server's latest render", async () => {
    mockedFetch.mockImplementation((url: string) => {
      if (url === "/api/songsets?limit=100&offset=0") {
        return Promise.resolve(
          songsetListResponse(1, [
            makeApiSongset({
              id: "songset-1",
              name: "Sunday Worship",
              latestRenderJobId: "render-job-2",
              lastCompletedRenderJobId: "render-job-2",
            }),
          ])
        );
      }
      return Promise.reject(new Error(`unexpected fetch: ${url}`));
    });
    mockListOfflineRecords.mockResolvedValue([makeRecord()]);
    await confirmOnline();

    render(<WorshipClient />);

    // Stale ⇒ the row's kebab menu offers re-download (SongsetRow).
    await waitFor(() => {
      expect(screen.getByText("Sunday Worship")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: "Open menu" }));
    await waitFor(() => {
      expect(screen.getByText(/re-download for offline/i)).toBeInTheDocument();
    });
  });

  it("paginates the fetch loop past the server's 100-per-page cap", async () => {
    const page1 = Array.from({ length: 100 }, (_, i) =>
      makeApiSongset({ id: `set-${i}`, name: `Set ${i}`, latestRenderJobId: `job-${i}`, lastCompletedRenderJobId: `job-${i}` })
    );
    const page2 = [makeApiSongset({ id: "set-100", name: "Set 100", latestRenderJobId: "job-100", lastCompletedRenderJobId: "job-100" })];
    mockedFetch.mockImplementation((url: string) => {
      if (url === "/api/songsets?limit=100&offset=0") {
        return Promise.resolve(songsetListResponse(101, page1));
      }
      if (url === "/api/songsets?limit=100&offset=100") {
        return Promise.resolve(songsetListResponse(101, page2));
      }
      return Promise.reject(new Error(`unexpected fetch: ${url}`));
    });
    mockListOfflineRecords.mockResolvedValue([]);
    await confirmOnline();

    render(<WorshipClient />);

    await waitFor(() => {
      expect(mockedFetch).toHaveBeenCalledWith("/api/songsets?limit=100&offset=100");
    });
    // Loop stops after page 2 (offset 100 + 1 row >= total 101).
    const { promise, resolve } = Promise.withResolvers<void>();
    setTimeout(resolve, 100);
    await promise;
    expect(mockedFetch.mock.calls.length).toBe(2);
  });

  it("keeps whatever loaded when a page fetch fails mid-loop (offline-first, no hard fail)", async () => {
    mockedFetch.mockImplementation((url: string) => {
      if (url === "/api/songsets?limit=100&offset=0") {
        return Promise.resolve(
          songsetListResponse(150, [makeApiSongset({ id: "set-0", name: "Set 0", latestRenderJobId: "job-0", lastCompletedRenderJobId: "job-0" })])
        );
      }
      if (url === "/api/songsets?limit=100&offset=100") {
        return Promise.reject(new Error("network gone"));
      }
      return Promise.reject(new Error(`unexpected fetch: ${url}`));
    });
    mockListOfflineRecords.mockResolvedValue([]);
    await confirmOnline();

    render(<WorshipClient />);

    // Page 1 loaded ⇒ Set 0 exists (All filter; it is not downloaded, so the
    // default Ready view stays empty). The failure does not blank the page.
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "All" })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: "All" }));
    await waitFor(() => {
      expect(screen.getByText("Set 0")).toBeInTheDocument();
    });
  });

  it("byte-verifies the mp3 artifact for an MP3-only download instead of wrongly marking it stale", async () => {
    mockListOfflineRecords.mockResolvedValue([
      makeRecord({ cachedMp4: false }),
    ]);

    render(<WorshipClient />);

    await waitFor(() => {
      expect(screen.getByText("Sunday Worship")).toBeInTheDocument();
    });
    // Only the MP3 artifact is probed; the absent MP4 must not mark it stale.
    await waitFor(() => {
      expect(mockMatchCachedArtifact).toHaveBeenCalledWith("render-job-1", "mp3");
    });
    expect(mockMatchCachedArtifact).not.toHaveBeenCalledWith("render-job-1", "mp4");
    fireEvent.click(screen.getByRole("button", { name: "Open menu" }));
    await waitFor(() => {
      // Fresh row's menu: no re-download item (would only appear when stale).
      expect(screen.queryByText(/re-download for offline/i)).not.toBeInTheDocument();
    });
  });

  it("marks a downloaded MP4 row stale only when neither mp4 nor cached mp3 exists", async () => {
    mockListOfflineRecords.mockResolvedValue([makeRecord()]);
    mockMatchCachedArtifact.mockImplementation((_jobId: string, kind: string) =>
      Promise.resolve(kind === "mp3" ? new Response(null) : null)
    );

    render(<WorshipClient />);

    await waitFor(() => {
      expect(screen.getByText("Sunday Worship")).toBeInTheDocument();
    });
    // MP4 probed first, missing; MP3 fallback present ⇒ stays fresh.
    await waitFor(() => {
      expect(mockMatchCachedArtifact).toHaveBeenCalledWith("render-job-1", "mp3");
    });
    fireEvent.click(screen.getByRole("button", { name: "Open menu" }));
    await waitFor(() => {
      expect(screen.queryByText(/re-download for offline/i)).not.toBeInTheDocument();
    });
  });

  it("excludes audio-only renders from the All view unless downloaded", async () => {
    mockedFetch.mockImplementation((url: string) => {
      if (url === "/api/songsets?limit=100&offset=0") {
        return Promise.resolve(
          songsetListResponse(2, [
            makeApiSongset({
              id: "audio-only",
              name: "Audio Only Set",
              latestRenderJobId: "job-audio",
              lastCompletedRenderJobId: "job-audio",
              lastCompletedRenderHasVideo: false,
            }),
            makeApiSongset(),
          ])
        );
      }
      return Promise.reject(new Error(`unexpected fetch: ${url}`));
    });
    mockListOfflineRecords.mockResolvedValue([]);
    await confirmOnline();

    render(<WorshipClient />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "All" })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: "All" }));
    await waitFor(() => {
      expect(screen.getByText("Evening Prayer")).toBeInTheDocument();
    });
    // Audio-only render: no Play action (online load would reject with
    // "No video available"), so the row is filtered out entirely.
    expect(screen.queryByText("Audio Only Set")).not.toBeInTheDocument();
  });

  it("shows a downloaded audio-only set in the All view (offline playback works)", async () => {
    mockedFetch.mockImplementation((url: string) => {
      if (url === "/api/songsets?limit=100&offset=0") {
        return Promise.resolve(
          songsetListResponse(1, [
            makeApiSongset({
              id: "songset-1",
              name: "Sunday Worship",
              latestRenderJobId: "render-job-2",
              lastCompletedRenderJobId: "render-job-2",
              lastCompletedRenderHasVideo: false,
            }),
          ])
        );
      }
      return Promise.reject(new Error(`unexpected fetch: ${url}`));
    });
    mockListOfflineRecords.mockResolvedValue([makeRecord()]);
    await confirmOnline();

    render(<WorshipClient />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "All" })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: "All" }));
    await waitFor(() => {
      expect(screen.getByText("Sunday Worship")).toBeInTheDocument();
    });
  });

  it("downloads the last completed render when the latest job is not completed", async () => {
    mockDownloadOfflineArtifacts.mockResolvedValue(undefined);
    mockedFetch.mockImplementation((url: string) => {
      if (url === "/api/songsets?limit=100&offset=0") {
        return Promise.resolve(
          songsetListResponse(1, [
            makeApiSongset({
              id: "songset-2",
              name: "Evening Prayer",
              // Latest render queued (running job), older render completed.
              renderState: "rendering",
              latestRenderJobId: "render-job-9",
              lastCompletedRenderJobId: "render-job-2",
            }),
          ])
        );
      }
      return Promise.reject(new Error(`unexpected fetch: ${url}`));
    });
    mockListOfflineRecords.mockResolvedValue([]);
    await confirmOnline();

    render(<WorshipClient />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "All" })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: "All" }));
    await waitFor(() => {
      expect(screen.getByText("Evening Prayer")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: "Open menu" }));
    fireEvent.click(screen.getByText(/download for offline/i));

    await waitFor(() => {
      expect(mockDownloadOfflineArtifacts).toHaveBeenCalledWith(
        expect.objectContaining({ renderJobId: "render-job-2" }),
        expect.any(Function)
      );
    });
  });
});
