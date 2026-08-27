import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithLocale as render } from "@/test/render";

// Mock next/navigation
const mockPush = vi.fn();
const mockReplace = vi.fn();
const mockRouterInstance = { push: mockPush, replace: mockReplace };
vi.mock("next/navigation", () => ({
  useRouter: () => mockRouterInstance,
  useParams: () => ({ token: "share-tok" }),
}));

// Mock auth-client so the session state is controllable per test.
const { mockUseSession } = vi.hoisted(() => ({
  mockUseSession: vi.fn(),
}));
vi.mock("@/lib/auth-client", () => ({
  useSession: (...args: unknown[]) => mockUseSession(...args),
}));

import SharePage from "@/app/share/[token]/page";

const shareResponse = {
  token: "share-tok",
  shareType: "songset" as const,
  songset: {
    id: "ss-1",
    name: "Shared Set Name",
    description: null,
    totalDurationSeconds: 600,
    renderState: "fresh" as const,
    latestRenderJobId: "job-1",
    lastCompletedRenderJobId: "job-1",
  },
  items: [],
  playback: {
    selectedRenderJobId: "job-1",
    isStale: false,
    staleStatus: null,
    mp3Url: null,
    mp4Url: "https://r2.example.com/share/video.mp4",
    chaptersUrl: null,
    chaptersData: null,
    mp3SizeBytes: null,
    mp4SizeBytes: null,
  },
  allowDownload: false,
  createdAt: new Date().toISOString(),
  expiresAt: null,
};

describe("SharePage invite footer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(shareResponse),
    });
  });

  it("shows the invite footer to anonymous viewers", async () => {
    mockUseSession.mockReturnValue({ data: null, isPending: false });

    render(<SharePage />);

    await waitFor(() => {
      expect(screen.getByText("Enjoying this worship set?")).toBeInTheDocument();
    });
    expect(
      screen.getByText("Create your own seamless worship sets with Stream of Worship.")
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Learn more" })).toHaveAttribute("href", "/");
  });

  it("hides the invite footer from logged-in viewers", async () => {
    mockUseSession.mockReturnValue({
      data: { user: { id: "1", name: "Test User", email: "user@example.com" } },
      isPending: false,
    });

    render(<SharePage />);

    await waitFor(() => {
      expect(screen.getByTestId("play-button")).toBeInTheDocument();
    });
    expect(screen.queryByText("Enjoying this worship set?")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Learn more" })).not.toBeInTheDocument();
  });
});
