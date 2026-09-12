import { describe, it, expect, vi, beforeEach, waitFor } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useLyricsFeedback } from "@/hooks/useLyricsFeedback";

const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

describe("useLyricsFeedback", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("loads existing feedback on mount (GET)", async () => {
    mockFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ feedback: { rating: "sad", reason: "timing" } }), {
        status: 200,
      })
    );

    const { result } = renderHook(() => useLyricsFeedback("hash123"));

    await vi.waitFor(() => {
      expect(result.current.feedback).toEqual({ rating: "sad", reason: "timing" });
    });
    expect(mockFetch).toHaveBeenCalledWith("/api/lyrics/feedback/hash123", {
      signal: expect.anything(),
    });
  });

  it("starts with no feedback when GET returns null", async () => {
    mockFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ feedback: null }), { status: 200 })
    );

    const { result } = renderHook(() => useLyricsFeedback("hash123"));

    await vi.waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.feedback).toBeNull();
  });

  it("submitting happy stores rating happy with null reason", async () => {
    mockFetch
      .mockResolvedValueOnce(new Response(JSON.stringify({ feedback: null }), { status: 200 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ feedback: { rating: "happy", reason: null } }), {
          status: 200,
        })
      );

    const { result } = renderHook(() => useLyricsFeedback("hash123"));
    await vi.waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.submit("happy");
    });

    expect(result.current.feedback).toEqual({ rating: "happy", reason: null });
  });

  it("submitting sad with a reason records the reason", async () => {
    mockFetch
      .mockResolvedValueOnce(new Response(JSON.stringify({ feedback: null }), { status: 200 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ feedback: { rating: "sad", reason: "missing" } }), {
          status: 200,
        })
      );

    const { result } = renderHook(() => useLyricsFeedback("hash123"));
    await vi.waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.submit("sad", "missing");
    });

    expect(result.current.feedback).toEqual({ rating: "sad", reason: "missing" });
    expect(mockFetch).toHaveBeenLastCalledWith(
      "/api/lyrics/feedback/hash123",
      expect.objectContaining({ method: "PUT" })
    );
  });

  it("retract clears feedback via DELETE", async () => {
    mockFetch
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ feedback: { rating: "happy", reason: null } }), {
          status: 200,
        })
      )
      .mockResolvedValueOnce(new Response(JSON.stringify({ success: true }), { status: 200 }));

    const { result } = renderHook(() => useLyricsFeedback("hash123"));
    await vi.waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.retract();
    });

    expect(result.current.feedback).toBeNull();
    expect(mockFetch).toHaveBeenLastCalledWith(
      "/api/lyrics/feedback/hash123",
      expect.objectContaining({ method: "DELETE" })
    );
  });

  it("failed submit rolls back to previous state", async () => {
    mockFetch
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ feedback: { rating: "happy", reason: null } }), {
          status: 200,
        })
      )
      .mockResolvedValueOnce(new Response(JSON.stringify({ error: "invalid" }), { status: 400 }));

    const { result } = renderHook(() => useLyricsFeedback("hash123"));
    await vi.waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.submit("sad", "timing");
    });

    expect(result.current.feedback).toEqual({ rating: "happy", reason: null });
  });

  it("does not fetch when recordingContentHash is undefined", async () => {
    renderHook(() => useLyricsFeedback(undefined));
    await vi.waitFor(() => expect(mockFetch).not.toHaveBeenCalled());
  });
});