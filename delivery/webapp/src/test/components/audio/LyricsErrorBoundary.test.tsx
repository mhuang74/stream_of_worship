import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { LyricsErrorBoundary } from "@/components/audio/LyricsErrorBoundary";

function ThrowingChild() {
  throw new Error("Test error");
}

function NormalChild() {
  return <div data-testid="normal-child">Normal content</div>;
}

function Fallback() {
  return <div data-testid="fallback">Lyrics unavailable</div>;
}

describe("LyricsErrorBoundary", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  it("(a) when child throws during render, boundary renders fallback UI", () => {
    render(
      <LyricsErrorBoundary fallback={<Fallback />}>
        <ThrowingChild />
      </LyricsErrorBoundary>
    );

    expect(screen.getByTestId("fallback")).toBeInTheDocument();
    expect(screen.queryByTestId("normal-child")).not.toBeInTheDocument();
  });

  it("(b) when child renders normally, boundary renders children", () => {
    render(
      <LyricsErrorBoundary fallback={<Fallback />}>
        <NormalChild />
      </LyricsErrorBoundary>
    );

    expect(screen.getByTestId("normal-child")).toBeInTheDocument();
    expect(screen.queryByTestId("fallback")).not.toBeInTheDocument();
  });

  it("(c) boundary resets when remounted (error state does not persist across unmount/remount)", () => {
    const { unmount } = render(
      <LyricsErrorBoundary fallback={<Fallback />}>
        <ThrowingChild />
      </LyricsErrorBoundary>
    );

    expect(screen.getByTestId("fallback")).toBeInTheDocument();
    unmount();

    // Remount with a normal child — should render normally, not show fallback
    render(
      <LyricsErrorBoundary fallback={<Fallback />}>
        <NormalChild />
      </LyricsErrorBoundary>
    );

    expect(screen.getByTestId("normal-child")).toBeInTheDocument();
    expect(screen.queryByTestId("fallback")).not.toBeInTheDocument();
  });
});
