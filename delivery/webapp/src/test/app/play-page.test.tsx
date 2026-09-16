import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from "vitest";
import { screen, waitFor, act } from "@testing-library/react";
import { renderWithLocale as render } from "@/test/render";
import PlayPage from "@/app/songsets/[id]/play/page";

// Mock next/navigation. The router object must keep a stable identity across
// renders: the play page's load effect depends on it, and a fresh object per
// render re-runs the effect (exhausting any once-per-call fetch mock).
const mockPush = vi.fn();
const mockRouterInstance = { push: mockPush };
vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "test-songset" }),
  useRouter: () => mockRouterInstance,
}));

// The offline card reads the offline index; the page never touches IndexedDB
// in this suite.
const { mockGetOfflineRecord } = vi.hoisted(() => ({
  mockGetOfflineRecord: vi.fn(),
}));
vi.mock("@/lib/offline/offline-index", () => ({
  getOfflineRecord: mockGetOfflineRecord,
}));

// Mock PrePlayCard component
vi.mock("@/components/play/PrePlayCard", () => ({
  PrePlayCard: (props: {
    songset: { name: string };
    onStartWorship: () => void;
    onReRender: () => void;
    onShare: () => void;
  }) => (
    <div data-testid="pre-play-card">
      <div data-testid="songset-name">{props.songset.name}</div>
      <button data-testid="start-worship-btn" onClick={props.onStartWorship}>
        Start Worship
      </button>
      <button data-testid="re-render-btn" onClick={props.onReRender}>
        Re-render
      </button>
      <button data-testid="share-btn" onClick={props.onShare}>
        Share
      </button>
    </div>
  ),
}));

describe("PlayPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetOfflineRecord.mockResolvedValue(null);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  describe("loading state", () => {
    it("shows loading spinner while fetching data", () => {
      global.fetch = vi.fn().mockImplementation(() => new Promise(() => {}));

      render(<PlayPage />);

      expect(screen.getByRole("status")).toBeInTheDocument();
    });
  });

  describe("error state", () => {
    it("shows error message when fetch fails", async () => {
      global.fetch = vi.fn().mockRejectedValue(new Error("Network error"));

      render(<PlayPage />);

      await waitFor(() => {
        expect(screen.getByText(/network error/i)).toBeInTheDocument();
      });
    });

    it("shows back button on error", async () => {
      global.fetch = vi.fn().mockRejectedValue(new Error("Network error"));

      render(<PlayPage />);

      await waitFor(() => {
        expect(screen.getByRole("button", { name: /back to songsets/i })).toBeInTheDocument();
      });
    });
  });

  describe("zh-Hant localization", () => {
    it("renders translated Back to songsets button", async () => {
      global.fetch = vi.fn().mockRejectedValue(new Error("Network error"));

      render(<PlayPage />, "zh-Hant");

      await waitFor(() => {
        expect(screen.getByRole("button", { name: /返回歌單/i })).toBeInTheDocument();
      });
    });
  });

  describe("data loading", () => {
    it("redirects to login on 401", async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
      });

      render(<PlayPage />);

      await waitFor(() => {
        expect(mockPush).toHaveBeenCalledWith("/login");
      });
    });
  });

  describe("offline entry (issue #206)", () => {
    const OFFLINE_RECORD = {
      songsetId: "test-songset",
      renderJobId: "job-1",
      songsetName: "Downloaded Sunday Set",
      cachedMp3: false,
      cachedMp4: true,
      cachedChapters: true,
      cachedAt: "2026-09-15T10:00:00.000Z",
      chapterContentHashes: [],
    };

    let locationAssignMock: Mock;
    function stubLocation(): void {
      locationAssignMock = vi.fn();
      Object.defineProperty(window, "location", {
        value: { assign: locationAssignMock },
        configurable: true,
      });
    }

    function stubOnline(online: boolean): void {
      Object.defineProperty(navigator, "onLine", {
        value: online,
        configurable: true,
      });
    }

    it("shows the offline-available card when the songset fetch fails with a record", async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: false,
        status: 503,
        json: () => Promise.resolve({ error: "offline" }),
      });
      mockGetOfflineRecord.mockResolvedValue(OFFLINE_RECORD);

      render(<PlayPage />);

      await waitFor(() => {
        expect(screen.getByText("Downloaded Sunday Set")).toBeInTheDocument();
      });
      expect(screen.getByText(/ready for offline playback/i)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /start worship/i })).toBeInTheDocument();
      expect(mockPush).not.toHaveBeenCalled();
    });

    it("keeps the error screen for a genuine server 503 (online, not the SW offline answer)", async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: false,
        status: 503,
        json: () => Promise.resolve({ error: "Service Unavailable" }),
      });
      mockGetOfflineRecord.mockResolvedValue(OFFLINE_RECORD);

      render(<PlayPage />);

      await waitFor(() => {
        expect(screen.getByText(/failed to load songset/i)).toBeInTheDocument();
      });
      expect(screen.queryByText("Downloaded Sunday Set")).not.toBeInTheDocument();
      expect(screen.queryByText(/ready for offline playback/i)).not.toBeInTheDocument();
    });

    it("shows the card when the songset fetch rejects offline", async () => {
      global.fetch = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
      mockGetOfflineRecord.mockResolvedValue(OFFLINE_RECORD);

      render(<PlayPage />);

      await waitFor(() => {
        expect(screen.getByText("Downloaded Sunday Set")).toBeInTheDocument();
      });
    });

    it("Start Worship on the card is a full document navigation", async () => {
      stubLocation();
      global.fetch = vi.fn().mockResolvedValue({
        ok: false,
        status: 503,
        json: () => Promise.resolve({ error: "offline" }),
      });
      mockGetOfflineRecord.mockResolvedValue(OFFLINE_RECORD);

      render(<PlayPage />);

      await waitFor(() => {
        expect(screen.getByRole("button", { name: /start worship/i })).toBeInTheDocument();
      });
      act(() => {
        screen.getByRole("button", { name: /start worship/i }).click();
      });

      expect(locationAssignMock).toHaveBeenCalledWith("/songsets/test-songset/play/controller");
      expect(mockPush).not.toHaveBeenCalled();
    });

    it("keeps the error screen when no offline record exists", async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: false,
        status: 503,
        json: () => Promise.resolve({ error: "offline" }),
      });
      mockGetOfflineRecord.mockResolvedValue(null);

      render(<PlayPage />);

      await waitFor(() => {
        expect(screen.getByText(/failed to load songset/i)).toBeInTheDocument();
      });
      expect(screen.queryByText(/ready for offline playback/i)).not.toBeInTheDocument();
    });

    it("does not show the card on 401 — redirects to login instead", async () => {
      global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 401 });
      mockGetOfflineRecord.mockResolvedValue(OFFLINE_RECORD);

      render(<PlayPage />);

      await waitFor(() => {
        expect(mockPush).toHaveBeenCalledWith("/login");
      });
      expect(screen.queryByText("Downloaded Sunday Set")).not.toBeInTheDocument();
    });

    it("does not show the card on 404 — the server is reachable", async () => {
      global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 404 });
      mockGetOfflineRecord.mockResolvedValue(OFFLINE_RECORD);

      render(<PlayPage />);

      await waitFor(() => {
        expect(screen.getByText(/songset not found/i)).toBeInTheDocument();
      });
      expect(screen.queryByText("Downloaded Sunday Set")).not.toBeInTheDocument();
    });

    it("online Start Worship keeps SPA navigation", async () => {
      stubOnline(true);
      global.fetch = vi
        .fn()
        .mockResolvedValueOnce({
          ok: true,
          json: () =>
            Promise.resolve({
              id: "test-songset",
              name: "Sunday Set",
              description: null,
              renderState: "fresh",
              latestRenderJobId: "job-1",
              lastFailedRenderJobId: null,
              items: [],
            }),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: () =>
            Promise.resolve({
              id: "job-1",
              status: "completed",
              mp3R2Key: "audio/a.mp3",
              mp4R2Key: "video/a.mp4",
              chaptersR2Key: null,
            }),
        });

      render(<PlayPage />);

      await waitFor(() => {
        expect(screen.getByTestId("start-worship-btn")).toBeInTheDocument();
      });
      act(() => {
        screen.getByTestId("start-worship-btn").click();
      });

      expect(mockPush).toHaveBeenCalledWith("/songsets/test-songset/play/controller");
    });

    it("offline Start Worship is a full document navigation", async () => {
      stubLocation();
      stubOnline(false);
      global.fetch = vi
        .fn()
        .mockResolvedValueOnce({
          ok: true,
          json: () =>
            Promise.resolve({
              id: "test-songset",
              name: "Sunday Set",
              description: null,
              renderState: "fresh",
              latestRenderJobId: "job-1",
              lastFailedRenderJobId: null,
              items: [],
            }),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: () =>
            Promise.resolve({
              id: "job-1",
              status: "completed",
              mp3R2Key: "audio/a.mp3",
              mp4R2Key: "video/a.mp4",
              chaptersR2Key: null,
            }),
        });

      render(<PlayPage />);

      await waitFor(() => {
        expect(screen.getByTestId("start-worship-btn")).toBeInTheDocument();
      });
      act(() => {
        screen.getByTestId("start-worship-btn").click();
      });

      expect(locationAssignMock).toHaveBeenCalledWith("/songsets/test-songset/play/controller");
      expect(mockPush).not.toHaveBeenCalledWith("/songsets/test-songset/play/controller");
    });
  });

  describe("page rendering (skipped - requires complex fetch mocking)", () => {
    it.skip("renders PrePlayCard with loaded data", async () => {
      // Skipped due to complex fetch mocking requirements
    });

    it.skip("renders header with songset name", async () => {
      // Skipped due to complex fetch mocking requirements
    });
  });
});
