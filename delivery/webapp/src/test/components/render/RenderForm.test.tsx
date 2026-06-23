import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { RenderForm, RenderFormData } from "@/components/render/RenderForm"
import type { PreviousRenderJobData } from "@/components/render/RenderForm"

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

  describe("confirmation dialog with comparison table", () => {
    const previousJob: PreviousRenderJobData = {
      id: "prev-job-1",
      createdAt: new Date("2025-01-01T00:00:00Z").toISOString(),
      template: "dark",
      fontFamily: "noto_serif_tc",
      fontSizePreset: "M",
      includeTitleCard: false,
      titleCardDurationSeconds: 10,
      resolution: "720p",
      totalDurationSeconds: 785,
      songCount: 4,
      songsetDurationSeconds: 750,
    }

    it("shows comparison dialog when previousRenderJob is provided and form is submitted", async () => {
      render(
        <RenderForm
          {...defaultProps}
          previousRenderJob={previousJob}
          currentSongCount={4}
          currentSongsetDurationSeconds={750}
        />
      )

      const submitButton = screen.getByRole("button", { name: /start render/i })
      fireEvent.click(submitButton)

      await waitFor(() => {
        expect(screen.getByText("Start New Render?")).toBeInTheDocument()
      })
      expect(screen.getByText("Parameter")).toBeInTheDocument()
      expect(screen.getByText("Previous Render")).toBeInTheDocument()
      expect(screen.getByText("Current Request")).toBeInTheDocument()
    })

    it("shows diff highlighting for changed values", async () => {
      render(
        <RenderForm
          {...defaultProps}
          previousRenderJob={previousJob}
          currentSongCount={4}
          currentSongsetDurationSeconds={750}
          initialData={{ resolution: "1080p" }}
        />
      )

      const submitButton = screen.getByRole("button", { name: /start render/i })
      fireEvent.click(submitButton)

      await waitFor(() => {
        expect(screen.getByText("1080p (Full HD)")).toBeInTheDocument()
      })

      const resolutionRow = screen.getByText("1080p (Full HD)").closest("td")
      expect(resolutionRow?.className).toContain("amber")
    })

    it("does not show diff highlighting for same values", async () => {
      render(
        <RenderForm
          {...defaultProps}
          previousRenderJob={previousJob}
          currentSongCount={4}
          currentSongsetDurationSeconds={750}
        />
      )

      const submitButton = screen.getByRole("button", { name: /start render/i })
      fireEvent.click(submitButton)

      await waitFor(() => {
        const classicElements = screen.getAllByText("Classic")
        expect(classicElements.length).toBe(2)
      })

      const classicElements = screen.getAllByText("Classic")
      const currentCol = classicElements.find((el) => el.className.includes("pl-3"))
      expect(currentCol?.className).not.toContain("amber")
    })

    it("shows dash for null previous fields", async () => {
      const jobWithNulls: PreviousRenderJobData = {
        id: "prev-job-2",
        createdAt: new Date("2025-01-01T00:00:00Z").toISOString(),
        template: "dark",
        fontFamily: "noto_serif_tc",
        fontSizePreset: "M",
        includeTitleCard: false,
        songCount: null,
        songsetDurationSeconds: null,
        totalDurationSeconds: null,
        resolution: undefined,
      }

      render(
        <RenderForm
          {...defaultProps}
          previousRenderJob={jobWithNulls}
          currentSongCount={3}
          currentSongsetDurationSeconds={500}
        />
      )

      const submitButton = screen.getByRole("button", { name: /start render/i })
      fireEvent.click(submitButton)

      await waitFor(() => {
        const dashElements = screen.getAllByText("—")
        expect(dashElements.length).toBeGreaterThanOrEqual(2)
      })
    })

    it("submits directly without confirmation dialog when no previous render", async () => {
      render(<RenderForm {...defaultProps} />)

      const submitButton = screen.getByRole("button", { name: /start render/i })
      fireEvent.click(submitButton)

      await waitFor(() => {
        expect(mockSubmit).toHaveBeenCalled()
      })
      expect(screen.queryByText("Start New Render?")).not.toBeInTheDocument()
    })

    it("displays current song count and duration correctly", async () => {
      render(
        <RenderForm
          {...defaultProps}
          previousRenderJob={previousJob}
          currentSongCount={5}
          currentSongsetDurationSeconds={900}
        />
      )

      const submitButton = screen.getByRole("button", { name: /start render/i })
      fireEvent.click(submitButton)

      await waitFor(() => {
        expect(screen.getByText("5")).toBeInTheDocument()
      })
      expect(screen.getByText("15m 0s")).toBeInTheDocument()
    })

    it("shows estimated total duration with tilde prefix", async () => {
      render(
        <RenderForm
          {...defaultProps}
          previousRenderJob={previousJob}
          currentSongCount={4}
          currentSongsetDurationSeconds={750}
          initialData={{ includeTitleCard: true, titleCardDurationSeconds: 10 }}
        />
      )

      const submitButton = screen.getByRole("button", { name: /start render/i })
      fireEvent.click(submitButton)

      await waitFor(() => {
        expect(screen.getByText("~12m 40s")).toBeInTheDocument()
      })
    })

    it("formatDurationSafe returns dash for null/undefined/zero values", async () => {
      const jobWithNullDuration: PreviousRenderJobData = {
        id: "prev-job-3",
        createdAt: new Date("2025-01-01T00:00:00Z").toISOString(),
        template: "dark",
        fontFamily: "noto_serif_tc",
        fontSizePreset: "M",
        includeTitleCard: false,
        totalDurationSeconds: null,
        songsetDurationSeconds: null,
        songCount: null,
      }

      render(
        <RenderForm
          {...defaultProps}
          previousRenderJob={jobWithNullDuration}
          currentSongCount={0}
          currentSongsetDurationSeconds={null}
        />
      )

      const submitButton = screen.getByRole("button", { name: /start render/i })
      fireEvent.click(submitButton)

      await waitFor(() => {
        const dashElements = screen.getAllByText("—")
        expect(dashElements.length).toBeGreaterThanOrEqual(2)
      })
    })
  })
})
