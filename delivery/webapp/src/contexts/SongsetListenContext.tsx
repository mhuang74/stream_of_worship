"use client";

/**
 * Audition transport for a whole Songset.
 *
 * One provider lives in the root layout, so an audition survives client-side
 * navigation while a full document load (offline controller boot, share links)
 * still tears it down. It owns the request, the one-shot metadata GET, and the
 * `useSongsetAudio` transport; the bar (`SongsetPlayerBar`) is purely
 * presentational.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useAudioPlayerContext } from "@/contexts/AudioPlayerContext";
import {
  useSongsetAudio,
  type SongsetAudioError,
  type SongsetAudioErrorKind,
  type UseSongsetAudioResult,
} from "@/hooks/useSongsetAudio";
import {
  toListenTimelineSources,
  type ListenSongsetItemPayload,
} from "@/lib/arrangements-listen";
import {
  buildArrangementTimeline,
  type ArrangementTimeline,
} from "@/lib/arrangements";
import { validateSongsetAudioSources } from "@/lib/audio/songset-audio";

export interface SongsetListenRequest {
  songsetId: string;
  name: string;
}

export type SongsetListenLoadState = "loading" | "ready" | "error";
export type SongsetListenLoadErrorKey = "notFound" | "build";

interface ListenSongsetPayload {
  items?: ListenSongsetItemPayload[];
  deletedItemCount?: number;
}

/** Internal request identity: open and retry each mint a new `token`. */
type ListenRequest = SongsetListenRequest & { token: number };

interface SongsetListenContextValue {
  request: SongsetListenRequest | null;
  loadState: SongsetListenLoadState;
  loadErrorKey: SongsetListenLoadErrorKey | null;
  items: ListenSongsetItemPayload[];
  deletedItemCount: number;
  timeline: ArrangementTimeline;
  validation: SongsetAudioError | null;
  previewErrorKind: SongsetAudioErrorKind | null;
  audio: UseSongsetAudioResult;
  open: (request: SongsetListenRequest) => void;
  retryLoad: () => void;
  close: () => void;
}

const SongsetListenContext = createContext<SongsetListenContextValue | null>(null);

export function SongsetListenProvider({
  enabled,
  children,
}: {
  enabled: boolean;
  children: React.ReactNode;
}) {
  const player = useAudioPlayerContext();

  const [request, setRequest] = useState<ListenRequest | null>(null);
  const [loadState, setLoadState] = useState<SongsetListenLoadState>("loading");
  const [loadErrorKey, setLoadErrorKey] = useState<SongsetListenLoadErrorKey | null>(null);
  const [payloadItems, setPayloadItems] = useState<ListenSongsetItemPayload[]>([]);
  const [deletedItemCount, setDeletedItemCount] = useState(0);
  const [previewErrorKind, setPreviewErrorKind] = useState<SongsetAudioErrorKind | null>(null);

  // Bumped by every open: a load/autoplay result may only apply to the
  // generation that asked for it.
  const generationRef = useRef(0);
  const autoplayGenerationRef = useRef(-1);

  const items = useMemo(() => toListenTimelineSources(payloadItems), [payloadItems]);
  const timeline = useMemo(() => buildArrangementTimeline(items), [items]);
  const validation = useMemo(() => validateSongsetAudioSources(items), [items]);
  const active = enabled && request !== null;

  const audio = useSongsetAudio({
    items,
    active,
    onPreviewError: (error) => {
      setPreviewErrorKind(error.kind);
    },
  });

  const close = useCallback(() => {
    // Never touches the Song transport: this provider does not own it.
    audio.stop();
    setRequest(null);
    setPayloadItems([]);
    setDeletedItemCount(0);
    setLoadErrorKey(null);
    setPreviewErrorKind(null);
  }, [audio]);

  const closeRef = useRef(close);
  useEffect(() => {
    closeRef.current = close;
  });

  const open = useCallback(
    (next: SongsetListenRequest) => {
      // Stop the Song transport first: two transports must never sound
      // together, and the songset bar must not freeze a track it replaced.
      player.stop();
      generationRef.current += 1;
      setRequest({ ...next, token: generationRef.current });
      setPayloadItems([]);
      setDeletedItemCount(0);
      setLoadErrorKey(null);
      setPreviewErrorKind(null);
      setLoadState("loading");
    },
    [player],
  );

  const retryLoad = useCallback(() => {
    setLoadErrorKey(null);
    setPreviewErrorKind(null);
    setLoadState("loading");
    // A retried load may autoplay again for the same generation.
    autoplayGenerationRef.current = -1;
    setRequest((previous) =>
      previous === null ? previous : { ...previous, token: previous.token + 1 },
    );
  }, []);

  // One GET per request object (open and retry both mint a new identity);
  // aborting on cleanup keeps a superseded response from landing on the new
  // request.
  useEffect(() => {
    if (request === null) return;
    const { songsetId } = request;

    const generation = generationRef.current;
    const controller = new AbortController();
    let cancelled = false;

    void (async () => {
      try {
        const response = await fetch(`/api/songsets/${songsetId}`, {
          signal: controller.signal,
        });
        if (cancelled) return;
        if (response.status === 401 || response.status === 404) {
          setLoadErrorKey("notFound");
          setLoadState("error");
          return;
        }
        if (!response.ok) {
          setLoadErrorKey("build");
          setLoadState("error");
          return;
        }
        const payload = (await response.json()) as ListenSongsetPayload;
        if (cancelled || generation !== generationRef.current) return;
        setPayloadItems(payload.items ?? []);
        setDeletedItemCount(payload.deletedItemCount ?? 0);
        setLoadState("ready");
      } catch {
        if (cancelled || controller.signal.aborted) return;
        setLoadErrorKey("build");
        setLoadState("error");
      }
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [request]);

  // Play once per open, and only for a playable payload. A rejected autoplay
  // (browser gesture policy) leaves the bar showing its Play button.
  const { play } = audio;
  useEffect(() => {
    if (!active || loadState !== "ready") return;
    if (deletedItemCount > 0 || validation !== null) return;
    if (items.length === 0) return;
    if (autoplayGenerationRef.current === generationRef.current) return;
    autoplayGenerationRef.current = generationRef.current;
    void play().catch(() => {});
  }, [active, loadState, deletedItemCount, validation, items.length, play]);

  // Starting any Song cancels the audition.
  const currentTrack = player.currentTrack;
  useEffect(() => {
    if (currentTrack) closeRef.current();
  }, [currentTrack]);

  // Controller/projection routes disable the dock entirely.
  useEffect(() => {
    if (!enabled) closeRef.current();
  }, [enabled]);

  const value = useMemo<SongsetListenContextValue>(
    () => ({
      request,
      loadState,
      loadErrorKey,
      items: payloadItems,
      deletedItemCount,
      timeline,
      validation,
      previewErrorKind,
      audio,
      open,
      retryLoad,
      close,
    }),
    [
      request,
      loadState,
      loadErrorKey,
      payloadItems,
      deletedItemCount,
      timeline,
      validation,
      previewErrorKind,
      audio,
      open,
      retryLoad,
      close,
    ],
  );

  return (
    <SongsetListenContext.Provider value={value}>{children}</SongsetListenContext.Provider>
  );
}

export function useSongsetListen(): SongsetListenContextValue {
  const value = useContext(SongsetListenContext);
  if (value === null) {
    throw new Error("useSongsetListen must be used within a SongsetListenProvider");
  }
  return value;
}
