import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
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
      const cancelButtons = screen.getAllByRole("button", { name: /cancel render/i })
      expect(cancelButtons.length).toBeGreaterThan(0)
    })
  })

  describe("cancel functionality", () => {
    it("calls onCancel when cancel button clicked", () => {
      render(<RenderSubmitted {...defaultProps} />)
      const cancelButtons = screen.getAllByRole("button", { name: /cancel render/i })
      fireEvent.click(cancelButtons[cancelButtons.length - 1])
      expect(mockCancel).toHaveBeenCalled()
    })

    it("disables cancel buttons when isCancelling is true", () => {
      render(<RenderSubmitted {...defaultProps} isCancelling={true} />)
      const cancelButtons = screen.getAllByRole("button", { name: /cancel render/i })
      cancelButtons.forEach((btn) => expect(btn).toBeDisabled())
    })
  })

  describe("estimated minutes", () => {
    it("renders different estimated minutes", () => {
      render(<RenderSubmitted estimatedMinutes={10} onCancel={mockCancel} />)
      expect(screen.getByText(/~10 minutes/i)).toBeInTheDocument()
    })
  })
})
