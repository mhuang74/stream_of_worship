"use client";

import { useState, useEffect, useRef } from "react";
import type { RecordingSection } from "@/lib/arrangements";

export interface SongLyricsResult {
  lrcContent: string | null;
  lines: string[] | null;
  /**
   * The Recording's detected sections, from the lyrics endpoint. `null` while
   * loading, on error, without a Recording, and when the Recording has no
   * readable sections — every one of those renders a flat lyric list.
   */
  sections: RecordingSection[] | null;
  loading: boolean;
  error: string | null;
}

interface CachedResult {
  lrcContent: string | null;
  lines: string[] | null;
  sections: RecordingSection[] | null;
}

const lyricsCache = new Map<string, CachedResult>();

const MAX_CACHE_SIZE = 50;

const NULL_RESULT: SongLyricsResult = {
  lrcContent: null,
  lines: null,
  sections: null,
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
        sections: cached.sections,
        loading: false,
        error: null,
      };
    }
    return { lrcContent: null, lines: null, sections: null, loading: true, error: null };
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
        sections: cached.sections,
        loading: false,
        error: null,
      });
      return;
    }

    const abortController = new AbortController();
    abortControllerRef.current = abortController;

    setResult({ lrcContent: null, lines: null, sections: null, loading: true, error: null });

    fetch(`/api/lyrics/${recordingContentHash}`, { signal: abortController.signal })
      .then(async (res) => {
        if (!res.ok) {
          throw new Error(`Failed to fetch lyrics: ${res.status}`);
        }
        const data = (await res.json()) as CachedResult;
        if (abortController.signal.aborted) return;

        // A response without sections renders flat rather than guessing.
        const sections = Array.isArray(data.sections) ? data.sections : null;

        if (lyricsCache.size >= MAX_CACHE_SIZE) {
          const oldestKey = lyricsCache.keys().next().value;
          if (oldestKey !== undefined) {
            lyricsCache.delete(oldestKey);
          }
        }
        lyricsCache.set(recordingContentHash, {
          lrcContent: data.lrcContent,
          lines: data.lines,
          sections,
        });

        setResult({
          lrcContent: data.lrcContent,
          lines: data.lines,
          sections,
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
          sections: null,
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
