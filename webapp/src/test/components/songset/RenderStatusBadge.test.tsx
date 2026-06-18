import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { RenderStatusBadge, RenderState } from "@/components/songset/RenderStatusBadge";

describe("RenderStatusBadge", () => {
  const renderBadge = (state: RenderState) => {
    return render(<RenderStatusBadge state={state} />);
  };

  describe("unrendered state", () => {
    it("renders 'Not rendered' label", () => {
      renderBadge("unrendered");
      expect(screen.getByText("Not rendered")).toBeInTheDocument();
    });
  });

  describe("rendering state", () => {
    it("renders 'Rendering' label", () => {
      renderBadge("rendering");
      expect(screen.getByText("Rendering")).toBeInTheDocument();
    });

    it("has spinning icon", () => {
      const { container } = renderBadge("rendering");
      expect(container.querySelector(".animate-spin")).toBeInTheDocument();
    });
  });

  describe("fresh state", () => {
    it("renders 'Rendered' label", () => {
      renderBadge("fresh");
      expect(screen.getByText("Rendered")).toBeInTheDocument();
    });
  });

  describe("stale state", () => {
    it("renders 'Needs re-render' label", () => {
      renderBadge("stale");
      expect(screen.getByText("Needs re-render")).toBeInTheDocument();
    });
  });

  describe("failed state", () => {
    it("renders 'Render failed' label", () => {
      renderBadge("failed");
      expect(screen.getByText("Render failed")).toBeInTheDocument();
    });

    it("shows tooltip on hover when errorMessage is provided", () => {
      render(
        <RenderStatusBadge
          state="failed"
          errorMessage="FFmpeg crashed"
          failedAt={new Date("2024-06-15T10:30:00Z")}
        />
      );
      const trigger = screen.getByRole("button");
      fireEvent.mouseEnter(trigger);
      expect(screen.getByText("FFmpeg crashed")).toBeInTheDocument();
    });

    it("shows tooltip on focus when errorMessage is provided", () => {
      render(
        <RenderStatusBadge
          state="failed"
          errorMessage="Out of memory"
          failedAt={null}
        />
      );
      const trigger = screen.getByRole("button");
      fireEvent.focus(trigger);
      expect(screen.getByText("Out of memory")).toBeInTheDocument();
    });

    it("shows fallback text in tooltip when errorMessage is null but failedAt exists", () => {
      render(
        <RenderStatusBadge
          state="failed"
          errorMessage={null}
          failedAt={new Date("2024-06-15T10:30:00Z")}
        />
      );
      const trigger = screen.getByRole("button");
      fireEvent.focus(trigger);
      expect(screen.getByText(/Render failed around/)).toBeInTheDocument();
      expect(screen.getByText(/Please render again/)).toBeInTheDocument();
    });

    it("shows generic fallback text when both errorMessage and failedAt are null", () => {
      render(
        <RenderStatusBadge
          state="failed"
          errorMessage={null}
          failedAt={null}
        />
      );
      const trigger = screen.getByRole("button");
      fireEvent.focus(trigger);
      expect(screen.getByText("Render failed. Please render again.")).toBeInTheDocument();
    });
  });

  describe("non-failed states", () => {
    it("does not create a tooltip for fresh state", () => {
      renderBadge("fresh");
      expect(screen.queryByRole("button")).not.toBeInTheDocument();
    });

    it("does not create a tooltip for unrendered state", () => {
      renderBadge("unrendered");
      expect(screen.queryByRole("button")).not.toBeInTheDocument();
    });

    it("does not create a tooltip for rendering state", () => {
      renderBadge("rendering");
      expect(screen.queryByRole("button")).not.toBeInTheDocument();
    });

    it("does not create a tooltip for stale state", () => {
      renderBadge("stale");
      expect(screen.queryByRole("button")).not.toBeInTheDocument();
    });
  });
});
