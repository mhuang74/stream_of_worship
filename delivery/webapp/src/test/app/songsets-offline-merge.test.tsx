import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, waitFor, fireEvent } from "@testing-library/react";
import { renderWithLocale as render } from "@/test/render";
import { SongsetsClient } from "@/app/songsets/SongsetsClient";
import { RenderState } from "@/components/songset/RenderStatusBadge";
import type { OfflineSongsetRecord } from "@/lib/offline/offline-index";
import { setConnectivityProbe } from "@/hooks/useConnectivity";

const {
  mockListOfflineRecords,
  mockRemoveOfflineSongset,
  toastSuccess,
  toastError,
} = vi.hoisted(() => ({
  mockListOfflineRecords: vi.fn<() => Promise<unknown>>(),
  mockRemoveOfflineSongset: vi.fn<() => Promise<void>>(),
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock("@/lib/offline/offline-index", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/offline/offline-index")>();
  return {
    ...actual,
    listOfflineRecords: mockListOfflineRecords,
    removeOfflineSongset: mockRemoveOfflineSongset,
  };
});

vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    refresh: vi.fn(),
  }),
}));

function makeApiSongset(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: "songset-1",
    name: "Sunday Worship",
    description: null,
    createdAt: "2024-01-15T10:00:00Z",
    updatedAt: "2024-01-15T10:30:00Z",
    renderState: "fresh" as RenderState,
    itemCount: 3,
    durationSeconds: 600,
    latestRenderJobId: "render-job-1",
    lastFailedRenderJobId: null,
    lastCompletedRenderJobId: "render-job-1",
    renderErrorMessage: null,
    failedAt: null,
    themes: [],
    ...overrides,
  };
}

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

function renderClient(songsets = [makeApiSongset()]) {
  return render(
    <SongsetsClient
      initialData={{ songsets, total: songsets.length }}
      currentPage={1}
      pageSize={20}
      initialSearch=""
    />
  );
}

function offlineBadge(): HTMLElement | null {
  const text = screen.queryByText(/offline/i);
  return text ? text.closest("span") : null;
}

describe("SongsetsClient offline merge (issue #207)", () => {
  const mockedFetch = vi.fn();
  let realFetch: typeof globalThis.fetch | undefined;

  beforeEach(() => {
    vi.clearAllMocks();
    mockListOfflineRecords.mockResolvedValue([]);
    mockRemoveOfflineSongset.mockResolvedValue(undefined);
    realFetch = globalThis.fetch;
    globalThis.fetch = mockedFetch as unknown as typeof globalThis.fetch;
    // The list effect fetches /api/songsets on mount.
    mockedFetch.mockImplementation((url: string) => {
      if (url.startsWith("/api/songsets")) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({ songsets: [makeApiSongset()], total: 1 }),
        });
      }
      return Promise.reject(new Error(`unexpected fetch: ${url}`));
    });
  });

  afterEach(() => {
    setConnectivityProbe(null);
    if (realFetch) globalThis.fetch = realFetch;
  });

  it("merges index records so rows with a record show the offline badge", async () => {
    mockListOfflineRecords.mockResolvedValue([makeRecord()]);
    renderClient();
    await waitFor(() => {
      expect(screen.getByText(/offline/i)).toBeInTheDocument();
    });
  });

  it("leaves rows without an index record visually unchanged", async () => {
    mockListOfflineRecords.mockResolvedValue([makeRecord({ songsetId: "other" })]);
    renderClient();
    // Wait for the merge effect to have run its course.
    await waitFor(() => {
      expect(mockListOfflineRecords).toHaveBeenCalled();
    });
    expect(screen.queryByText(/offline/i)).not.toBeInTheDocument();
    const row = screen.getByText("Sunday Worship").closest("[data-songset-id]");
    expect(row).not.toBeNull();
    expect(row?.className).not.toContain("border-amber-500/50");
  });

  it("shows the stale offline badge when the cached renderJobId differs from the latest", async () => {
    mockListOfflineRecords.mockResolvedValue([
      makeRecord({ renderJobId: "old-render-job" }),
    ]);
    renderClient();
    await waitFor(() => {
      const badge = offlineBadge();
      expect(badge).not.toBeNull();
      expect(badge?.className).toContain("text-amber-600");
    });
  });

  it("keeps the neutral offline badge when the cached renderJobId matches", async () => {
    mockListOfflineRecords.mockResolvedValue([makeRecord()]);
    renderClient();
    await waitFor(() => {
      const badge = offlineBadge();
      expect(badge).not.toBeNull();
      expect(badge?.className).not.toContain("text-amber-600");
    });
  });

  it("keeps the amber offline badge for a render-stale set whose cache matches", async () => {
    // Songs edited after the last render: the cached copy is of the current
    // render, but the render itself is out of date — the pre-existing
    // staleness signal must survive the merge.
    mockedFetch.mockImplementation(() =>
      Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve({
            songsets: [makeApiSongset({ renderState: "stale" as RenderState })],
            total: 1,
          }),
      })
    );
    mockListOfflineRecords.mockResolvedValue([makeRecord()]);
    renderClient();
    await waitFor(() => {
      const badge = offlineBadge();
      expect(badge).not.toBeNull();
      expect(badge?.className).toContain("text-amber-600");
    });
  });

  it("tints amber when the songset has no latest render but a cached copy exists", async () => {
    mockedFetch.mockImplementation(() =>
      Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve({
            songsets: [makeApiSongset({ latestRenderJobId: null })],
            total: 1,
          }),
      })
    );
    mockListOfflineRecords.mockResolvedValue([makeRecord()]);
    renderClient();
    await waitFor(() => {
      const badge = offlineBadge();
      expect(badge).not.toBeNull();
      expect(badge?.className).toContain("text-amber-600");
    });
  });

  it("Remove from offline invalidates artifacts, updates the list, and toasts", async () => {
    mockListOfflineRecords
      .mockResolvedValueOnce([makeRecord()])
      .mockResolvedValue([]);
    renderClient();
    await waitFor(() => {
      expect(screen.getByText(/offline/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /open menu/i }));
    await waitFor(() => {
      expect(
        screen.getByRole("menuitem", { name: /remove from offline/i })
      ).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("menuitem", { name: /remove from offline/i }));

    await waitFor(() => {
      expect(mockRemoveOfflineSongset).toHaveBeenCalledWith("songset-1");
    });
    await waitFor(() => {
      expect(screen.queryByText(/offline/i)).not.toBeInTheDocument();
    });
    expect(toastSuccess).toHaveBeenCalled();
    expect(toastError).not.toHaveBeenCalled();
  });

  it("toasts an error and keeps the badge when removal fails", async () => {
    mockListOfflineRecords.mockResolvedValue([makeRecord()]);
    mockRemoveOfflineSongset.mockRejectedValue(new Error("boom"));
    renderClient();
    await waitFor(() => {
      expect(screen.getByText(/offline/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /open menu/i }));
    await waitFor(() => {
      expect(
        screen.getByRole("menuitem", { name: /remove from offline/i })
      ).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("menuitem", { name: /remove from offline/i }));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    // Badge still present — the copy remains.
    expect(screen.getByText(/offline/i)).toBeInTheDocument();
  });

  // Fail toward offline (issue #211): probe unstubbed → Unknown, onLine
  // false → definitive Offline. Play must take the deterministic
  // full-document path, never SPA navigation.
  it("offline Play is a full document navigation", async () => {
    const locationAssignMock = vi.fn();
    Object.defineProperty(window, "location", {
      value: { assign: locationAssignMock },
      configurable: true,
    });
    const onLineDescriptor = Object.getOwnPropertyDescriptor(navigator, "onLine");
    Object.defineProperty(navigator, "onLine", { value: false, configurable: true });
    try {
      renderClient();
      const playButton = await screen.findByRole("button", { name: "Play" });
      fireEvent.click(playButton);
      expect(locationAssignMock).toHaveBeenCalledWith("/songsets/songset-1/play");
    } finally {
      if (onLineDescriptor) {
        Object.defineProperty(navigator, "onLine", onLineDescriptor);
      }
    }
  });
});
