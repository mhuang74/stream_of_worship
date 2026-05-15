import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import ProjectionPage from "@/app/songsets/[id]/play/projection/page";

// Mock next/navigation
vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "test-songset" }),
  useRouter: () => ({
    push: vi.fn(),
  }),
}));

// Mock ProjectionPlayer to avoid complex video/hook mocking
vi.mock("@/components/play/ProjectionPlayer", () => ({
  ProjectionPlayer: (props: { videoSrc: string; initialSongTitle?: string }) => (
    <div data-testid="projection-player">
      <span data-testid="video-src">{props.videoSrc}</span>
      <span data-testid="initial-title">{props.initialSongTitle}</span>
    </div>
  ),
}));

describe("ProjectionPage", () => {
  const mockSongsetData = {
    id: "test-songset",
    name: "Morning Worship",
    latestRenderJobId: "job-123",
    renderState: "fresh",
  };

  const mockJobData = {
    id: "job-123",
    status: "completed",
    mp4R2Key: "renders/test-songset/video.mp4",
  };

  const mockSignedUrlData = {
    url: "https://cdn.example.com/video.mp4?signature=abc",
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("loading state", () => {
    it("shows loading spinner while fetching data", () => {
      global.fetch = vi.fn().mockImplementation(() => new Promise(() => {}));

      render(<ProjectionPage />);

      expect(screen.getByRole("status", { name: /loading projection/i })).toBeInTheDocument();
    });
  });

  describe("error states", () => {
    it("shows error when songset fetch fails", async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
      });

      render(<ProjectionPage />);

      await waitFor(() => {
        expect(screen.getByText(/failed to load songset/i)).toBeInTheDocument();
      });
    });

    it("shows error when no render artifacts available", async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: vi.fn().mockResolvedValue({
          ...mockSongsetData,
          latestRenderJobId: null,
        }),
      });

      render(<ProjectionPage />);

      await waitFor(() => {
        expect(screen.getByText(/no render artifacts available/i)).toBeInTheDocument();
      });
    });

    it("shows error when render job fetch fails", async () => {
      global.fetch = vi
        .fn()
        .mockResolvedValueOnce({
          ok: true,
          json: vi.fn().mockResolvedValue(mockSongsetData),
        })
        .mockResolvedValueOnce({
          ok: false,
          status: 500,
        });

      render(<ProjectionPage />);

      await waitFor(() => {
        expect(screen.getByText(/failed to load render job/i)).toBeInTheDocument();
      });
    });

    it("shows error when no video available", async () => {
      global.fetch = vi
        .fn()
        .mockResolvedValueOnce({
          ok: true,
          json: vi.fn().mockResolvedValue(mockSongsetData),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: vi.fn().mockResolvedValue({ ...mockJobData, mp4R2Key: null }),
        });

      render(<ProjectionPage />);

      await waitFor(() => {
        expect(screen.getByText(/no video available/i)).toBeInTheDocument();
      });
    });

    it("shows error when signed URL fetch fails", async () => {
      global.fetch = vi
        .fn()
        .mockResolvedValueOnce({
          ok: true,
          json: vi.fn().mockResolvedValue(mockSongsetData),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: vi.fn().mockResolvedValue(mockJobData),
        })
        .mockResolvedValueOnce({
          ok: false,
          status: 500,
        });

      render(<ProjectionPage />);

      await waitFor(() => {
        expect(screen.getByText(/failed to get video url/i)).toBeInTheDocument();
      });
    });

    it("shows error message on 401", async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
      });

      render(<ProjectionPage />);

      await waitFor(() => {
        expect(screen.getByText(/authentication required/i)).toBeInTheDocument();
      });
    });
  });

  describe("successful load", () => {
    beforeEach(() => {
      global.fetch = vi
        .fn()
        .mockResolvedValueOnce({
          ok: true,
          json: vi.fn().mockResolvedValue(mockSongsetData),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: vi.fn().mockResolvedValue(mockJobData),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: vi.fn().mockResolvedValue(mockSignedUrlData),
        });
    });

    it("renders ProjectionPlayer when data loads", async () => {
      render(<ProjectionPage />);

      await waitFor(() => {
        expect(screen.getByTestId("projection-player")).toBeInTheDocument();
      });
    });

    it("passes signed video URL to ProjectionPlayer", async () => {
      render(<ProjectionPage />);

      await waitFor(() => {
        expect(screen.getByTestId("video-src")).toHaveTextContent(
          "https://cdn.example.com/video.mp4?signature=abc"
        );
      });
    });

    it("passes songset name as initial title to ProjectionPlayer", async () => {
      render(<ProjectionPage />);

      await waitFor(() => {
        expect(screen.getByTestId("initial-title")).toHaveTextContent("Morning Worship");
      });
    });

    it("fetches signed URL with encoded R2 key", async () => {
      render(<ProjectionPage />);

      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalledWith(
          expect.stringContaining(encodeURIComponent("renders/test-songset/video.mp4"))
        );
      });
    });
  });

  describe("no chrome (lyrics-only)", () => {
    it("does not render app header", async () => {
      global.fetch = vi.fn().mockImplementation(() => new Promise(() => {}));
      render(<ProjectionPage />);

      // No header element
      expect(screen.queryByRole("banner")).not.toBeInTheDocument();
    });

    it("does not render navigation", async () => {
      global.fetch = vi.fn().mockImplementation(() => new Promise(() => {}));
      render(<ProjectionPage />);

      expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    });
  });
});
