import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import RenderPage from "@/app/songsets/[id]/render/page"

const mockPush = vi.fn()
vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "test-songset" }),
  useRouter: () => ({
    push: mockPush,
  }),
}))

const mockSongsetData = {
  id: "test-songset",
  name: "Sunday Worship",
  description: "Easter service",
  items: [{ markedLineCount: 2 }, { markedLineCount: 1 }],
  latestRenderJobId: null,
  lastFailedRenderJobId: null,
  renderState: "unrendered",
}

const mockSettingsData = {
  settings: {
    userId: 1,
    offlineAutoCache: true,
    defaultGapBeats: 2.0,
    defaultVideoTemplate: "dark",
    defaultResolution: "720p",
    lyricsLoopWindowSeconds: 3.0,
    defaultFontSizePreset: "M",
    defaultFontFamily: "noto_serif_tc",
    defaultKeyShiftSemitones: 0,
    timingReviewFont: "sans",
  },
}

describe("RenderPage", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockPush.mockClear()
  })

  it("renders loading state initially", () => {
    global.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes("/api/songsets/")) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(mockSongsetData) })
      }
      if (url.includes("/api/settings")) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(mockSettingsData) })
      }
      return Promise.resolve({ ok: false, status: 404 })
    })
    render(<RenderPage />)
    expect(screen.getByRole("status")).toBeInTheDocument()
  })

  it("redirects to login when unauthorized", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
    })
    render(<RenderPage />)
    await waitFor(() => {
      expect(mockPush).toHaveBeenCalledWith("/login")
    }, { timeout: 10000 })
  }, 15000)

  it("shows error when songset not found", async () => {
    global.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes("/api/songsets/")) {
        return Promise.resolve({ ok: false, status: 404 })
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(mockSettingsData) })
    })
    render(<RenderPage />)
    await waitFor(() => {
      expect(screen.getByText(/songset not found/i)).toBeInTheDocument()
    }, { timeout: 10000 })
  }, 15000)
})
