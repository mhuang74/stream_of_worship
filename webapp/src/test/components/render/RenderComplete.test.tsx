import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react"
import { RenderComplete } from "@/components/render/RenderComplete"

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    loading: vi.fn().mockReturnValue("toast-id"),
  },
}))

vi.mock("@/lib/download", () => ({
  sanitizeFilename: (name: string) => name.toLowerCase().replace(/\s+/g, "-"),
  downloadArtifact: vi.fn(),
}))

describe("RenderComplete", () => {
  const mockDone = vi.fn()
  const mockShare = vi.fn()

  const defaultProps = {
    jobId: "test-job-id",
    songsetId: "test-songset",
    songsetName: "Sunday Worship",
    hasAudio: false,
    hasVideo: false,
    hasChapters: false,
    onDone: mockDone,
    onShare: mockShare,
  }

  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(async () => {
    vi.clearAllMocks()
    
    fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({ url: "https://r2.example.com/signed-url" }),
    })
    
    vi.stubGlobal("fetch", fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  describe("rendering", () => {
    it("renders completion message", () => {
      render(<RenderComplete {...defaultProps} />)
      expect(screen.getByText("Render Complete!")).toBeInTheDocument()
      expect(screen.getByText(/Sunday Worship/)).toBeInTheDocument()
      expect(screen.getByText(/is ready for playback/)).toBeInTheDocument()
    })

    it("renders checkmark icon", () => {
      render(<RenderComplete {...defaultProps} />)
      expect(screen.getByText(/Render Complete!/)).toBeInTheDocument()
    })

    it("renders done button", () => {
      render(<RenderComplete {...defaultProps} />)
      expect(screen.getByRole("button", { name: /done/i })).toBeInTheDocument()
    })

    it("renders share button", () => {
      render(<RenderComplete {...defaultProps} />)
      expect(screen.getByRole("button", { name: /share songset/i })).toBeInTheDocument()
    })

    it("renders elapsed time when elapsedSeconds provided", () => {
      render(<RenderComplete {...defaultProps} elapsedSeconds={204} />)
      expect(screen.getByText(/Total time:/)).toBeInTheDocument()
      expect(screen.getByText(/3m 24s/)).toBeInTheDocument()
    })

    it("renders elapsed time in seconds when under a minute", () => {
      render(<RenderComplete {...defaultProps} elapsedSeconds={45} />)
      expect(screen.getByText(/Total time:/)).toBeInTheDocument()
      expect(screen.getByText(/45s/)).toBeInTheDocument()
    })

    it("does not render elapsed time when elapsedSeconds not provided", () => {
      render(<RenderComplete {...defaultProps} />)
      expect(screen.queryByText(/Total time:/)).not.toBeInTheDocument()
    })

    it("does not render elapsed time when elapsedSeconds is zero", () => {
      render(<RenderComplete {...defaultProps} elapsedSeconds={0} />)
      expect(screen.queryByText(/Total time:/)).not.toBeInTheDocument()
    })
  })

  describe("download buttons", () => {
    it("renders audio download button when hasAudio is true", () => {
      render(<RenderComplete {...defaultProps} hasAudio={true} />)
      expect(screen.getByRole("button", { name: /download audio/i })).toBeInTheDocument()
    })

    it("does not render audio button when hasAudio is false", () => {
      render(<RenderComplete {...defaultProps} />)
      expect(screen.queryByRole("button", { name: /download audio/i })).not.toBeInTheDocument()
    })

    it("renders video download button when hasVideo is true", () => {
      render(<RenderComplete {...defaultProps} hasVideo={true} />)
      expect(screen.getByRole("button", { name: /download video/i })).toBeInTheDocument()
    })

    it("does not render video button when hasVideo is false", () => {
      render(<RenderComplete {...defaultProps} />)
      expect(screen.queryByRole("button", { name: /download video/i })).not.toBeInTheDocument()
    })

    it("renders chapters download button when hasChapters is true", () => {
      render(<RenderComplete {...defaultProps} hasChapters={true} />)
      expect(screen.getByRole("button", { name: /download chapters/i })).toBeInTheDocument()
    })

    it("calls handleDownloadFile when audio button clicked", async () => {
      render(<RenderComplete {...defaultProps} hasAudio={true} />)
      
      const downloadButton = screen.getByRole("button", { name: /download audio/i })
      
      await act(async () => {
        fireEvent.click(downloadButton)
      })

      await waitFor(() => {
        expect(fetchMock).toHaveBeenCalled()
      })
    })

    it("shows error when signed URL fetch fails", async () => {
      const { toast } = await import("sonner")
      
      fetchMock.mockRejectedValue(new Error("Network error"))
      
      render(<RenderComplete {...defaultProps} hasAudio={true} />)
      
      const downloadButton = screen.getByRole("button", { name: /download audio/i })
      
      await act(async () => {
        fireEvent.click(downloadButton)
      })

      await waitFor(() => {
        expect(toast.error).toHaveBeenCalledWith("Download failed", { id: "toast-id" })
      })
    })
  })

  describe("actions", () => {
    it("calls onDone when done button clicked", () => {
      render(<RenderComplete {...defaultProps} />)
      
      const doneButton = screen.getByRole("button", { name: /done/i })
      fireEvent.click(doneButton)
      
      expect(mockDone).toHaveBeenCalled()
    })

    it("calls onShare when share button clicked", () => {
      render(<RenderComplete {...defaultProps} />)
      
      const shareButton = screen.getByRole("button", { name: /share songset/i })
      fireEvent.click(shareButton)
      
      expect(mockShare).toHaveBeenCalled()
    })

    it("uses Web Share API when available", async () => {
      const mockNavigatorShare = vi.fn().mockResolvedValue(undefined)
      Object.defineProperty(navigator, "share", {
        value: mockNavigatorShare,
        writable: true,
        configurable: true,
      })

      render(<RenderComplete {...defaultProps} hasAudio={true} />)
      
      const shareButton = screen.getByRole("button", { name: /share songset/i })
      fireEvent.click(shareButton)

      await waitFor(() => {
        expect(mockNavigatorShare).toHaveBeenCalledWith(
          expect.objectContaining({
            title: "Sunday Worship",
            text: expect.stringContaining("Sunday Worship"),
          })
        )
      })
    })
  })
})
