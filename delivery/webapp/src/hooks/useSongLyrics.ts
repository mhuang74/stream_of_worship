"use client";

import { useState, useEffect, useRef } from "react";

export interface SongLyricsResult {
  lrcContent: string | null;
  lines: string[] | null;
  loading: boolean;
  error: string | null;
}

interface CachedResult {
  lrcContent: string | null;
  lines: string[] | null;
}

const lyricsCache = new Map<string, CachedResult>();

const MAX_CACHE_SIZE = 50;

const NULL_RESULT: SongLyricsResult = {
  lrcContent: null,
  lines: null,
  loading: false,
  error: null,
};

export function useSongLyrics(recordingContentHash: string | undefined): SongLyricsResult {
  const [result, setResult] = useState<SongLyricsResult>(() => {
    if (!recordingContentHash) return NULL_RESULT;
    const cached = lyricsCache.get(recordingContentHash);
    if (cached) {
      return {
        lrcContent: cached.lrcContent,
        lines: cached.lines,
        loading: false,
        error: null,
      };
    }
    return { lrcContent: null, lines: null, loading: true, error: null };
  });

  const abortControllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!recordingContentHash) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setResult(NULL_RESULT);
      return;
    }

    const cached = lyricsCache.get(recordingContentHash);
    if (cached) {
      setResult({
        lrcContent: cached.lrcContent,
        lines: cached.lines,
        loading: false,
        error: null,
      });
      return;
    }

    const abortController = new AbortController();
    abortControllerRef.current = abortController;

    setResult({ lrcContent: null, lines: null, loading: true, error: null });

    fetch(`/api/lyrics/${recordingContentHash}`, { signal: abortController.signal })
      .then(async (res) => {
        if (!res.ok) {
          throw new Error(`Failed to fetch lyrics: ${res.status}`);
        }
        const data = (await res.json()) as CachedResult;
        if (abortController.signal.aborted) return;

        if (lyricsCache.size >= MAX_CACHE_SIZE) {
          const oldestKey = lyricsCache.keys().next().value;
          if (oldestKey !== undefined) {
            lyricsCache.delete(oldestKey);
          }
        }
        lyricsCache.set(recordingContentHash, {
          lrcContent: data.lrcContent,
          lines: data.lines,
        });

        setResult({
          lrcContent: data.lrcContent,
          lines: data.lines,
          loading: false,
          error: null,
        });
      })
      .catch((err: unknown) => {
        if (abortController.signal.aborted) return;
        const message = err instanceof Error ? err.message : "Failed to load lyrics";
        setResult({
          lrcContent: null,
          lines: null,
          loading: false,
          error: message,
        });
      });

    return () => {
      abortController.abort();
    };
  }, [recordingContentHash]);

  return result;
}

export function clearLyricsCache(): void {
  lyricsCache.clear();
}
