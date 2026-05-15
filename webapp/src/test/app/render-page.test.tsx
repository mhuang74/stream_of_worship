import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import RenderPage from "@/app/songsets/[id]/render/page"

// Mock next/navigation
const mockPush = vi.fn()
vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "test-songset" }),
  useRouter: () => ({
    push: mockPush,
  }),
}))

describe("RenderPage", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockPush.mockClear()
    
    // Mock fetch with immediate resolution
    global.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes("/api/songsets/")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              id: "test-songset",
              name: "Sunday Worship",
              description: "Easter service",
              items: [
                { markedLineCount: 2 },
                { markedLineCount: 1 },
              ],
              latestRenderJobId: null,
              lastFailedRenderJobId: null,
            }),
        })
      }
      if (url.includes("/api/render-jobs")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              id: "test-job",
              status: "queued",
            }),
        })
      }
      return Promise.resolve({ ok: false, status: 404 })
    })
  })

  it("renders loading state initially", () => {
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
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
    })
    
    render(<RenderPage />)
    
    await waitFor(() => {
      expect(screen.getByText(/songset not found/i)).toBeInTheDocument()
    }, { timeout: 10000 })
  }, 15000)
})
