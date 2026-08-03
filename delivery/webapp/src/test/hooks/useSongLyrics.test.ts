import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { useSongLyrics, clearLyricsCache } from "@/hooks/useSongLyrics";

const mockFetch = vi.fn();

vi.stubGlobal("fetch", mockFetch);

describe("useSongLyrics", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockFetch.mockReset();
    clearLyricsCache();
  });

  afterEach(() => {
    clearLyricsCache();
  });

  it("(f) recordingContentHash === undefined returns nulls with no fetch", () => {
    const { result } = renderHook(() => useSongLyrics(undefined));

    expect(result.current.lrcContent).toBeNull();
    expect(result.current.lines).toBeNull();
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeNull();
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("(a) loading → success transitions", async () => {
    mockFetch.mockResolvedValue(
      new Response(JSON.stringify({ lrcContent: "[00:01.00]Hello", lines: null }), {
        status: 200,
      })
    );

    const { result } = renderHook(() => useSongLyrics("hash123"));

    expect(result.current.loading).toBe(true);

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.lrcContent).toBe("[00:01.00]Hello");
    expect(result.current.lines).toBeNull();
    expect(result.current.error).toBeNull();
  });

  it("(b) error state", async () => {
    mockFetch.mockResolvedValue(
      new Response("Internal Server Error", { status: 500 })
    );

    const { result } = renderHook(() => useSongLyrics("hash-error"));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.error).not.toBeNull();
    expect(result.current.lrcContent).toBeNull();
    expect(result.current.lines).toBeNull();
  });

  it("(c) module-scoped cache: second expansion of same contentHash does not refetch", async () => {
    mockFetch.mockResolvedValue(
      new Response(JSON.stringify({ lrcContent: "cached lyrics", lines: null }), {
        status: 200,
      })
    );

    const { result, unmount } = renderHook(() => useSongLyrics("hash-cached"));

    await waitFor(() => {
      expect(result.current.lrcContent).toBe("cached lyrics");
    });

    unmount();

    mockFetch.mockClear();

    const { result: result2 } = renderHook(() => useSongLyrics("hash-cached"));

    expect(result2.current.lrcContent).toBe("cached lyrics");
    expect(result2.current.loading).toBe(false);
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("(d) cache survives unmount+remount within same session", async () => {
    mockFetch.mockResolvedValue(
      new Response(JSON.stringify({ lrcContent: null, lines: ["line1", "line2"] }), {
        status: 200,
      })
    );

    const { result, unmount } = renderHook(() => useSongLyrics("hash-survive"));

    await waitFor(() => {
      expect(result.current.lines).toEqual(["line1", "line2"]);
    });

    unmount();

    mockFetch.mockClear();

    const { result: result2 } = renderHook(() => useSongLyrics("hash-survive"));

    expect(result2.current.lines).toEqual(["line1", "line2"]);
    expect(result2.current.loading).toBe(false);
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("(e) abort on unmount or hash change", async () => {
    const abortSpy = vi.spyOn(AbortController.prototype, "abort");

    mockFetch.mockImplementation(
      (_url: string | URL | Request, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => {
            reject(new DOMException("Aborted", "AbortError"));
          });
        })
    );

    const { unmount } = renderHook(() => useSongLyrics("hash-abort-1"));

    unmount();
    expect(abortSpy).toHaveBeenCalled();

    abortSpy.mockClear();

    mockFetch.mockImplementation(
      (_url: string | URL | Request, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => {
            reject(new DOMException("Aborted", "AbortError"));
          });
        })
    );

    const { rerender, unmount: unmount2 } = renderHook(
      ({ hash }) => useSongLyrics(hash),
      { initialProps: { hash: "hash-abort-2" } }
    );

    rerender({ hash: "hash-abort-3" });
    expect(abortSpy).toHaveBeenCalled();

    unmount2();
    abortSpy.mockRestore();
    void rerender;
  });
});
