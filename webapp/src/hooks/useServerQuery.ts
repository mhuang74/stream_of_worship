"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { fetchQuery, subscribe, getEntry, isStale, invalidateQuery } from "@/lib/query-client";

interface QueryState<T> {
  data: T | undefined;
  isLoading: boolean;
  isFetching: boolean;
  error: Error | null;
  refetch: () => Promise<void>;
}

/**
 * Hook for server state caching with stale-while-revalidate.
 * Caches responses by key, deduplicates concurrent requests,
 * and supports background refetching when data becomes stale.
 */
export function useServerQuery<T>(
  key: string | null,
  fetcher: () => Promise<T>,
  options: { staleTimeMs?: number; enabled?: boolean } = {}
): QueryState<T> {
  const { staleTimeMs = 60_000, enabled = true } = options;

  const [state, setState] = useState<{ data: T | undefined; isLoading: boolean; isFetching: boolean; error: Error | null }>(() => {
    if (!key || !enabled) return { data: undefined, isLoading: false, isFetching: false, error: null };
    const cached = getEntry<T>(key);
    const loading = !cached;
    return {
      data: cached?.data,
      isLoading: loading,
      isFetching: loading,
      error: null,
    };
  });

  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const doFetch = useCallback(async () => {
    if (!key) return;
    setState((prev) => ({ ...prev, isFetching: true, error: null }));
    try {
      const data = await fetchQuery<T>(key, () => fetcherRef.current(), staleTimeMs);
      setState({ data, isLoading: false, isFetching: false, error: null });
    } catch (err) {
      setState((prev) => ({
        ...prev,
        isLoading: false,
        isFetching: false,
        error: err instanceof Error ? err : new Error(String(err)),
      }));
    }
  }, [key, staleTimeMs]);

  useEffect(() => {
    if (!key || !enabled) return;

    const cached = getEntry<T>(key);
    if (cached) {
      setState({ data: cached.data, isLoading: false, isFetching: false, error: null });
      if (isStale(key)) doFetch();
    } else {
      doFetch();
    }

    return subscribe(key, () => {
      const entry = getEntry<T>(key);
      if (entry) setState((prev) => ({ ...prev, data: entry.data }));
    });
  }, [key, enabled, doFetch]);

  const refetch = useCallback(async () => {
    if (key) invalidateQuery(key);
    await doFetch();
  }, [key, doFetch]);

  return { ...state, refetch };
}
