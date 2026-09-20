"use client";

/**
 * Shared playback-core for the two controller pages (issue #218): the
 * connectivity-aware boot, the media model, media-error recovery, and the
 * Cast + Presentation transport wiring live here once. The logged-in songset
 * controller (app/songsets/[id]/play/controller) and the anonymous share
 * controller (app/share/[token]/play/controller) are thin consumers that
 * supply only their fetch chain, offline resolver, and exit surfaces — new
 * playback features land on both controllers without manual porting.
 */

import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import { useCastTransport, type CastMedia } from "@/hooks/useCast";
import { usePresentationSender } from "@/hooks/usePresentation";
import { dispatchCast } from "@/lib/cast/dispatch";
import type { PresentationCommand, PresentationMediaStatus } from "@/types/presentation-api";
import type { Chapter } from "@/lib/render/chapters";
import {
  createOfflineBlobUrl,
  revokeOfflineBlobUrl,
  type OfflineMediaKind,
} from "@/lib/offline/offline-playback";
import { toast } from "sonner";
import { useLocale } from "@/hooks/useLocale";
import { getConnectivity, probeConnectivity, useConnectivity } from "@/hooks/useConnectivity";

/** items[].recording.contentHash keyed by item position for the
 * Lyrics Feedback affordance (issue #194). */
export type ChapterRecordingHashes = (string | null)[];

/**
 * Media the controller boots with: the online chain's presigned URL, or an
 * offline artifact (service-worker-served proxy URL, or a blob URL over the
 * cached bytes).
 */
export interface ControllerMedia {
  src: string;
  kind: OfflineMediaKind;
  /** Offline boot: the offline hint renders and Cast is hidden. */
  isOffline: boolean;
  /** True while `src` is the proxy URL the service worker serves from cache;
   * a blob fallback is still possible. */
  viaProxy: boolean;
  renderJobId: string;
}

/** Control-flow signal: the page's fetch chain answered 401 and the page is
 * navigating to /login. Never a reason to fall back to the offline index. */
export class AuthRedirectError extends Error {}

/** What a page's offline resolver hands the core when a cached copy can play. */
export interface OfflineBoot {
  title: string;
  media: ControllerMedia;
  chapters: Chapter[];
  chapterRecordingHashes: ChapterRecordingHashes;
}

/** Setters an online chain uses to hand boot state to the core. */
export interface PlaybackChainContext {
  setTitle: (title: string) => void;
  setChapters: (chapters: Chapter[]) => void;
  setChapterRecordingHashes: (hashes: ChapterRecordingHashes) => void;
  setMedia: (media: ControllerMedia) => void;
  isCancelled: () => boolean;
}

export interface UsePlaybackCoreOptions {
  /** songsetId or share token — the boot effect's key and the Cast source id. */
  bootKey: string;
  /** The page's authenticated (songset) or anonymous (share) fetch chain. */
  runOnlineChain: (ctx: PlaybackChainContext) => Promise<void>;
  /** Resolves the page's cached copy, or null when none can play. */
  resolveOffline: () => Promise<OfflineBoot | null>;
  /** Surfaced when a boot finds no connectivity and no cached copy. */
  offlineUnavailableMessage: string;
  /** Projection document path, without query (the core appends ?v=&t=). */
  projectionPath: string;
  castSourceKind: "songset" | "share";
  /** Cast/projection title before the chain has loaded the set name. */
  defaultTitle: string;
}

/**
 * The playback core both controller pages consume. Returns every piece of
 * state and handler the page needs to render ControllerPlayer; the pages own
 * only their fetch chain and their loading/failure exit routes.
 */
export function usePlaybackCore(options: UsePlaybackCoreOptions) {
  const { t } = useLocale();
  const bootKey = options.bootKey;
  const connectivity = useConnectivity();
  const offlineNow = connectivity === "offline";

  const [title, setTitle] = useState<string | null>(null);
  const [media, setMedia] = useState<ControllerMedia | null>(null);
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [chapterRecordingHashes, setChapterRecordingHashes] =
    useState<ChapterRecordingHashes>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [presentationMediaStatus, setPresentationMediaStatus] =
    useState<PresentationMediaStatus | null>(null);

  // The page's callbacks close over router/t/token and change identity every
  // render; the boot effect must not re-run on that. Keep the latest options
  // in a ref via an effect (never during render) and let the boot effect read
  // through it — the boot decision is made ONCE per key (see the boot
  // comment below).
  const optionsRef = useRef(options);
  useEffect(() => {
    optionsRef.current = options;
  });

  // Blob URLs keep the whole artifact resident; release every one this page
  // created when it unmounts. (A swap replaces a proxy URL, never a blob URL,
  // so unmount is the only release point.)
  const blobUrlsRef = useRef<string[]>([]);
  useEffect(() => {
    const blobUrls = blobUrlsRef.current;
    return () => {
      for (const src of blobUrls) revokeOfflineBlobUrl(src);
      blobUrls.length = 0;
    };
  }, []);

  // Connectivity-aware three-branch boot (issue #205, probed state from
  // issue #211's shared Connectivity):
  //   1. online → the page's online chain verbatim (freshness and Cast
  //      preserved for a downloaded-but-online leader);
  //   2. chain fails while not positively offline (mid-session drop: a
  //      rejected fetch, or the service worker's 503 for an uncached API
  //      route) → boot cache-first from the offline resolver, Cast hidden;
  //   3. offline at boot (navigator.onLine false at effect time) →
  //      cache-first directly, zero API fetches.
  // The boot decision is made ONCE at effect time and deliberately does not
  // re-run on later state flips: an Unknown (in-flight/inconclusive) probe
  // falls into branch 1, whose failure fallback (branch 2) covers the
  // genuinely-offline outcome without ever stealing a fresh online boot.
  // Branch 3 keys on definitive Offline ("offline" ⟺ navigator.onLine
  // false); probing would be pointless there. `isOffline` media only ever
  // comes out of branches 2/3, so Cast gating on it is correct: online
  // never skips the API chain, and never silently plays a stale download.
  useEffect(() => {
    let cancelled = false;

    // Boots the player from the page's offline resolver (offline index +
    // artifact cache). Returns false when there is no usable cached copy
    // (the caller then surfaces the chain's own error).
    async function bootOffline(): Promise<boolean> {
      const offline = await optionsRef.current.resolveOffline();
      if (cancelled || !offline) return false;
      if (offline.media.isOffline && !offline.media.viaProxy) {
        blobUrlsRef.current.push(offline.media.src);
      }
      setTitle(offline.title);
      setChapters(offline.chapters);
      setChapterRecordingHashes(offline.chapterRecordingHashes);
      setMedia(offline.media);
      return true;
    }

    async function loadData() {
      try {
        setIsLoading(true);
        setError(null);

        // OS-offline: no API fetch is even attempted — the offline index
        // and the artifact cache are the only sources. No playable copy →
        // the offline-unavailable error.
        if (getConnectivity() === "offline") {
          if (await bootOffline()) return;
          throw new Error(optionsRef.current.offlineUnavailableMessage);
        }

        // Cache-first boot (issue #211 follow-up): when a downloaded copy
        // exists and is playable, boot it without any API fetch — cached ⇒
        // offline copy, connectivity irrelevant. A record whose bytes are
        // unusable (corrupt/evicted) falls through to the online chain.
        if (await bootOffline()) return;

        const ctx: PlaybackChainContext = {
          setTitle,
          setChapters,
          setChapterRecordingHashes,
          setMedia,
          isCancelled: () => cancelled,
        };

        try {
          await optionsRef.current.runOnlineChain(ctx);
        } catch (err) {
          if (err instanceof AuthRedirectError) throw err;
          // The chain did not complete: prefer the downloaded copy over the
          // error screen, and say so — a silent fallback hides the
          // stale-playback risk from the leader (and from what Cast
          // reflects). The toast carries no behavioral weight: isOfflineMedia
          // stays the sole Cast gate. Branch 3 (offline at boot) needs no
          // hint — the boot screen already announced it. Re-probe so the
          // shared state (and the offline banner) reflects why the chain
          // could not load (issue #211: probe after an app-level fetch
          // failure).
          void probeConnectivity();
          if (await bootOffline()) {
            toast.info(t("control.offlineFallback"));
            return;
          }
          throw err;
        }
      } catch (err) {
        if (!cancelled) {
          if (err instanceof AuthRedirectError) return;
          const message =
            err instanceof Error ? err.message : t("control.failedToLoadPlayer");
          setError(message);
          toast.error(message);
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    }

    if (bootKey) {
      void loadData();
    }

    return () => {
      cancelled = true;
    };
  }, [bootKey, t]);

  // ── Media failure → recovery ────────────────────────────────────────────
  // The offline proxy URL is served by the service worker from Cache Storage.
  // When it is not (worker no longer controlling the document, entry evicted
  // between boot and play), the media element errors — re-issue the load
  // against a blob URL of the cached artifact rather than going straight to
  // the failure overlay. One attempt per boot: a blob URL already holds the
  // whole artifact, there is no cheaper source to fall back to after it.
  //
  // An online boot has no offline source to fail FROM, but it may still have
  // a downloaded copy to fail TO: when the presigned R2 URL dies mid-playback
  // (Airplane Mode, 4-hour expiry), swap to the offline artifact once per
  // boot. Unhandled failures keep the overlay + Retry behavior.
  const blobFallbackTriedRef = useRef(false);
  const offlineRecoveryTriedRef = useRef(false);

  const handleMediaError = useCallback(async (): Promise<boolean> => {
    if (!media) return false;

    if (media.isOffline) {
      // A blob URL holds the whole artifact — no cheaper source after it.
      // The online-recovery branch below consumes the boot's single
      // offline-recovery attempt: after it, a failing recovered source is
      // terminal (no swap loop when the cached copy itself is broken).
      if (
        !media.viaProxy ||
        blobFallbackTriedRef.current ||
        offlineRecoveryTriedRef.current
      ) {
        return false;
      }
      blobFallbackTriedRef.current = true;

      const src = await createOfflineBlobUrl(media.renderJobId, media.kind);
      if (!src) return false;

      blobUrlsRef.current.push(src);
      setMedia({ ...media, src, viaProxy: false });
      return true;
    }

    // Online source failed: one attempt per boot at the downloaded copy.
    if (offlineRecoveryTriedRef.current) return false;
    offlineRecoveryTriedRef.current = true;

    const offline = await optionsRef.current.resolveOffline();
    if (!offline) return false;

    // The cached manifest/hashes are at least as good as what the online
    // chain loaded — and they describe the artifact we are swapping to.
    if (offline.media.isOffline && !offline.media.viaProxy) {
      blobUrlsRef.current.push(offline.media.src);
    }
    setTitle(offline.title);
    setChapters(offline.chapters);
    setChapterRecordingHashes(offline.chapterRecordingHashes);
    setMedia(offline.media);
    return true;
  }, [media]);

  // Cast + Presentation transport wiring.
  //
  // The Cast Web Sender SDK is the production transport. The dev-only
  // Presentation API sender is retained as a fallback used only when the Cast
  // SDK is unavailable (e.g. iOS) — `sender.send` / `sender.start` are never
  // invoked when `cast.isSupported` is true.
  // Build the projection receiver URL. The controller passes the media source
  // via the `v` query param and the set name via `t` so the receiver (a
  // Presentation-API context with no session cookies) can boot without
  // calling any API. When no media is loaded yet the URL falls back to the
  // bare path; the controller's render guards prevent `handleSendToTV` from
  // running before data is ready.
  const mediaSrc = media?.src;
  const presentationUrl = useMemo(() => {
    const params = new URLSearchParams();
    if (mediaSrc) params.set("v", mediaSrc);
    if (title) params.set("t", title);
    const qs = params.toString();
    return qs
      ? `${options.projectionPath}?${qs}`
      : options.projectionPath;
  }, [options.projectionPath, mediaSrc, title]);

  const castMedia = useMemo<CastMedia>(
    () => ({
      videoUrl: mediaSrc ?? "",
      title: title ?? options.defaultTitle,
      source: { kind: options.castSourceKind, idOrToken: bootKey },
      startSeconds: 0,
    }),
    [mediaSrc, title, options.defaultTitle, options.castSourceKind, bootKey],
  );

  const cast = useCastTransport({
    media: castMedia,
    onError: (m) => toast.error(m),
  });

  const sender = usePresentationSender({
    presentationUrl,
    onConnected: () => toast.success(t("control.connectedProjection")),
    onDisconnected: () => {
      setPresentationMediaStatus(null);
      toast.info(t("control.disconnectedProjection"));
    },
    onStartError: (m) => toast.error(m),
    onStatus: (status) => {
      if (status.type === "error") {
        toast.error(t("projection.tvFailed"));
      } else if (status.type === "media") {
        setPresentationMediaStatus(status);
      }
    },
  });

  // Toasts only from transport lifecycle: cast connection transitions are
  // observed via state (the hook exposes `isConnected`, not a callback).
  const prevCastConnectedRef = useRef(false);
  useEffect(() => {
    const wasConnected = prevCastConnectedRef.current;
    if (cast.isConnected && !wasConnected) {
      toast.success(
        `${t("control.connectedTo")} ${cast.deviceName || t("control.tv")}`
      );
    } else if (!cast.isConnected && wasConnected) {
      toast.info(t("control.disconnectedFromTV"));
    }
    prevCastConnectedRef.current = cast.isConnected;
  }, [cast.isConnected, cast.deviceName, t]);

  const isPresentationActive =
    cast.isConnected || (!cast.isSupported && sender.isConnected);

  // Unified intent handlers. Cast is preferred when supported; the
  // Presentation fallback only runs when `!cast.isSupported`.
  const handleSendToTV = useCallback(() => {
    if (cast.isSupported) {
      void cast.start();
    } else {
      void sender.start();
    }
  }, [cast, sender]);

  const handleSendTransportCommand = useCallback(
    (command: PresentationCommand) => {
      if (cast.isSupported) {
        dispatchCast(cast, command);
      } else {
        sender.send(command);
      }
    },
    [cast, sender],
  );

  const handleStopPresentation = useCallback(() => {
    if (cast.isConnected) {
      cast.stop();
    } else if (!cast.isSupported && sender.isConnected) {
      sender.stop();
    }
  }, [cast, sender]);

  return {
    title,
    media,
    chapters,
    chapterRecordingHashes,
    isLoading,
    error,
    offlineNow,
    presentationUrl,
    transport: cast,
    sender,
    isPresentationActive,
    presentationMediaStatus,
    handleMediaError,
    handleSendToTV,
    handleStopPresentation,
    handleSendTransportCommand,
  };
}
