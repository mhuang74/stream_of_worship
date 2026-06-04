import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { RenderForm, RenderFormData } from "@/components/render/RenderForm"

// Mock next/link
vi.mock("next/link", () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}))

describe("RenderForm", () => {
  const mockSubmit = vi.fn()
  const mockCancel = vi.fn()

  const defaultProps = {
    songsetId: "test-songset",
    onSubmit: mockSubmit,
    onCancel: mockCancel,
    isSubmitting: false,
  }

  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  describe("rendering", () => {
    it("renders output options section", () => {
      render(<RenderForm {...defaultProps} />)
      expect(screen.getByText("Output Options")).toBeInTheDocument()
      expect(screen.getByText("Audio (MP3)")).toBeInTheDocument()
      expect(screen.getByText("Video (MP4)")).toBeInTheDocument()
    })

    it("renders video settings when video is enabled", () => {
      render(<RenderForm {...defaultProps} />)
      expect(screen.getByText("Video Settings")).toBeInTheDocument()
      expect(screen.getByText("Template")).toBeInTheDocument()
      expect(screen.getByText("Resolution")).toBeInTheDocument()
      expect(screen.getByText("Font Size")).toBeInTheDocument()
      expect(screen.getByText("Font Family")).toBeInTheDocument()
    })

    it("renders title card section", () => {
      render(<RenderForm {...defaultProps} />)
      expect(screen.getByText("Title Card")).toBeInTheDocument()
      expect(screen.getByRole("checkbox", { name: /include title card/i })).toBeInTheDocument()
    })

    it("renders offline availability section", () => {
      render(<RenderForm {...defaultProps} />)
      expect(screen.getByText("Offline Availability")).toBeInTheDocument()
      expect(screen.getByRole("checkbox", { name: /make available offline/i })).toBeInTheDocument()
    })

    it("renders action buttons", () => {
      render(<RenderForm {...defaultProps} />)
      expect(screen.getByRole("button", { name: /cancel/i })).toBeInTheDocument()
      expect(screen.getByRole("button", { name: /start render/i })).toBeInTheDocument()
    })
  })

  describe("output options", () => {
    it("toggles audio enabled", async () => {
      render(<RenderForm {...defaultProps} />)
      
      const audioSwitch = screen.getByRole("switch", { name: /audio/i })
      expect(audioSwitch).toBeChecked()
      
      fireEvent.click(audioSwitch)
      expect(audioSwitch).not.toBeChecked()
    })

    it("toggles video enabled", async () => {
      render(<RenderForm {...defaultProps} />)
      
      const videoSwitch = screen.getByRole("switch", { name: /video/i })
      expect(videoSwitch).toBeChecked()
      
      fireEvent.click(videoSwitch)
      expect(videoSwitch).not.toBeChecked()
    })

    it("hides video settings when video is disabled", () => {
      render(<RenderForm {...defaultProps} />)
      
      const videoSwitch = screen.getByRole("switch", { name: /video/i })
      fireEvent.click(videoSwitch)
      
      expect(screen.queryByText("Video Settings")).not.toBeInTheDocument()
    })
  })

  describe("title card configuration", () => {
    it("shows duration selector when title card is enabled", async () => {
      render(<RenderForm {...defaultProps} />)
      
      const titleCardCheckbox = screen.getByRole("checkbox", { name: /include title card/i })
      fireEvent.click(titleCardCheckbox)
      
      await waitFor(() => {
        expect(screen.getByText("Duration")).toBeInTheDocument()
      })
    })

    it("hides duration selector when title card is disabled", () => {
      render(<RenderForm {...defaultProps} />)
      
      expect(screen.queryByText("Duration")).not.toBeInTheDocument()
    })
  })

  describe("marked lines warning", () => {
    it("shows warning when marked lines exist", () => {
      render(<RenderForm {...defaultProps} markedLineCount={3} />)
      
      expect(screen.getByText(/3 marked lines need attention/i)).toBeInTheDocument()
      expect(screen.getByRole("link", { name: /review/i })).toBeInTheDocument()
    })

    it("does not show warning when no marked lines", () => {
      render(<RenderForm {...defaultProps} markedLineCount={0} />)
      
      expect(screen.queryByText(/marked lines/i)).not.toBeInTheDocument()
    })

    it("uses singular form for one marked line", () => {
      render(<RenderForm {...defaultProps} markedLineCount={1} />)
      
      expect(screen.getByText(/1 marked line need attention/i)).toBeInTheDocument()
    })
  })

  describe("form submission", () => {
    it("submits form with default values", async () => {
      render(<RenderForm {...defaultProps} />)
      
      const submitButton = screen.getByRole("button", { name: /start render/i })
      fireEvent.click(submitButton)
      
      await waitFor(() => {
        expect(mockSubmit).toHaveBeenCalledWith(
          expect.objectContaining({
            audioEnabled: true,
            videoEnabled: true,
            template: "dark",
            resolution: "720p",
            fontSizePreset: "M",
            fontFamily: "noto_serif_tc",
            includeTitleCard: false,
            titleCardDurationSeconds: 10,
            offlineEnabled: false,
          })
        )
      })
    })

    it("disables submit when both audio and video are disabled", () => {
      render(<RenderForm {...defaultProps} />)
      
      const audioSwitch = screen.getByRole("switch", { name: /audio/i })
      const videoSwitch = screen.getByRole("switch", { name: /video/i })
      
      fireEvent.click(audioSwitch)
      fireEvent.click(videoSwitch)
      
      const submitButton = screen.getByRole("button", { name: /start render/i })
      expect(submitButton).toBeDisabled()
    })

    it("calls onCancel when cancel button clicked", () => {
      render(<RenderForm {...defaultProps} />)
      
      const cancelButton = screen.getByRole("button", { name: /cancel/i })
      fireEvent.click(cancelButton)
      
      expect(mockCancel).toHaveBeenCalled()
    })
  })

  describe("initial data", () => {
    it("uses initial data when provided", async () => {
      const initialData: Partial<RenderFormData> = {
        audioEnabled: false,
        videoEnabled: true,
        template: "gradient_warm",
        resolution: "1080p",
        fontSizePreset: "L",
        fontFamily: "lxgw_wenkai_tc",
        includeTitleCard: true,
        titleCardDurationSeconds: 15,
        offlineEnabled: true,
      }
      
      render(<RenderForm {...defaultProps} initialData={initialData} />)
      
      const submitButton = screen.getByRole("button", { name: /start render/i })
      fireEvent.click(submitButton)
      
      await waitFor(() => {
        expect(mockSubmit).toHaveBeenCalledWith(
          expect.objectContaining({
            audioEnabled: false,
            videoEnabled: true,
            template: "gradient_warm",
            resolution: "1080p",
            fontSizePreset: "L",
            fontFamily: "lxgw_wenkai_tc",
            includeTitleCard: true,
            titleCardDurationSeconds: 15,
            offlineEnabled: true,
          })
        )
      })
    })

    it("renders font preview text", () => {
      render(<RenderForm {...defaultProps} />)
      expect(screen.getByText("耶和華是我的牧者")).toBeInTheDocument()
      expect(screen.getByText("我必不至缺乏")).toBeInTheDocument()
    })
  })
})
