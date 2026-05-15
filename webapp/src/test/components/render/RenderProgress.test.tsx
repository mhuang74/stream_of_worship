import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { RenderProgress, RenderPhase } from "@/components/render/RenderProgress"

// Mock EventSource class
class MockEventSource {
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: ((error: Event) => void) | null = null
  onopen: (() => void) | null = null
  close = vi.fn()
  
  constructor() {
    // Trigger onopen immediately
    setTimeout(() => {
      if (this.onopen) this.onopen()
    }, 0)
  }
}

// Store instances for test access
const eventSourceInstances: MockEventSource[] = []

// Create a proper constructor mock
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
    // Clear the global instances array
    eventSourceInstances.length = 0

    // Mock fetch for initial status check
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
      expect(screen.getByText(/Overall progress/)).toBeInTheDocument()
    })

    it("renders time estimates", () => {
      render(<RenderProgress {...defaultProps} />)
      expect(screen.getByText("Elapsed")).toBeInTheDocument()
      expect(screen.getByText("Estimated remaining")).toBeInTheDocument()
    })

    it("renders cancel button", () => {
      render(<RenderProgress {...defaultProps} />)
      const cancelButtons = screen.getAllByRole("button", { name: /cancel render/i })
      expect(cancelButtons.length).toBeGreaterThan(0)
    })
  })

  describe("SSE events", () => {
    it("updates progress on SSE message", async () => {
      // Use real timers for this test
      vi.useRealTimers()
      
      render(<RenderProgress {...defaultProps} />)

      // Wait for component to render and EventSource to be created
      await waitFor(() => {
        expect(screen.getByText("Rendering")).toBeInTheDocument()
      })

      // Get the EventSource instance
      const instance = eventSourceInstances[0]
      expect(instance).toBeDefined()

      // Simulate SSE message
      const progressData = {
        phase: "mixing_audio" as RenderPhase,
        phaseIndex: 1,
        totalPhases: 5,
        percentComplete: 25,
        estimatedSecondsLeft: 120,
        elapsedSeconds: 30,
      }

      if (instance.onmessage) {
        instance.onmessage({
          data: JSON.stringify(progressData),
        } as MessageEvent)
      }

      await waitFor(() => {
        expect(screen.getByText("Mixing audio...")).toBeInTheDocument()
        expect(screen.getByText("25%")).toBeInTheDocument()
      })
    }, 10000)

    it("calls onComplete when phase is completed", async () => {
      // Use real timers for this test
      vi.useRealTimers()
      
      render(<RenderProgress {...defaultProps} />)

      // Wait for component to render and EventSource to be created
      await waitFor(() => {
        expect(screen.getByText("Rendering")).toBeInTheDocument()
      })

      const instance = eventSourceInstances[0]
      expect(instance).toBeDefined()

      const completedData = {
        phase: "completed" as RenderPhase,
        phaseIndex: 5,
        totalPhases: 5,
        percentComplete: 100,
        estimatedSecondsLeft: 0,
        elapsedSeconds: 180,
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
  })

  describe("cancel functionality", () => {
    it("calls onCancel when cancel button clicked", async () => {
      // Use real timers for this test
      vi.useRealTimers()
      
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: vi.fn().mockResolvedValue({ status: "cancelled" }),
      })

      render(<RenderProgress {...defaultProps} />)

      // Wait for component to render
      await waitFor(() => {
        expect(screen.getByText("Rendering")).toBeInTheDocument()
      })

      // Get the footer cancel button (not the header icon button)
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
      // Use real timers for this test
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

      // Wait for component to render
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
      // Use real timers for this test
      vi.useRealTimers()
      
      // First render with successful initial fetch
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: vi.fn().mockResolvedValue({
          id: "test-job-id",
          status: "running",
        }),
      })

      render(<RenderProgress {...defaultProps} />)

      // Wait for component to render
      await waitFor(() => {
        expect(screen.getByText("Rendering")).toBeInTheDocument()
      })

      // Then make the cancel request fail
      global.fetch = vi.fn().mockRejectedValue(new Error("Network error"))

      const cancelButtons = screen.getAllByRole("button", { name: /cancel render/i })
      const footerCancelButton = cancelButtons[cancelButtons.length - 1]
      fireEvent.click(footerCancelButton)

      await waitFor(() => {
        expect(screen.getByText("Network error")).toBeInTheDocument()
      })
    }, 10000)

    it("calls onError when job status is failed", async () => {
      // Use real timers for this test
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
      // Use real timers for this test
      vi.useRealTimers()
      
      const { unmount } = render(<RenderProgress {...defaultProps} />)
      
      // Wait for component to render and EventSource to be created
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
