import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
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
  });
});
