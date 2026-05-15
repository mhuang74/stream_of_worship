import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { RenderComplete } from "@/components/render/RenderComplete"

// Mock sonner toast
vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}))

describe("RenderComplete", () => {
  const mockDone = vi.fn()
  const mockShare = vi.fn()

  const defaultProps = {
    jobId: "test-job-id",
    songsetId: "test-songset",
    songsetName: "Sunday Worship",
    onDone: mockDone,
    onShare: mockShare,
  }

  beforeEach(() => {
    vi.clearAllMocks()
    
    // Mock fetch for downloads
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      blob: vi.fn().mockResolvedValue(new Blob()),
    })
    
    // Mock URL.createObjectURL
    global.URL.createObjectURL = vi.fn().mockReturnValue("blob:test")
    global.URL.revokeObjectURL = vi.fn()
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
  })

  describe("download buttons", () => {
    it("renders audio download button when mp3Url provided", () => {
      render(<RenderComplete {...defaultProps} mp3Url="https://r2.example.com/audio.mp3" />)
      expect(screen.getByRole("button", { name: /download audio/i })).toBeInTheDocument()
    })

    it("does not render audio button when mp3Url not provided", () => {
      render(<RenderComplete {...defaultProps} />)
      expect(screen.queryByRole("button", { name: /download audio/i })).not.toBeInTheDocument()
    })

    it("renders video download button when mp4Url provided", () => {
      render(<RenderComplete {...defaultProps} mp4Url="https://r2.example.com/video.mp4" />)
      expect(screen.getByRole("button", { name: /download video/i })).toBeInTheDocument()
    })

    it("does not render video button when mp4Url not provided", () => {
      render(<RenderComplete {...defaultProps} />)
      expect(screen.queryByRole("button", { name: /download video/i })).not.toBeInTheDocument()
    })

    it("renders chapters download button when chaptersUrl provided", () => {
      render(<RenderComplete {...defaultProps} chaptersUrl="https://r2.example.com/chapters.json" />)
      expect(screen.getByRole("button", { name: /download chapters/i })).toBeInTheDocument()
    })

    it("downloads audio when button clicked", async () => {
      const { toast } = await import("sonner")
      
      render(<RenderComplete {...defaultProps} mp3Url="https://r2.example.com/audio.mp3" />)
      
      const downloadButton = screen.getByRole("button", { name: /download audio/i })
      fireEvent.click(downloadButton)

      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalledWith("https://r2.example.com/audio.mp3")
        expect(toast.success).toHaveBeenCalledWith(expect.stringContaining("Downloaded"))
      })
    })

    it("shows error when download fails", async () => {
      const { toast } = await import("sonner")
      
      global.fetch = vi.fn().mockRejectedValue(new Error("Network error"))
      
      render(<RenderComplete {...defaultProps} mp3Url="https://r2.example.com/audio.mp3" />)
      
      const downloadButton = screen.getByRole("button", { name: /download audio/i })
      fireEvent.click(downloadButton)

      await waitFor(() => {
        expect(toast.error).toHaveBeenCalledWith("Download failed")
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
      const mockShare = vi.fn().mockResolvedValue(undefined)
      Object.defineProperty(navigator, "share", {
        value: mockShare,
        writable: true,
        configurable: true,
      })

      render(<RenderComplete {...defaultProps} mp3Url="https://r2.example.com/audio.mp3" />)
      
      const shareButton = screen.getByRole("button", { name: /share songset/i })
      fireEvent.click(shareButton)

      await waitFor(() => {
        expect(mockShare).toHaveBeenCalledWith(
          expect.objectContaining({
            title: "Sunday Worship",
            text: expect.stringContaining("Sunday Worship"),
          })
        )
      })
    })
  })
})
