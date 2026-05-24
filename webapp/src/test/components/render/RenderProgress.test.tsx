import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react"
import { RenderProgress, RenderPhase } from "@/components/render/RenderProgress"

class MockEventSource {
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: ((error: Event) => void) | null = null
  onopen: (() => void) | null = null
  readyState: number = EventSource.CONNECTING
  close = vi.fn()

  constructor() {
    setTimeout(() => {
      this.readyState = EventSource.OPEN
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

const defaultJobResponse = {
  id: "test-job-id",
  status: "running",
  phase: "preparing",
  phaseIndex: 0,
  totalPhases: 5,
  estimatedTotalSeconds: 0,
  elapsedSeconds: 0,
}

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
    eventSourceInstances.length = 0

    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue(defaultJobResponse),
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

  describe("polling", () => {
    it("starts polling immediately on mount", async () => {
      render(<RenderProgress {...defaultProps} />)

      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalledWith(
          "/api/render-jobs/test-job-id",
          expect.objectContaining({ signal: expect.any(AbortSignal) })
        )
      })
    })

    it("updates progress from poll response", async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: vi.fn().mockResolvedValue({
          ...defaultJobResponse,
          phase: "mixing_audio",
          phaseIndex: 1,
          estimatedTotalSeconds: 180,
          elapsedSeconds: 30,
        }),
      })

      render(<RenderProgress {...defaultProps} />)

      await waitFor(() => {
        expect(screen.getByText("Mixing audio...")).toBeInTheDocument()
        expect(screen.getByText("30s")).toBeInTheDocument()
        expect(screen.getByText("~3m 0s")).toBeInTheDocument()
      })
    })

    it("calls onComplete when poll returns completed status", async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: vi.fn().mockResolvedValue({
          ...defaultJobResponse,
          status: "completed",
          phase: "completed",
          phaseIndex: 5,
        }),
      })

      render(<RenderProgress {...defaultProps} />)

      await waitFor(() => {
        expect(mockComplete).toHaveBeenCalled()
      })
    })

    it("shows error when poll returns failed status", async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: vi.fn().mockResolvedValue({
          ...defaultJobResponse,
          status: "failed",
          errorMessage: "Encoding failed",
        }),
      })

      render(<RenderProgress {...defaultProps} />)

      await waitFor(() => {
        expect(screen.getByText("Encoding failed")).toBeInTheDocument()
        expect(mockError).toHaveBeenCalledWith("Encoding failed")
      })
    })

    it("calls onCancel when poll returns cancelled status", async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: vi.fn().mockResolvedValue({
          ...defaultJobResponse,
          status: "cancelled",
        }),
      })

      render(<RenderProgress {...defaultProps} />)

      await waitFor(() => {
        expect(mockCancel).toHaveBeenCalled()
      })
    })

    it("handles dynamic estimate adjustment when elapsed exceeds estimate", async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: vi.fn().mockResolvedValue({
          ...defaultJobResponse,
          phase: "encoding_video",
          phaseIndex: 3,
          estimatedTotalSeconds: 100,
          elapsedSeconds: 150,
        }),
      })

      render(<RenderProgress {...defaultProps} />)

      await waitFor(() => {
        expect(screen.getByText("2m 30s")).toBeInTheDocument()
      })
    })

    it("shows only elapsed time when estimatedTotalSeconds is 0", async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: vi.fn().mockResolvedValue({
          ...defaultJobResponse,
          phase: "preparing",
          phaseIndex: 0,
          estimatedTotalSeconds: 0,
          elapsedSeconds: 10,
        }),
      })

      render(<RenderProgress {...defaultProps} />)

      await waitFor(() => {
        expect(screen.getByText("10s")).toBeInTheDocument()
        expect(screen.queryByText(/~/)).not.toBeInTheDocument()
      })
    })

    it("uses exponential backoff on poll failure", async () => {
      vi.useFakeTimers()

      const fetchMock = vi.fn()
      fetchMock.mockRejectedValue(new Error("Network error"))

      global.fetch = fetchMock

      render(<RenderProgress {...defaultProps} />)

      await act(async () => {
        await vi.runOnlyPendingTimersAsync()
      })

      const callsAfterInitial = fetchMock.mock.calls.length

      act(() => { vi.advanceTimersByTime(2000) })
      await act(async () => {
        await vi.runOnlyPendingTimersAsync()
      })

      const callsAfter2s = fetchMock.mock.calls.length
      expect(callsAfter2s).toBeGreaterThan(callsAfterInitial)

      act(() => { vi.advanceTimersByTime(4000) })
      await act(async () => {
        await vi.runOnlyPendingTimersAsync()
      })

      const callsAfter6s = fetchMock.mock.calls.length
      expect(callsAfter6s).toBeGreaterThan(callsAfter2s)
    })

    it("resets backoff on successful poll after failures", async () => {
      vi.useFakeTimers()

      const fetchMock = vi.fn()
      fetchMock.mockRejectedValueOnce(new Error("Network error"))
      fetchMock.mockResolvedValue({
        ok: true,
        json: vi.fn().mockResolvedValue(defaultJobResponse),
      })

      global.fetch = fetchMock

      render(<RenderProgress {...defaultProps} />)

      await act(async () => {
        await vi.runOnlyPendingTimersAsync()
      })

      const callsAfterInitial = fetchMock.mock.calls.length

      act(() => { vi.advanceTimersByTime(4000) })
      await act(async () => {
        await vi.runOnlyPendingTimersAsync()
      })

      const callsAfterBackoff = fetchMock.mock.calls.length
      expect(callsAfterBackoff).toBeGreaterThan(callsAfterInitial)

      act(() => { vi.advanceTimersByTime(2000) })
      await act(async () => {
        await vi.runOnlyPendingTimersAsync()
      })

      const callsAfterReset = fetchMock.mock.calls.length
      expect(callsAfterReset).toBeGreaterThan(callsAfterBackoff)
    })

    it("aborts fetch on unmount", () => {
      const abortSpy = vi.spyOn(AbortController.prototype, "abort")

      const { unmount } = render(<RenderProgress {...defaultProps} />)

      unmount()

      expect(abortSpy).toHaveBeenCalled()
    })
  })

  describe("SSE enhancement", () => {
    it("attempts SSE connection on mount", async () => {
      render(<RenderProgress {...defaultProps} />)

      await waitFor(() => {
        expect(eventSourceInstances.length).toBeGreaterThan(0)
      })
    })

    it("reduces polling frequency when SSE connects", async () => {
      const fetchMock = vi.fn().mockResolvedValue({
        ok: true,
        json: vi.fn().mockResolvedValue(defaultJobResponse),
      })
      global.fetch = fetchMock

      render(<RenderProgress {...defaultProps} />)

      await waitFor(() => {
        expect(eventSourceInstances.length).toBeGreaterThan(0)
      })

      const instance = eventSourceInstances[eventSourceInstances.length - 1]
      act(() => {
        if (instance.onopen) instance.onopen()
      })

      const pollCountAfterSSE = fetchMock.mock.calls.length

      await act(async () => {
        await new Promise((r) => setTimeout(r, 100))
      })

      const pollCountLater = fetchMock.mock.calls.length
      expect(pollCountLater - pollCountAfterSSE).toBeLessThanOrEqual(1)
    })

    it("restores fast polling when SSE drops", async () => {
      const fetchMock = vi.fn().mockResolvedValue({
        ok: true,
        json: vi.fn().mockResolvedValue(defaultJobResponse),
      })
      global.fetch = fetchMock

      render(<RenderProgress {...defaultProps} />)

      await waitFor(() => {
        expect(eventSourceInstances.length).toBeGreaterThan(0)
      })

      const instance = eventSourceInstances[eventSourceInstances.length - 1]
      act(() => {
        if (instance.onopen) instance.onopen()
      })

      const pollCountBefore = fetchMock.mock.calls.length

      act(() => {
        if (instance.onerror) instance.onerror({} as Event)
      })

      expect(instance.close).toHaveBeenCalled()

      await act(async () => {
        await new Promise((r) => setTimeout(r, 3000))
      })

      const pollCountAfter = fetchMock.mock.calls.length
      expect(pollCountAfter - pollCountBefore).toBeGreaterThanOrEqual(1)
    })

    it("updates progress from SSE message", async () => {
      render(<RenderProgress {...defaultProps} />)

      await waitFor(() => {
        expect(eventSourceInstances.length).toBeGreaterThan(0)
      })

      const instance = eventSourceInstances[eventSourceInstances.length - 1]

      const progressData = {
        phase: "mixing_audio" as RenderPhase,
        phaseIndex: 1,
        totalPhases: 5,
        estimatedTotalSeconds: 180,
        elapsedSeconds: 30,
        status: "running" as const,
      }

      act(() => {
        if (instance.onmessage) {
          instance.onmessage({
            data: JSON.stringify(progressData),
          } as MessageEvent)
        }
      })

      await waitFor(() => {
        expect(screen.getByText("Mixing audio...")).toBeInTheDocument()
        expect(screen.getByText("30s")).toBeInTheDocument()
      })
    })

    it("calls onComplete when SSE sends completed status", async () => {
      render(<RenderProgress {...defaultProps} />)

      await waitFor(() => {
        expect(eventSourceInstances.length).toBeGreaterThan(0)
      })

      const instance = eventSourceInstances[eventSourceInstances.length - 1]

      const completedData = {
        phase: "completed" as RenderPhase,
        phaseIndex: 5,
        totalPhases: 5,
        estimatedTotalSeconds: 180,
        elapsedSeconds: 180,
        status: "completed" as const,
      }

      act(() => {
        if (instance.onmessage) {
          instance.onmessage({
            data: JSON.stringify(completedData),
          } as MessageEvent)
        }
      })

      await waitFor(() => {
        expect(mockComplete).toHaveBeenCalled()
        expect(instance.close).toHaveBeenCalled()
      })
    })

    it("shows error when SSE sends failed status", async () => {
      render(<RenderProgress {...defaultProps} />)

      await waitFor(() => {
        expect(eventSourceInstances.length).toBeGreaterThan(0)
      })

      const instance = eventSourceInstances[eventSourceInstances.length - 1]

      const failedData = {
        phase: "encoding_video" as RenderPhase,
        phaseIndex: 3,
        totalPhases: 5,
        estimatedTotalSeconds: 180,
        elapsedSeconds: 120,
        status: "failed" as const,
        errorMessage: "Encoding failed",
      }

      act(() => {
        if (instance.onmessage) {
          instance.onmessage({
            data: JSON.stringify(failedData),
          } as MessageEvent)
        }
      })

      await waitFor(() => {
        expect(screen.getByText("Encoding failed")).toBeInTheDocument()
        expect(mockError).toHaveBeenCalledWith("Encoding failed")
        expect(instance.close).toHaveBeenCalled()
      })
    })

    it("calls onCancel when SSE sends cancelled status", async () => {
      render(<RenderProgress {...defaultProps} />)

      await waitFor(() => {
        expect(eventSourceInstances.length).toBeGreaterThan(0)
      })

      const instance = eventSourceInstances[eventSourceInstances.length - 1]

      const cancelledData = {
        phase: "mixing_audio" as RenderPhase,
        phaseIndex: 1,
        totalPhases: 5,
        estimatedTotalSeconds: 180,
        elapsedSeconds: 30,
        status: "cancelled" as const,
      }

      act(() => {
        if (instance.onmessage) {
          instance.onmessage({
            data: JSON.stringify(cancelledData),
          } as MessageEvent)
        }
      })

      await waitFor(() => {
        expect(mockCancel).toHaveBeenCalled()
        expect(instance.close).toHaveBeenCalled()
      })
    })
  })

  describe("cancel functionality", () => {
    it("calls onCancel when cancel button clicked", async () => {
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

    it("closes SSE connection when cancelling", async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: vi.fn().mockResolvedValue({ status: "cancelled" }),
      })

      render(<RenderProgress {...defaultProps} />)

      await waitFor(() => {
        expect(eventSourceInstances.length).toBeGreaterThan(0)
      })

      const instance = eventSourceInstances[eventSourceInstances.length - 1]

      const cancelButtons = screen.getAllByRole("button", { name: /cancel render/i })
      const footerCancelButton = cancelButtons[cancelButtons.length - 1]
      fireEvent.click(footerCancelButton)

      await waitFor(() => {
        expect(instance.close).toHaveBeenCalled()
      })
    }, 10000)

    it("shows loading state while cancelling", async () => {
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
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: vi.fn().mockResolvedValue(defaultJobResponse),
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
    })

    it("calls onError when poll returns failed status", async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: vi.fn().mockResolvedValue({
          ...defaultJobResponse,
          status: "failed",
          errorMessage: "Render failed",
        }),
      })

      render(<RenderProgress {...defaultProps} />)

      await waitFor(() => {
        expect(mockError).toHaveBeenCalledWith("Render failed")
      })
    })
  })

  describe("cleanup", () => {
    it("closes EventSource on unmount", async () => {
      const { unmount } = render(<RenderProgress {...defaultProps} />)

      await waitFor(() => {
        expect(eventSourceInstances.length).toBeGreaterThan(0)
      })

      const instance = eventSourceInstances[eventSourceInstances.length - 1]

      unmount()

      expect(instance.close).toHaveBeenCalled()
    })

    it("aborts in-flight fetch on unmount", () => {
      const abortSpy = vi.spyOn(AbortController.prototype, "abort")

      const { unmount } = render(<RenderProgress {...defaultProps} />)

      unmount()

      expect(abortSpy).toHaveBeenCalled()
    })
  })
})
