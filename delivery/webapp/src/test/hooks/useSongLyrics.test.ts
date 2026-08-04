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

  describe("v6 LRU eviction", () => {
    it("evicts oldest entry when cache exceeds 50 entries", async () => {
      // Fill cache with 50 entries
      for (let i = 0; i < 50; i++) {
        const hash = `hash-${i}`;
        mockFetch.mockResolvedValue(
          new Response(JSON.stringify({ lrcContent: `lyrics-${i}`, lines: null }), {
            status: 200,
          })
        );
        const { result, unmount } = renderHook(() => useSongLyrics(hash));
        await waitFor(() => {
          expect(result.current.lrcContent).toBe(`lyrics-${i}`);
        });
        unmount();
        mockFetch.mockClear();
      }

      // Insert 51st entry — should evict hash-0 (oldest)
      mockFetch.mockResolvedValue(
        new Response(JSON.stringify({ lrcContent: "lyrics-50", lines: null }), {
          status: 200,
        })
      );
      const { result: result51, unmount: unmount51 } = renderHook(() => useSongLyrics("hash-50"));
      await waitFor(() => {
        expect(result51.current.lrcContent).toBe("lyrics-50");
      });
      unmount51();
      mockFetch.mockClear();

      // Accessing hash-0 should trigger a refetch (it was evicted)
      mockFetch.mockResolvedValue(
        new Response(JSON.stringify({ lrcContent: "lyrics-0-refetched", lines: null }), {
          status: 200,
        })
      );
      const { result: resultRefetch, unmount: unmountRefetch } = renderHook(() => useSongLyrics("hash-0"));
      expect(resultRefetch.current.loading).toBe(true);
      await waitFor(() => {
        expect(resultRefetch.current.lrcContent).toBe("lyrics-0-refetched");
      });
      unmountRefetch();
    });

    it("cache size stays at 50 after eviction", async () => {
      for (let i = 0; i < 51; i++) {
        const hash = `evict-hash-${i}`;
        mockFetch.mockResolvedValue(
          new Response(JSON.stringify({ lrcContent: `lyrics-${i}`, lines: null }), {
            status: 200,
          })
        );
        const { result, unmount } = renderHook(() => useSongLyrics(hash));
        await waitFor(() => {
          expect(result.current.lrcContent).toBe(`lyrics-${i}`);
        });
        unmount();
        mockFetch.mockClear();
      }

      // Accessing a cached entry should not refetch
      mockFetch.mockResolvedValue(
        new Response(JSON.stringify({ lrcContent: "should-not-be-called", lines: null }), {
          status: 200,
        })
      );
      const { result: resultCached, unmount: unmountCached } = renderHook(() => useSongLyrics("evict-hash-50"));
      expect(resultCached.current.loading).toBe(false);
      expect(resultCached.current.lrcContent).toBe("lyrics-50");
      expect(mockFetch).not.toHaveBeenCalled();
      unmountCached();
    });
  });
});
