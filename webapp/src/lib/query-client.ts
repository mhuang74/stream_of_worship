/**
 * Lightweight in-memory query cache with stale-while-revalidate semantics.
 * Provides the same caching, deduplication, and background refetch benefits
 * as React Query without the external dependency.
 */

interface CacheEntry<T> {
  data: T;
  fetchedAt: number;
  staleTime: number;
}

interface InFlightRequest {
  promise: Promise<unknown>;
  subscribers: number;
}

const cache = new Map<string, CacheEntry<unknown>>();
const inFlight = new Map<string, InFlightRequest>();
const listeners = new Map<string, Set<() => void>>();

const DEFAULT_STALE_TIME_MS = 60_000; // 1 minute

function notify(key: string) {
  listeners.get(key)?.forEach((cb) => cb());
}

export function subscribe(key: string, cb: () => void) {
  if (!listeners.has(key)) listeners.set(key, new Set());
  listeners.get(key)!.add(cb);
  return () => listeners.get(key)?.delete(cb);
}

export function getEntry<T>(key: string): CacheEntry<T> | undefined {
  return cache.get(key) as CacheEntry<T> | undefined;
}

export function isStale(key: string): boolean {
  const entry = cache.get(key);
  if (!entry) return true;
  return Date.now() - entry.fetchedAt > entry.staleTime;
}

export async function fetchQuery<T>(
  key: string,
  fetcher: () => Promise<T>,
  staleTimeMs = DEFAULT_STALE_TIME_MS
): Promise<T> {
  if (!isStale(key)) {
    return (cache.get(key) as CacheEntry<T>).data;
  }

  const existing = inFlight.get(key);
  if (existing) {
    existing.subscribers++;
    return existing.promise as Promise<T>;
  }

  const promise = fetcher().then(
    (data) => {
      cache.set(key, { data, fetchedAt: Date.now(), staleTime: staleTimeMs });
      inFlight.delete(key);
      notify(key);
      return data;
    },
    (err) => {
      inFlight.delete(key);
      throw err;
    }
  );

  inFlight.set(key, { promise, subscribers: 1 });
  return promise;
}

export function invalidateQuery(key: string) {
  cache.delete(key);
  notify(key);
}

export function invalidateQueriesStartingWith(prefix: string) {
  for (const key of cache.keys()) {
    if (key.startsWith(prefix)) {
      cache.delete(key);
      notify(key);
    }
  }
}

export function setQueryData<T>(key: string, data: T, staleTimeMs = DEFAULT_STALE_TIME_MS) {
  cache.set(key, { data, fetchedAt: Date.now(), staleTime: staleTimeMs });
  notify(key);
}
