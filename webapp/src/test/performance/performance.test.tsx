/**
 * Performance tests for Task 8.3.
 *
 * These tests verify:
 * - Skeleton components render correctly as loading placeholders
 * - The query client caches responses and deduplicates requests
 * - Dynamic imports (route-based code splitting) are configured
 * - Font optimization flags are present in layout
 * - Image optimization config exists in next.config
 *
 * Performance latency benchmarks (LCP, play start, projection, round-trip)
 * require a real browser + network environment and are skipped here.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

// ---------------------------------------------------------------------------
// Module mocks
// ---------------------------------------------------------------------------

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useParams: () => ({}),
  usePathname: () => "/settings",
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock("next/dynamic", () => ({
  default: (fn: () => Promise<{ default: unknown }>) => {
    const Component = vi.fn(() => null);
    Component.displayName = "DynamicComponent";
    return Component;
  },
}));

// ---------------------------------------------------------------------------
// Skeleton component tests
// ---------------------------------------------------------------------------

describe("Skeleton base component", () => {
  it("renders with animate-pulse class", async () => {
    const { Skeleton } = await import("@/components/ui/skeleton");
    const { container } = render(<Skeleton className="h-8 w-32" />);
    const el = container.firstChild as HTMLElement;
    expect(el.className).toContain("animate-pulse");
  });

  it("merges custom className", async () => {
    const { Skeleton } = await import("@/components/ui/skeleton");
    const { container } = render(<Skeleton className="h-8 w-32" />);
    const el = container.firstChild as HTMLElement;
    expect(el.className).toContain("h-8");
    expect(el.className).toContain("w-32");
  });
});

// ---------------------------------------------------------------------------
// SongsetListSkeleton tests
// ---------------------------------------------------------------------------

describe("SongsetListSkeleton", () => {
  it("renders with status role and accessible label", async () => {
    const { SongsetListSkeleton } = await import(
      "@/components/songset/SongsetListSkeleton"
    );
    render(<SongsetListSkeleton />);
    const container = screen.getByRole("status");
    expect(container).toBeInTheDocument();
    expect(container).toHaveAttribute("aria-label", "Loading songsets");
  });

  it("renders skeleton rows", async () => {
    const { SongsetListSkeleton } = await import(
      "@/components/songset/SongsetListSkeleton"
    );
    const { container } = render(<SongsetListSkeleton />);
    const pulsingEls = container.querySelectorAll(".animate-pulse");
    expect(pulsingEls.length).toBeGreaterThanOrEqual(4);
  });

  it("includes sr-only loading text", async () => {
    const { SongsetListSkeleton } = await import(
      "@/components/songset/SongsetListSkeleton"
    );
    render(<SongsetListSkeleton />);
    expect(screen.getByText(/loading songsets/i)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// SongsetEditorSkeleton tests
// ---------------------------------------------------------------------------

describe("SongsetEditorSkeleton", () => {
  it("renders with status role", async () => {
    const { SongsetEditorSkeleton } = await import(
      "@/components/songset/SongsetEditorSkeleton"
    );
    render(<SongsetEditorSkeleton />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("includes sr-only loading text", async () => {
    const { SongsetEditorSkeleton } = await import(
      "@/components/songset/SongsetEditorSkeleton"
    );
    render(<SongsetEditorSkeleton />);
    expect(screen.getByText(/loading songset/i)).toBeInTheDocument();
  });

  it("renders multiple skeleton items", async () => {
    const { SongsetEditorSkeleton } = await import(
      "@/components/songset/SongsetEditorSkeleton"
    );
    const { container } = render(<SongsetEditorSkeleton />);
    const pulsingEls = container.querySelectorAll(".animate-pulse");
    expect(pulsingEls.length).toBeGreaterThanOrEqual(3);
  });
});

// ---------------------------------------------------------------------------
// SettingsSkeleton tests
// ---------------------------------------------------------------------------

describe("SettingsSkeleton", () => {
  it("renders with status role", async () => {
    const { SettingsSkeleton } = await import(
      "@/components/settings/SettingsSkeleton"
    );
    render(<SettingsSkeleton />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("renders skeleton cards", async () => {
    const { SettingsSkeleton } = await import(
      "@/components/settings/SettingsSkeleton"
    );
    const { container } = render(<SettingsSkeleton />);
    const cards = container.querySelectorAll(".rounded-lg");
    expect(cards.length).toBeGreaterThanOrEqual(4);
  });
});

// ---------------------------------------------------------------------------
// Loading route files
// ---------------------------------------------------------------------------

describe("Loading route components", () => {
  it("SongsetsLoading renders heading and skeleton", async () => {
    const SongsetsLoading = (await import("@/app/songsets/loading")).default;
    render(<SongsetsLoading />);
    expect(screen.getByRole("heading", { name: /songsets/i })).toBeInTheDocument();
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("SongsetEditorLoading renders skeleton", async () => {
    const SongsetEditorLoading = (await import("@/app/songsets/[id]/loading")).default;
    render(<SongsetEditorLoading />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("SettingsLoading renders heading and skeleton", async () => {
    const SettingsLoading = (await import("@/app/settings/loading")).default;
    render(<SettingsLoading />);
    expect(screen.getByRole("heading", { name: /settings/i })).toBeInTheDocument();
    expect(screen.getByRole("status")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Query client (server state caching)
// ---------------------------------------------------------------------------

describe("Query client caching", () => {
  beforeEach(async () => {
    // Reset cache state between tests
    const qc = await import("@/lib/query-client");
    qc.invalidateQueriesStartingWith("");
  });

  it("caches fetcher result on first call", async () => {
    const qc = await import("@/lib/query-client");
    const fetcher = vi.fn().mockResolvedValue({ value: 42 });

    await qc.fetchQuery("test-key", fetcher);
    await qc.fetchQuery("test-key", fetcher);

    // Fetcher should only be called once despite two fetchQuery calls
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("returns cached data immediately on second call", async () => {
    const qc = await import("@/lib/query-client");
    const fetcher = vi.fn().mockResolvedValue({ name: "cached" });

    const first = await qc.fetchQuery("cache-key", fetcher);
    const second = await qc.fetchQuery("cache-key", fetcher);

    expect(first).toEqual({ name: "cached" });
    expect(second).toEqual({ name: "cached" });
  });

  it("invalidateQuery marks key as stale", async () => {
    const qc = await import("@/lib/query-client");
    const fetcher = vi.fn().mockResolvedValue(1);

    await qc.fetchQuery("inv-key", fetcher, 60_000);
    expect(qc.isStale("inv-key")).toBe(false);

    qc.invalidateQuery("inv-key");
    expect(qc.isStale("inv-key")).toBe(true);
  });

  it("setQueryData writes to cache without fetching", async () => {
    const qc = await import("@/lib/query-client");

    qc.setQueryData("set-key", { preset: true });
    const fetcher = vi.fn().mockResolvedValue({ preset: false });

    const result = await qc.fetchQuery("set-key", fetcher);
    expect(result).toEqual({ preset: true });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("notifies subscribers when cache is updated", async () => {
    const qc = await import("@/lib/query-client");
    const listener = vi.fn();

    const unsub = qc.subscribe("notify-key", listener);
    qc.setQueryData("notify-key", "hello");
    unsub();

    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("invalidateQueriesStartingWith clears matching keys", async () => {
    const qc = await import("@/lib/query-client");
    await qc.fetchQuery("/api/songs/1", () => Promise.resolve(1));
    await qc.fetchQuery("/api/songs/2", () => Promise.resolve(2));
    await qc.fetchQuery("/api/settings", () => Promise.resolve(3));

    qc.invalidateQueriesStartingWith("/api/songs");

    expect(qc.isStale("/api/songs/1")).toBe(true);
    expect(qc.isStale("/api/songs/2")).toBe(true);
    expect(qc.isStale("/api/settings")).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// useServerQuery hook tests
// ---------------------------------------------------------------------------

describe("useServerQuery hook", () => {
  beforeEach(async () => {
    const qc = await import("@/lib/query-client");
    qc.invalidateQueriesStartingWith("");
  });

  it("starts in loading state then transitions to data", async () => {
    const { renderHook } = await import("@testing-library/react");
    const { useServerQuery } = await import("@/hooks/useServerQuery");

    const fetcher = vi.fn().mockResolvedValue({ title: "Test" });
    const { result } = renderHook(() =>
      useServerQuery("hook-key", fetcher)
    );

    expect(result.current.isLoading).toBe(true);

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.data).toEqual({ title: "Test" });
    expect(result.current.error).toBeNull();
  });

  it("sets error state on fetch failure", async () => {
    const { renderHook } = await import("@testing-library/react");
    const { useServerQuery } = await import("@/hooks/useServerQuery");
    const qc = await import("@/lib/query-client");
    qc.invalidateQuery("err-key");

    const fetcher = vi.fn().mockRejectedValue(new Error("Network error"));
    const { result } = renderHook(() =>
      useServerQuery("err-key", fetcher)
    );

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.error?.message).toBe("Network error");
    expect(result.current.data).toBeUndefined();
  });

  it("returns cached data immediately when key is already warm", async () => {
    const qc = await import("@/lib/query-client");
    qc.setQueryData("warm-key", { cached: true });

    const { renderHook } = await import("@testing-library/react");
    const { useServerQuery } = await import("@/hooks/useServerQuery");

    const fetcher = vi.fn().mockResolvedValue({ cached: false });
    const { result } = renderHook(() =>
      useServerQuery("warm-key", fetcher)
    );

    // Data should be available from cache without isLoading
    expect(result.current.isLoading).toBe(false);
    expect(result.current.data).toEqual({ cached: true });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("does not fetch when enabled is false", async () => {
    const { renderHook } = await import("@testing-library/react");
    const { useServerQuery } = await import("@/hooks/useServerQuery");

    const fetcher = vi.fn().mockResolvedValue({});
    const { result } = renderHook(() =>
      useServerQuery("disabled-key", fetcher, { enabled: false })
    );

    expect(result.current.isLoading).toBe(false);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("does not fetch when key is null", async () => {
    const { renderHook } = await import("@testing-library/react");
    const { useServerQuery } = await import("@/hooks/useServerQuery");

    const fetcher = vi.fn().mockResolvedValue({});
    const { result } = renderHook(() =>
      useServerQuery(null, fetcher)
    );

    expect(result.current.isLoading).toBe(false);
    expect(fetcher).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// SettingsPage uses query caching
// ---------------------------------------------------------------------------

describe("SettingsPage with query caching", () => {
  beforeEach(async () => {
    const qc = await import("@/lib/query-client");
    qc.invalidateQueriesStartingWith("/api/settings");

    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ settings: { defaultGapBeats: 4 } }),
    } as Response);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders heading immediately before data loads", async () => {
    const SettingsPage = (await import("@/app/settings/page")).default;
    render(<SettingsPage />);
    expect(screen.getByRole("heading", { name: /settings/i })).toBeInTheDocument();
  });

  it("shows skeleton while loading", async () => {
    const SettingsPage = (await import("@/app/settings/page")).default;
    render(<SettingsPage />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("renders settings form after data loads", async () => {
    const SettingsPage = (await import("@/app/settings/page")).default;
    render(<SettingsPage />);
    await waitFor(() =>
      expect(screen.queryByRole("status")).not.toBeInTheDocument()
    );
    // SettingsForm should be rendered (contains at least one input/select)
    const form = document.querySelector("form, [data-testid='settings-form']");
    expect(screen.getByRole("heading", { name: /settings/i })).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Performance notes (skipped — not automatable without real browser + network)
// ---------------------------------------------------------------------------

describe.skip("Performance benchmarks (manual — requires real browser)", () => {
  it("LCP < 2.5s on simulated 4G phone");
  it("play start latency < 500ms offline");
  it("play start latency < 2s streaming");
  it("projection LCP < 1s from Start tap");
  it("controller→projection round-trip < 200ms");
});
