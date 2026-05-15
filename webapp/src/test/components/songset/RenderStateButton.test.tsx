import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { RenderStateButton, RenderState } from "@/components/songset/RenderStateButton";

describe("RenderStateButton", () => {
  const renderButton = (props: {
    state: RenderState;
    progress?: number;
    onRender?: () => void;
    onPlay?: () => void;
    onRetry?: () => void;
  }) => {
    return render(<RenderStateButton {...props} />);
  };

  describe("unrendered state", () => {
    it("renders with 'Render' label", () => {
      renderButton({ state: "unrendered" });
      expect(screen.getByRole("button", { name: /render/i })).toBeInTheDocument();
    });

    it("calls onRender when clicked", () => {
      const onRender = vi.fn();
      renderButton({ state: "unrendered", onRender });
      fireEvent.click(screen.getByRole("button"));
      expect(onRender).toHaveBeenCalled();
    });

    it("has correct aria-label", () => {
      renderButton({ state: "unrendered" });
      expect(screen.getByRole("button")).toHaveAttribute("aria-label", "Render songset");
    });
  });

  describe("rendering state", () => {
    it("renders with progress percentage", () => {
      renderButton({ state: "rendering", progress: 45 });
      expect(screen.getByText(/rendering\.\.\. 45%/i)).toBeInTheDocument();
    });

    it("is disabled during rendering", () => {
      renderButton({ state: "rendering", progress: 50 });
      expect(screen.getByRole("button")).toBeDisabled();
    });

    it("has correct aria-label with progress", () => {
      renderButton({ state: "rendering", progress: 75 });
      expect(screen.getByRole("button")).toHaveAttribute("aria-label", "Rendering 75%");
    });
  });

  describe("fresh state", () => {
    it("renders with 'Play' label", () => {
      renderButton({ state: "fresh" });
      expect(screen.getByRole("button", { name: /play/i })).toBeInTheDocument();
    });

    it("calls onPlay when clicked", () => {
      const onPlay = vi.fn();
      renderButton({ state: "fresh", onPlay });
      fireEvent.click(screen.getByRole("button"));
      expect(onPlay).toHaveBeenCalled();
    });

    it("has correct aria-label", () => {
      renderButton({ state: "fresh" });
      expect(screen.getByRole("button")).toHaveAttribute("aria-label", "Play songset");
    });
  });

  describe("stale state", () => {
    it("renders with 'Re-render' label", () => {
      renderButton({ state: "stale" });
      expect(screen.getByRole("button", { name: /re-render/i })).toBeInTheDocument();
    });

    it("calls onRender when clicked", () => {
      const onRender = vi.fn();
      renderButton({ state: "stale", onRender });
      fireEvent.click(screen.getByRole("button"));
      expect(onRender).toHaveBeenCalled();
    });

    it("has correct aria-label", () => {
      renderButton({ state: "stale" });
      expect(screen.getByRole("button")).toHaveAttribute("aria-label", "Re-render songset");
    });
  });

  describe("failed state", () => {
    it("renders with 'Retry render' label", () => {
      renderButton({ state: "failed" });
      expect(screen.getByRole("button", { name: /retry render/i })).toBeInTheDocument();
    });

    it("calls onRetry when clicked", () => {
      const onRetry = vi.fn();
      renderButton({ state: "failed", onRetry });
      fireEvent.click(screen.getByRole("button"));
      expect(onRetry).toHaveBeenCalled();
    });

    it("has correct aria-label", () => {
      renderButton({ state: "failed" });
      expect(screen.getByRole("button")).toHaveAttribute("aria-label", "Retry render");
    });
  });

  describe("size variants", () => {
    it("renders with default size", () => {
      const { container } = render(<RenderStateButton state="unrendered" />);
      expect(container.querySelector("button")).toBeInTheDocument();
    });

    it("renders with sm size", () => {
      const { container } = render(<RenderStateButton state="unrendered" size="sm" />);
      expect(container.querySelector("button")).toBeInTheDocument();
    });
  });
});
