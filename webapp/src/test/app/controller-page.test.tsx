import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import ControllerPage from "@/app/songsets/[id]/play/controller/page";

// Mock next/navigation
const mockPush = vi.fn();
// Use a stable object so useRouter() returns the same reference on every render,
// preventing useEffect([songsetId, router]) from re-running on each re-render.
const mockRouterInstance = { push: mockPush };
vi.mock("next/navigation", () => ({
  useRouter: () => mockRouterInstance,
  useParams: () => ({ id: "test-songset" }),
}));

// Mock sonner toast
vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
  },
}));

// Mock ControllerPlayer component
vi.mock("@/components/play/ControllerPlayer", () => ({
  ControllerPlayer: (props: {
    songsetId: string;
    videoSrc: string;
    chapters: unknown[];
    isPresentationActive: boolean;
  }) => (
    <div data-testid="controller-player">
      <div data-testid="video-src">{props.videoSrc}</div>
      <div data-testid="chapters-count">{props.chapters.length}</div>
      <div data-testid="presentation-active">
        {props.isPresentationActive ? "true" : "false"}
      </div>
    </div>
  ),
}));

describe("ControllerPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("loading state", () => {
    it("shows loading spinner while fetching data", async () => {
      global.fetch = vi.fn().mockImplementation(() => new Promise(() => {}));

      render(<ControllerPage />);

      expect(screen.getByText(/loading player/i)).toBeInTheDocument();
    });
  });

  describe("error state", () => {
    it("shows error when songset not found", async () => {
      global.fetch = vi.fn().mockResolvedValueOnce({
        ok: false,
        status: 404,
      });

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByText(/songset not found/i)).toBeInTheDocument();
      });
    });

    it("shows error when render job not found", async () => {
      global.fetch = vi.fn().mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({
            id: "test-songset",
            name: "Test Songset",
            renderState: "unrendered",
            latestRenderJobId: null,
            lastFailedRenderJobId: null,
          }),
      });

      render(<ControllerPage />);

      await waitFor(() => {
        expect(
          screen.getByText(/songset has not been rendered yet/i)
        ).toBeInTheDocument();
      });
    });

    it("shows error when video not available", async () => {
      global.fetch = vi
        .fn()
        .mockResolvedValueOnce({
          ok: true,
          json: () =>
            Promise.resolve({
              id: "test-songset",
              name: "Test Songset",
              renderState: "fresh",
              latestRenderJobId: "job-1",
              lastFailedRenderJobId: null,
            }),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: () =>
            Promise.resolve({
              id: "job-1",
              status: "completed",
              mp4R2Key: null,
              chaptersR2Key: null,
            }),
        });

      render(<ControllerPage />);

      await waitFor(() => {
        expect(
          screen.getByText(/no video available for this songset/i)
        ).toBeInTheDocument();
      });
    });

    it("shows go back button on error", async () => {
      global.fetch = vi.fn().mockResolvedValueOnce({
        ok: false,
        status: 404,
      });

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByText(/go back/i)).toBeInTheDocument();
      });
    });
  });

  describe("success state", () => {
    it("renders ControllerPlayer when data loaded", async () => {
      global.fetch = vi
        .fn()
        .mockResolvedValueOnce({
          ok: true,
          json: () =>
            Promise.resolve({
              id: "test-songset",
              name: "Test Songset",
              renderState: "fresh",
              latestRenderJobId: "job-1",
              lastFailedRenderJobId: null,
            }),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: () =>
            Promise.resolve({
              id: "job-1",
              status: "completed",
              mp4R2Key: "videos/test.mp4",
              chaptersR2Key: null,
            }),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: () =>
            Promise.resolve({
              url: "https://r2.example.com/videos/test.mp4",
            }),
        });

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("controller-player")).toBeInTheDocument();
      });
    });

    it("passes video URL to ControllerPlayer", async () => {
      global.fetch = vi
        .fn()
        .mockResolvedValueOnce({
          ok: true,
          json: () =>
            Promise.resolve({
              id: "test-songset",
              name: "Test Songset",
              renderState: "fresh",
              latestRenderJobId: "job-1",
              lastFailedRenderJobId: null,
            }),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: () =>
            Promise.resolve({
              id: "job-1",
              status: "completed",
              mp4R2Key: "videos/test.mp4",
              chaptersR2Key: null,
            }),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: () =>
            Promise.resolve({
              url: "https://r2.example.com/videos/test.mp4",
            }),
        });

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("video-src")).toHaveTextContent(
          "https://r2.example.com/videos/test.mp4"
        );
      });
    });

    it("loads chapters when chaptersR2Key present", async () => {
      global.fetch = vi
        .fn()
        .mockResolvedValueOnce({
          ok: true,
          json: () =>
            Promise.resolve({
              id: "test-songset",
              name: "Test Songset",
              renderState: "fresh",
              latestRenderJobId: "job-1",
              lastFailedRenderJobId: null,
            }),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: () =>
            Promise.resolve({
              id: "job-1",
              status: "completed",
              mp4R2Key: "videos/test.mp4",
              chaptersR2Key: "chapters/test.json",
            }),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: () =>
            Promise.resolve({
              url: "https://r2.example.com/videos/test.mp4",
            }),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: () =>
            Promise.resolve({
              chapters: [
                {
                  position: 0,
                  songTitle: "Amazing Grace",
                  startSeconds: 0,
                  endSeconds: 180,
                  lines: [],
                },
              ],
            }),
        });

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("chapters-count")).toHaveTextContent("1");
      });
    });
  });

  describe("authentication", () => {
    it("redirects to login on 401", async () => {
      global.fetch = vi.fn().mockResolvedValueOnce({
        ok: false,
        status: 401,
      });

      render(<ControllerPage />);

      await waitFor(() => {
        expect(mockPush).toHaveBeenCalledWith("/login");
      });
    });
  });

  describe("presentation API messages", () => {
    beforeEach(() => {
      global.fetch = vi
        .fn()
        .mockResolvedValueOnce({
          ok: true,
          json: () =>
            Promise.resolve({
              id: "test-songset",
              name: "Test Songset",
              renderState: "fresh",
              latestRenderJobId: "job-1",
              lastFailedRenderJobId: null,
            }),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: () =>
            Promise.resolve({
              id: "job-1",
              status: "completed",
              mp4R2Key: "videos/test.mp4",
              chaptersR2Key: null,
            }),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: () =>
            Promise.resolve({
              url: "https://r2.example.com/videos/test.mp4",
            }),
        });
    });

    it("listens for presentation messages", async () => {
      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("controller-player")).toBeInTheDocument();
      });

      window.dispatchEvent(
        new MessageEvent("message", {
          data: { type: "presentation", action: "connected" },
        })
      );

      await waitFor(() => {
        expect(screen.getByTestId("presentation-active")).toHaveTextContent(
          "true"
        );
      });
    });

    it("handles presentation disconnected message", async () => {
      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("controller-player")).toBeInTheDocument();
      });

      window.dispatchEvent(
        new MessageEvent("message", {
          data: { type: "presentation", action: "connected" },
        })
      );

      await waitFor(() => {
        expect(screen.getByTestId("presentation-active")).toHaveTextContent(
          "true"
        );
      });

      window.dispatchEvent(
        new MessageEvent("message", {
          data: { type: "presentation", action: "disconnected" },
        })
      );

      await waitFor(() => {
        expect(screen.getByTestId("presentation-active")).toHaveTextContent(
          "false"
        );
      });
    });
  });
});
