import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { screen, fireEvent, act } from "@testing-library/react"
import { renderWithLocale as render } from "@/test/render"
import { RenderSubmitted, RENDER_JOB_POLL_INTERVAL_MS } from "@/components/render/RenderSubmitted"

const POLL_INTERVAL_MS = RENDER_JOB_POLL_INTERVAL_MS

function jobResponse(status: string, overrides: Record<string, unknown> = {}) {
  return {
    ok: true,
    json: () =>
      Promise.resolve({
        id: "job-1",
        status,
        mp3R2Key: "renders/job-1/output.mp3",
        mp4R2Key: "renders/job-1/output.mp4",
        ...overrides,
      }),
  }
}

/** Stubs `fetch` with one response per poll, repeating the last one. */
function stubFetch(...responses: unknown[]) {
  const mock = vi.fn()
  for (const response of responses) {
    mock.mockResolvedValueOnce(response)
  }
  mock.mockResolvedValue(responses[responses.length - 1])
  vi.stubGlobal("fetch", mock)
  return mock
}

describe("RenderSubmitted", () => {
  const mockCancel = vi.fn()
  const mockComplete = vi.fn()
  const mockFailed = vi.fn()

  const defaultProps = {
    estimatedMinutes: 5,
    jobId: "job-1",
    onComplete: mockComplete,
    onFailed: mockFailed,
    onCancel: mockCancel,
  }

  beforeEach(() => {
    vi.clearAllMocks()
    stubFetch(jobResponse("running"))
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  describe("rendering", () => {
    it("renders 'Render Started' title", () => {
      render(<RenderSubmitted {...defaultProps} />)
      expect(screen.getByText("Render Started")).toBeInTheDocument()
    })

    it("renders estimated time", () => {
      render(<RenderSubmitted {...defaultProps} />)
      expect(screen.getByText(/~5 minutes/i)).toBeInTheDocument()
    })

    it("renders leave page message", () => {
      render(<RenderSubmitted {...defaultProps} />)
      expect(screen.getByText(/you can leave this page/i)).toBeInTheDocument()
    })

    it("renders cancel button", () => {
      render(<RenderSubmitted {...defaultProps} />)
      expect(screen.getByRole("button", { name: /cancel render/i })).toBeInTheDocument()
    })
  })

  describe("cancel functionality", () => {
    it("calls onCancel when cancel button clicked", () => {
      render(<RenderSubmitted {...defaultProps} />)
      fireEvent.click(screen.getByRole("button", { name: /cancel render/i }))
      expect(mockCancel).toHaveBeenCalled()
    })

    it("disables cancel button when isCancelling is true", () => {
      render(<RenderSubmitted {...defaultProps} isCancelling={true} />)
      expect(screen.getByRole("button", { name: /cancel render/i })).toBeDisabled()
    })
  })

  describe("estimated minutes", () => {
    it("renders different estimated minutes", () => {
      render(<RenderSubmitted estimatedMinutes={10} onCancel={mockCancel} />)
      expect(screen.getByText(/~10 minutes/i)).toBeInTheDocument()
    })
  })

  describe("zh-Hant", () => {
    it("renders zh-Hant title, estimate and cancel button", () => {
      render(<RenderSubmitted {...defaultProps} />, "zh-Hant")
      expect(screen.getByText("已開始渲染")).toBeInTheDocument()
      expect(screen.getByText(/約 5 分鐘/)).toBeInTheDocument()
      expect(screen.getByRole("button", { name: "取消渲染" })).toBeInTheDocument()
    })
  })

  describe("job polling", () => {
    it("re-reads the job every 10 seconds while it is running", async () => {
      vi.useFakeTimers()
      const fetchMock = stubFetch(jobResponse("running"))
      render(<RenderSubmitted {...defaultProps} />)

      expect(fetchMock).not.toHaveBeenCalled()

      await act(async () => {
        await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 2)
      })

      expect(fetchMock).toHaveBeenCalledTimes(2)
      expect(fetchMock).toHaveBeenCalledWith("/api/render-jobs/job-1")
      expect(mockComplete).not.toHaveBeenCalled()
      expect(mockFailed).not.toHaveBeenCalled()
    })

    it("reports completion once and stops polling", async () => {
      vi.useFakeTimers()
      const fetchMock = stubFetch(jobResponse("completed"))
      render(<RenderSubmitted {...defaultProps} />)

      await act(async () => {
        await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS)
      })

      expect(mockComplete).toHaveBeenCalledTimes(1)
      expect(mockComplete).toHaveBeenCalledWith(
        expect.objectContaining({ id: "job-1", status: "completed" })
      )

      await act(async () => {
        await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 3)
      })

      expect(fetchMock).toHaveBeenCalledTimes(1)
      expect(mockComplete).toHaveBeenCalledTimes(1)
    })

    it("reports a failed job and stops polling", async () => {
      vi.useFakeTimers()
      const fetchMock = stubFetch(jobResponse("failed"))
      render(<RenderSubmitted {...defaultProps} />)

      await act(async () => {
        await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS)
      })

      expect(mockFailed).toHaveBeenCalledTimes(1)
      expect(mockComplete).not.toHaveBeenCalled()

      await act(async () => {
        await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 3)
      })

      expect(fetchMock).toHaveBeenCalledTimes(1)
    })

    it("keeps polling after a failed poll request", async () => {
      vi.useFakeTimers()
      const fetchMock = vi.fn()
      fetchMock.mockRejectedValueOnce(new Error("network down"))
      fetchMock.mockResolvedValueOnce({ ok: false, status: 500, json: () => Promise.resolve({}) })
      fetchMock.mockResolvedValue(jobResponse("completed"))
      vi.stubGlobal("fetch", fetchMock)
      render(<RenderSubmitted {...defaultProps} />)

      await act(async () => {
        await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 3)
      })

      expect(fetchMock).toHaveBeenCalledTimes(3)
      expect(mockComplete).toHaveBeenCalledTimes(1)
    })

    it("stops polling on unmount", async () => {
      vi.useFakeTimers()
      const fetchMock = stubFetch(jobResponse("running"))
      const { unmount } = render(<RenderSubmitted {...defaultProps} />)

      await act(async () => {
        await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS)
      })
      expect(fetchMock).toHaveBeenCalledTimes(1)

      unmount()

      await act(async () => {
        await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 3)
      })

      expect(fetchMock).toHaveBeenCalledTimes(1)
    })

    it("stops polling when the job is gone", async () => {
      vi.useFakeTimers()
      const fetchMock = stubFetch({ ok: false, status: 404, json: () => Promise.resolve({}) })
      render(<RenderSubmitted {...defaultProps} />)

      await act(async () => {
        await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS)
      })

      expect(mockFailed).toHaveBeenCalledTimes(1)

      await act(async () => {
        await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 3)
      })

      expect(fetchMock).toHaveBeenCalledTimes(1)
    })

    it("stops polling without claiming failure when the session expired", async () => {
      vi.useFakeTimers()
      const fetchMock = stubFetch({ ok: false, status: 401, json: () => Promise.resolve({}) })
      render(<RenderSubmitted {...defaultProps} />)

      await act(async () => {
        await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS)
      })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 3)
      })

      expect(fetchMock).toHaveBeenCalledTimes(1)
      expect(mockFailed).not.toHaveBeenCalled()
      expect(mockComplete).not.toHaveBeenCalled()
    })

    it("ignores a completion that lands after unmount", async () => {
      vi.useFakeTimers()
      const { promise, resolve } = Promise.withResolvers<unknown>()
      const fetchMock = vi.fn().mockReturnValue(promise)
      vi.stubGlobal("fetch", fetchMock)

      const { unmount } = render(<RenderSubmitted {...defaultProps} />)

      await act(async () => {
        await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS)
      })
      expect(fetchMock).toHaveBeenCalledTimes(1)

      unmount()

      await act(async () => {
        resolve(jobResponse("completed"))
        await vi.advanceTimersByTimeAsync(0)
      })

      expect(mockComplete).not.toHaveBeenCalled()
    })
  })
})
