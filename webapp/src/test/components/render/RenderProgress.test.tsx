import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { RenderProgress, RenderPhase } from "@/components/render/RenderProgress"

class MockEventSource {
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: ((error: Event) => void) | null = null
  onopen: (() => void) | null = null
  close = vi.fn()
  
  constructor() {
    setTimeout(() => {
      if (this.onopen) this.onopen()
    }, 0)
  }
}

const eventSourceInstances: MockEventSource[] = []

function MockEventSourceConstructor(this: MockEventSource) {
  const instance = new MockEventSource()
  eventSourceInstances.push(instance)
  return instance
}

global.EventSource = MockEventSourceConstructor as unknown as typeof EventSource

describe("RenderProgress", () => {
  const mockComplete = vi.fn()
  const mockCancel = vi.fn()
  const mockError = vi.fn()

  const defaultProps = {
    jobId: "test-job-id",
    onComplete: mockComplete,
    onCancel: mockCancel,
    onError: mockError,
  }

  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    eventSourceInstances.length = 0

    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({
        id: "test-job-id",
        status: "running",
      }),
    })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  describe("rendering", () => {
    it("renders progress card", () => {
      render(<RenderProgress {...defaultProps} />)
      expect(screen.getByText("Rendering")).toBeInTheDocument()
    })

    it("renders phase indicator", () => {
      render(<RenderProgress {...defaultProps} />)
      expect(screen.getByText(/Step \d+ of \d+/)).toBeInTheDocument()
    })

    it("renders progress bar", () => {
      render(<RenderProgress {...defaultProps} />)
      const bar = document.querySelector(".h-2.w-full")
      expect(bar).toBeInTheDocument()
    })

    it("renders cancel button", () => {
      render(<RenderProgress {...defaultProps} />)
      const cancelButtons = screen.getAllByRole("button", { name: /cancel render/i })
      expect(cancelButtons.length).toBeGreaterThan(0)
    })
  })

  describe("SSE events", () => {
    it("updates progress on SSE message", async () => {
      vi.useRealTimers()
      
      render(<RenderProgress {...defaultProps} />)

      await waitFor(() => {
        expect(screen.getByText("Rendering")).toBeInTheDocument()
      })

      const instance = eventSourceInstances[0]
      expect(instance).toBeDefined()

      const progressData = {
        phase: "mixing_audio" as RenderPhase,
        phaseIndex: 1,
        totalPhases: 5,
        estimatedTotalSeconds: 180,
        elapsedSeconds: 30,
        status: "running" as const,
      }

      if (instance.onmessage) {
        instance.onmessage({
          data: JSON.stringify(progressData),
        } as MessageEvent)
      }

      await waitFor(() => {
        expect(screen.getByText("Mixing audio...")).toBeInTheDocument()
        expect(screen.getByText("30s")).toBeInTheDocument()
        expect(screen.getByText("~3m 0s")).toBeInTheDocument()
      })
    }, 10000)

    it("calls onComplete when status is completed", async () => {
      vi.useRealTimers()
      
      render(<RenderProgress {...defaultProps} />)

      await waitFor(() => {
        expect(screen.getByText("Rendering")).toBeInTheDocument()
      })

      const instance = eventSourceInstances[0]
      expect(instance).toBeDefined()

      const completedData = {
        phase: "completed" as RenderPhase,
        phaseIndex: 5,
        totalPhases: 5,
        estimatedTotalSeconds: 180,
        elapsedSeconds: 180,
        status: "completed" as const,
      }

      if (instance.onmessage) {
        instance.onmessage({
          data: JSON.stringify(completedData),
        } as MessageEvent)
      }

      await waitFor(() => {
        expect(mockComplete).toHaveBeenCalled()
      })
    }, 10000)

    it("shows error when status is failed", async () => {
      vi.useRealTimers()
      
      render(<RenderProgress {...defaultProps} />)

      await waitFor(() => {
        expect(screen.getByText("Rendering")).toBeInTheDocument()
      })

      const instance = eventSourceInstances[0]
      expect(instance).toBeDefined()

      const failedData = {
        phase: "encoding_video" as RenderPhase,
        phaseIndex: 3,
        totalPhases: 5,
        estimatedTotalSeconds: 180,
        elapsedSeconds: 120,
        status: "failed" as const,
        errorMessage: "Encoding failed",
      }

      if (instance.onmessage) {
        instance.onmessage({
          data: JSON.stringify(failedData),
        } as MessageEvent)
      }

      await waitFor(() => {
        expect(screen.getByText("Encoding failed")).toBeInTheDocument()
        expect(mockError).toHaveBeenCalledWith("Encoding failed")
      })
    }, 10000)

    it("calls onCancel when status is cancelled", async () => {
      vi.useRealTimers()
      
      render(<RenderProgress {...defaultProps} />)

      await waitFor(() => {
        expect(screen.getByText("Rendering")).toBeInTheDocument()
      })

      const instance = eventSourceInstances[0]
      expect(instance).toBeDefined()

      const cancelledData = {
        phase: "mixing_audio" as RenderPhase,
        phaseIndex: 1,
        totalPhases: 5,
        estimatedTotalSeconds: 180,
        elapsedSeconds: 30,
        status: "cancelled" as const,
      }

      if (instance.onmessage) {
        instance.onmessage({
          data: JSON.stringify(cancelledData),
        } as MessageEvent)
      }

      await waitFor(() => {
        expect(mockCancel).toHaveBeenCalled()
      })
    }, 10000)

    it("handles dynamic estimate adjustment when elapsed exceeds estimate", async () => {
      vi.useRealTimers()
      
      render(<RenderProgress {...defaultProps} />)

      await waitFor(() => {
        expect(screen.getByText("Rendering")).toBeInTheDocument()
      })

      const instance = eventSourceInstances[0]
      expect(instance).toBeDefined()

      const progressData = {
        phase: "encoding_video" as RenderPhase,
        phaseIndex: 3,
        totalPhases: 5,
        estimatedTotalSeconds: 100,
        elapsedSeconds: 150,
        status: "running" as const,
      }

      if (instance.onmessage) {
        instance.onmessage({
          data: JSON.stringify(progressData),
        } as MessageEvent)
      }

      await waitFor(() => {
        expect(screen.getByText("2m 30s")).toBeInTheDocument()
      })
    }, 10000)

    it("shows only elapsed time when estimatedTotalSeconds is 0", async () => {
      vi.useRealTimers()
      
      render(<RenderProgress {...defaultProps} />)

      await waitFor(() => {
        expect(screen.getByText("Rendering")).toBeInTheDocument()
      })

      const instance = eventSourceInstances[0]
      expect(instance).toBeDefined()

      const progressData = {
        phase: "preparing" as RenderPhase,
        phaseIndex: 0,
        totalPhases: 5,
        estimatedTotalSeconds: 0,
        elapsedSeconds: 10,
        status: "running" as const,
      }

      if (instance.onmessage) {
        instance.onmessage({
          data: JSON.stringify(progressData),
        } as MessageEvent)
      }

      await waitFor(() => {
        expect(screen.getByText("10s")).toBeInTheDocument()
        expect(screen.queryByText(/~/)).not.toBeInTheDocument()
      })
    }, 10000)
  })

  describe("cancel functionality", () => {
    it("calls onCancel when cancel button clicked", async () => {
      vi.useRealTimers()
      
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: vi.fn().mockResolvedValue({ status: "cancelled" }),
      })

      render(<RenderProgress {...defaultProps} />)

      await waitFor(() => {
        expect(screen.getByText("Rendering")).toBeInTheDocument()
      })

      const cancelButtons = screen.getAllByRole("button", { name: /cancel render/i })
      const footerCancelButton = cancelButtons[cancelButtons.length - 1]
      fireEvent.click(footerCancelButton)

      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalledWith(
          "/api/render-jobs/test-job-id",
          expect.objectContaining({ method: "DELETE" })
        )
      })

      await waitFor(() => {
        expect(mockCancel).toHaveBeenCalled()
      })
    }, 10000)

    it("shows loading state while cancelling", async () => {
      vi.useRealTimers()
      
      global.fetch = vi.fn().mockImplementation(() =>
        new Promise((resolve) => {
          setTimeout(() => {
            resolve({
              ok: true,
              json: vi.fn().mockResolvedValue({ status: "cancelled" }),
            })
          }, 100)
        })
      )

      render(<RenderProgress {...defaultProps} />)

      await waitFor(() => {
        expect(screen.getByText("Rendering")).toBeInTheDocument()
      })

      const cancelButtons = screen.getAllByRole("button", { name: /cancel render/i })
      const footerCancelButton = cancelButtons[cancelButtons.length - 1]
      fireEvent.click(footerCancelButton)

      await waitFor(() => {
        expect(screen.getByText(/Cancelling/i)).toBeInTheDocument()
      })
    }, 10000)
  })

  describe("error handling", () => {
    it("shows error when cancel fetch fails", async () => {
      vi.useRealTimers()
      
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: vi.fn().mockResolvedValue({
          id: "test-job-id",
          status: "running",
        }),
      })

      render(<RenderProgress {...defaultProps} />)

      await waitFor(() => {
        expect(screen.getByText("Rendering")).toBeInTheDocument()
      })

      global.fetch = vi.fn().mockRejectedValue(new Error("Network error"))

      const cancelButtons = screen.getAllByRole("button", { name: /cancel render/i })
      const footerCancelButton = cancelButtons[cancelButtons.length - 1]
      fireEvent.click(footerCancelButton)

      await waitFor(() => {
        expect(screen.getByText("Network error")).toBeInTheDocument()
      })
    }, 10000)

    it("calls onError when job status is failed", async () => {
      vi.useRealTimers()
      
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: vi.fn().mockResolvedValue({
          id: "test-job-id",
          status: "failed",
          errorMessage: "Render failed",
        }),
      })

      render(<RenderProgress {...defaultProps} />)

      await waitFor(() => {
        expect(mockError).toHaveBeenCalledWith("Render failed")
      })
    }, 10000)
  })

  describe("cleanup", () => {
    it("closes EventSource on unmount", async () => {
      vi.useRealTimers()
      
      const { unmount } = render(<RenderProgress {...defaultProps} />)
      
      await waitFor(() => {
        expect(screen.getByText("Rendering")).toBeInTheDocument()
      })
      
      const instance = eventSourceInstances[0]
      expect(instance).toBeDefined()
      
      unmount()

      expect(instance.close).toHaveBeenCalled()
    })
  })
})
