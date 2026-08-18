import { describe, it, expect, vi, beforeEach } from "vitest"
import { screen, fireEvent } from "@testing-library/react"
import { renderWithLocale as render } from "@/test/render"
import { RenderSubmitted } from "@/components/render/RenderSubmitted"

describe("RenderSubmitted", () => {
  const mockCancel = vi.fn()

  const defaultProps = {
    estimatedMinutes: 5,
    onCancel: mockCancel,
  }

  beforeEach(() => {
    vi.clearAllMocks()
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
      expect(screen.getByText("渲染已開始")).toBeInTheDocument()
      expect(screen.getByText(/約 5 分鐘/)).toBeInTheDocument()
      expect(screen.getByRole("button", { name: "取消渲染" })).toBeInTheDocument()
    })
  })
})
