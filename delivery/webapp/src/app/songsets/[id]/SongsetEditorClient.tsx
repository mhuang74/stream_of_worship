"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import dynamic from "next/dynamic";
import { SongsetEditor } from "@/components/songset/SongsetEditor";
import { SongCardData } from "@/components/songset/SongCard";
import { SongListItem } from "@/components/songset/SongList";
import type { SongArrangement, RecordingSection, ArrangementItemInput } from "@/lib/arrangements";
import type { ResolvedGlueJunction } from "@/lib/glue/resolve";
import {
  buildSnappedSteps,
  getItemDurationSeconds,
  getSongsetDurationSeconds,
} from "@/lib/arrangements";
import { RenderState } from "@/components/songset/RenderStatusBadge";
import { TransitionSettings } from "@/components/songset/TransitionPanel";
import { toast } from "sonner";
import { useLocale } from "@/hooks/useLocale";
import { useConnectivity } from "@/hooks/useConnectivity";
import { useOfflineRedirect } from "@/hooks/useOfflineRedirect";
import { getOfflineRecord } from "@/lib/offline/offline-index";
import { useSongsetListBack } from "@/hooks/useSongsetListBack";
import { sanitizeFilename, fetchSignedUrlAndDownload } from "@/lib/download";
import { removeOfflineSongset } from "@/lib/offline/offline-index";
import { OfflineStatus } from "@/components/play/OfflineStatus";
import { unlockAudioContext } from "@/lib/audio/audio-context";
import { useSongsetListen } from "@/contexts/SongsetListenContext";
import { useRenderJobWatch, type RenderJobSnapshot } from "@/hooks/useRenderJobWatch";
import { isOfflineSupportedOnCurrentDevice } from "@/lib/offline/artifact-cache";
import { downloadOfflineArtifacts, NoArtifactsError } from "@/lib/offline/download-offline";
import { SONGSET_MAX_SONGS } from "@/lib/constants";

/** R2 keys the offline download needs, fetched from the render job. */
interface RenderJobR2Keys {
  id: string;
  mp3R2Key: string | null;
  mp4R2Key: string | null;
  chaptersR2Key: string | null;
}

const BrowseSheet = dynamic(
  () => import("@/components/songset/BrowseSheet").then((m) => ({ default: m.BrowseSheet })),
  { ssr: false }
);

const ShareDialog = dynamic(
  () => import("@/components/share/ShareDialog").then((m) => ({ default: m.ShareDialog })),
  { ssr: false }
);

const ArrangementEditorSheet = dynamic(
  () =>
    import("@/components/songset/ArrangementEditorSheet").then((m) => ({
      default: m.ArrangementEditorSheet,
    })),
  { ssr: false }
);

interface ApiSongset {
  id: string;
  name: string;
  description: string | null;
  createdAt: string;
  updatedAt: string;
  renderState: RenderState;
  itemCount: number;
  durationSeconds: number | null;
  latestRenderJobId: string | null;
  lastFailedRenderJobId: string | null;
  lastCompletedRenderJobId: string | null;
  renderErrorMessage: string | null;
  failedAt: string | null;
  isArtifactsStale?: boolean;
  latestRenderJobEstimatedTotalSeconds: number | null;
}

interface ApiSongsetItem {
  id: string;
  songId: string;
  recordingHashPrefix: string | null;
  position: number;
  gapBeats: number;
  crossfadeEnabled: number;
  crossfadeDurationSeconds: number | null;
  keyShiftSemitones: number;
  tempoRatio: number;
  song: {
    id: string;
    title: string;
    composer: string | null;
    lyricist: string | null;
    albumName: string | null;
    musicalKey: string | null;
  } | null;
  recording: {
    contentHash: string;
    durationSeconds: number | null;
    tempoBpm: number | null;
    musicalKey: string | null;
    theme?: string | null;
    /** #283 exposes parsed analysis; older responses omit it. */
    sections?: RecordingSection[] | null;
    beats?: number[];
  } | null;
  markedLineCount?: number;
  /** Raw persisted arrangement JSON; undefined when the API predates #283. */
  arrangementSteps?: string | null;
  updatedAt?: string;
  glueEnabled?: boolean;
  glueBeats?: 2 | 4 | 8 | 16;
  glue?: ResolvedGlueJunction;
}

interface ApiResponse {
  id: string;
  name: string;
  description: string | null;
  createdAt: string;
  updatedAt: string;
  renderState: RenderState;
  itemCount: number;
  durationSeconds: number | null;
  latestRenderJobId: string | null;
  lastFailedRenderJobId: string | null;
  lastCompletedRenderJobId: string | null;
  renderErrorMessage: string | null;
  failedAt: string | null;
  isArtifactsStale?: boolean;
  latestRenderJobEstimatedTotalSeconds: number | null;
  items: ApiSongsetItem[];
}

interface SongsetEditorClientProps {
  songsetId: string;
  initialData: ApiResponse;
}

/** Arrangement input shape shared with the canonical duration helpers. */
function toArrangementInput(item: SongListItem): ArrangementItemInput {
  return {
    arrangementSteps: item.arrangementSteps,
    gapBeats: item.gapBeats,
    crossfadeEnabled: item.crossfadeEnabled,
    crossfadeDurationSeconds: item.crossfadeDurationSeconds ?? null,
    tempoBpm: item.recording?.tempoBpm ?? null,
    recording: { durationSeconds: item.recording?.durationSeconds ?? null },
  };
}

function hasRecordingAnalysis(recording: SongListItem["recording"]): boolean {
  const sections = recording?.sections ?? null;
  const beats = recording?.beats ?? [];
  const duration = recording?.durationSeconds ?? null;
  if (!sections?.length || beats.length === 0 || duration == null) return false;
  // The sheet drafts from snapped sections, so a collapsed snap means no
  // arrangement can be created even though raw analysis fields exist.
  return buildSnappedSteps(sections, beats, duration) !== null;
}

function toSongListItem(item: ApiSongsetItem): SongListItem {
  const arrangementSteps = item.arrangementSteps ?? null;
  const recording = item.recording
    ? {
        ...item.recording,
        hashPrefix: item.recordingHashPrefix ?? "",
        sections: item.recording.sections ?? null,
        beats: item.recording.beats ?? [],
      }
    : null;
  return {
    id: item.id,
    songId: item.songId,
    position: item.position,
    song: item.song,
    recording,
    gapBeats: item.gapBeats,
    crossfadeEnabled: item.crossfadeEnabled,
    crossfadeDurationSeconds: item.crossfadeDurationSeconds,
    keyShiftSemitones: item.keyShiftSemitones,
    tempoRatio: item.tempoRatio,
    markedLineCount: item.markedLineCount,
    arrangementSteps,
    arrangedDurationSeconds: getItemDurationSeconds({
      arrangementSteps,
      recording: { durationSeconds: recording?.durationSeconds ?? null },
    }),
    arrangementAnalysisAvailable: hasRecordingAnalysis(recording),
    updatedAt: item.updatedAt,
    glueEnabled: item.glueEnabled,
    glueBeats: item.glueBeats,
    glue: item.glue,
  };
}

/** Narrow subset of a saved item detail returned by the arrangement PUT. */
interface ArrangementDetailFields {
  arrangementSteps?: string | null;
  recording?: {
    durationSeconds?: number | null;
    sections?: RecordingSection[] | null;
    beats?: number[];
  } | null;
}

function readSections(value: unknown): RecordingSection[] | null {
  if (!Array.isArray(value)) return null;
  const sections: RecordingSection[] = [];
  for (const entry of value) {
    if (
      typeof entry !== "object" ||
      entry === null ||
      !("label" in entry) ||
      !("start" in entry) ||
      !("end" in entry) ||
      typeof entry.label !== "string" ||
      typeof entry.start !== "number" ||
      typeof entry.end !== "number"
    ) {
      return null;
    }
    sections.push({ label: entry.label, start: entry.start, end: entry.end });
  }
  return sections;
}

function readBeats(value: unknown): number[] {
  if (!Array.isArray(value)) return [];
  const beats: number[] = [];
  for (const entry of value) {
    if (typeof entry !== "number" || !Number.isFinite(entry)) return [];
    beats.push(entry);
  }
  return beats;
}

/** Boundary-read the PUT response detail; unknown shapes yield no fields. */
function readArrangementDetail(value: unknown): ArrangementDetailFields | null {
  if (typeof value !== "object" || value === null) return null;
  const detail: ArrangementDetailFields = {};
  if ("arrangementSteps" in value) {
    const steps = value.arrangementSteps;
    if (steps === null || typeof steps === "string") {
      detail.arrangementSteps = steps;
    }
  }
  if ("recording" in value) {
    const recording = value.recording;
    if (recording === null) {
      detail.recording = null;
    } else if (typeof recording === "object" && recording !== null) {
      const fields: NonNullable<ArrangementDetailFields["recording"]> = {
        durationSeconds:
          "durationSeconds" in recording && typeof recording.durationSeconds === "number"
            ? recording.durationSeconds
            : null,
        sections: "sections" in recording ? readSections(recording.sections) : null,
      };
      if ("beats" in recording) {
        fields.beats = readBeats(recording.beats);
      }
      detail.recording = fields;
    }
  }
  return detail;
}

/** Merge a saved arrangement (and any detail returned by the PUT) back onto
 * one item without touching sibling items. */
function applySavedArrangement(
  item: SongListItem,
  arrangementSteps: string | null,
  detail: ArrangementDetailFields | null
): SongListItem {
  const recordingDurationSeconds =
    detail?.recording?.durationSeconds ?? item.recording?.durationSeconds ?? null;
  const recording = item.recording
    ? {
        ...item.recording,
        durationSeconds: recordingDurationSeconds,
        sections: detail?.recording?.sections ?? item.recording.sections ?? null,
        beats: detail?.recording?.beats ?? item.recording.beats ?? [],
      }
    : null;
  return {
    ...item,
    recording,
    arrangementSteps,
    arrangedDurationSeconds: getItemDurationSeconds({
      arrangementSteps,
      recording: { durationSeconds: recordingDurationSeconds },
    }),
    arrangementAnalysisAvailable: hasRecordingAnalysis(recording),
  };
}

export function SongsetEditorClient({ songsetId, initialData }: SongsetEditorClientProps) {
  const router = useRouter();
  const backToList = useSongsetListBack();
  const { t } = useLocale();
  const connectivity = useConnectivity();
  const { open } = useSongsetListen();
  useOfflineRedirect();
  const searchParams = useSearchParams();
  const isNew = searchParams.get("new") === "true";
  const highlightSongParam = searchParams.get("highlightSong");
  const glueItemParam = searchParams.get("glueItem");
  const [highlightSongId, setHighlightSongId] = useState<string | null>(
    highlightSongParam
  );

  const [songset, setSongset] = useState<ApiSongset | null>(() => ({
    id: initialData.id,
    name: initialData.name,
    description: initialData.description,
    createdAt: initialData.createdAt,
    updatedAt: initialData.updatedAt,
    renderState: initialData.renderState,
    itemCount: initialData.itemCount,
    durationSeconds: initialData.durationSeconds ?? null,
    latestRenderJobId: initialData.latestRenderJobId,
    lastFailedRenderJobId: initialData.lastFailedRenderJobId,
    lastCompletedRenderJobId: initialData.lastCompletedRenderJobId,
    renderErrorMessage: initialData.renderErrorMessage,
    failedAt: initialData.failedAt,
    isArtifactsStale: initialData.isArtifactsStale,
    latestRenderJobEstimatedTotalSeconds: initialData.latestRenderJobEstimatedTotalSeconds,
  }));
  const [items, setItems] = useState<SongListItem[]>(() => initialData.items.map(toSongListItem));
  // Offline index record (client-only): cached ⇒ offline-copy play entry.
  const [isOfflineAvailable, setIsOfflineAvailable] = useState(false);
  useEffect(() => {
    let cancelled = false;
    void getOfflineRecord(songsetId).then((record) => {
      if (!cancelled) setIsOfflineAvailable(record != null);
    });
    return () => {
      cancelled = true;
    };
  }, [songsetId]);
  const [isBrowseSheetOpen, setIsBrowseSheetOpen] = useState(false);
  // Item whose arrangement draft the bottom sheet owns (null = sheet closed).
  const [editingArrangementItemId, setEditingArrangementItemId] = useState<string | null>(null);
  // Render job R2 keys for the offline download control (moved from the
  // deleted Play screen, issue #211 follow-up Q16). Fetched once when the
  // songset has a completed render.
  const [renderJobR2Keys, setRenderJobR2Keys] = useState<RenderJobR2Keys | null>(null);
  useEffect(() => {
    const jobId = songset?.latestRenderJobId;
    if (!jobId) return;
    let cancelled = false;
    void (async () => {
      try {
        const response = await fetch(`/api/render-jobs/${jobId}`);
        if (!response.ok) return;
        const data = await response.json();
        if (!cancelled) {
          setRenderJobR2Keys({
            id: data.id,
            mp3R2Key: data.mp3R2Key,
            mp4R2Key: data.mp4R2Key,
            chaptersR2Key: data.chaptersR2Key,
          });
        }
      } catch {
        // Best-effort: the offline control just stays absent this boot.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [songset?.latestRenderJobId]);
  const isLoading = false;
  const error: string | null = null;
  const [isRemoving, setIsRemoving] = useState(false);
  // Every Item mutation (append, remove, duplicate, reorder) goes through this
  // promise queue: React state updates are ordered, but the network calls are
  // not, so an unqueued remove could race an in-flight duplicate and drop the
  // persisted copy from the list.
  const itemMutationQueueRef = useRef<Promise<void>>(Promise.resolve());
  const [pendingItemMutations, setPendingItemMutations] = useState(0);
  const itemsRef = useRef(items);
  // Queued operations read and write the ref synchronously: React may not have
  // committed the previous operation's state before the next one starts.
  const commitItems = useCallback((nextItems: SongListItem[]) => {
    itemsRef.current = nextItems;
    setItems(nextItems);
  }, []);
  const runItemMutation = useCallback(<T,>(operation: () => Promise<T>): Promise<T> => {
    const run = async () => {
      setPendingItemMutations((count) => count + 1);
      try {
        return await operation();
      } finally {
        setPendingItemMutations((count) => count - 1);
      }
    };
    const result = itemMutationQueueRef.current.then(run, run);
    itemMutationQueueRef.current = result.then(
      () => undefined,
      () => undefined
    );
    return result;
  }, []);
  const isItemMutationPending = pendingItemMutations > 0;
  const isSongsetFull = items.length >= SONGSET_MAX_SONGS;
  const [shareDialogOpen, setShareDialogOpen] = useState(searchParams.get("share") === "true");
  const autoOpenDoneRef = useRef(false);
  const isShare = searchParams.get("share") === "true";

  // ---- In-flight render -------------------------------------------------
  // The editor is the render screen's destination now (the /render page
  // redirects here while a job is active), so it owns the completion watch.
  const [isCancellingRender, setIsCancellingRender] = useState(false);
  // True between a Cancel tap and the DELETE settling, so a poll tick landing
  // in that window cannot report our own cancellation as a failure.
  const isCancellingRef = useRef(false);
  // The auto-cache switch lives in settings (settings page owns it). An
  // unreadable setting counts as off — nobody should discover a surprise
  // hundred-megabyte download. Kept in a ref, not state: nothing renders from
  // it, and the completion callback must see the value the moment the fetch
  // lands rather than after a re-render.
  const offlineAutoCacheRef = useRef(false);
  useEffect(() => {
    if (songset?.renderState !== "rendering") return;
    let cancelled = false;
    fetch("/api/settings")
      .then((response) => (response.ok ? response.json() : null))
      .then((data: { settings?: { offlineAutoCache?: boolean } } | null) => {
        if (!cancelled && data) {
          offlineAutoCacheRef.current = data.settings?.offlineAutoCache === true;
        }
      })
      .catch(() => {
        offlineAutoCacheRef.current = false;
      });
    return () => {
      cancelled = true;
    };
  }, [songset?.latestRenderJobId, songset?.renderState]);

  const handleRenderComplete = useCallback(
    (job: RenderJobSnapshot) => {
      toast.success(t("render.toast.completed"));
      // Local patch, not router.refresh(): the state above is prop-seeded once
      // and never re-synced, so the editor would otherwise keep showing the
      // in-flight status until a full reload.
      setSongset((prev) =>
        prev
          ? {
              ...prev,
              renderState: "fresh" as RenderState,
              isArtifactsStale: false,
              renderErrorMessage: null,
              failedAt: null,
              latestRenderJobId: job.id,
              lastCompletedRenderJobId: job.id,
              latestRenderJobEstimatedTotalSeconds: null,
            }
          : prev
      );
      // Refresh the offline control's keys: the mount-time fetch saw a running
      // job, so its artifact keys were all null.
      setRenderJobR2Keys({
        id: job.id,
        mp3R2Key: job.mp3R2Key,
        mp4R2Key: job.mp4R2Key,
        chaptersR2Key: job.chaptersR2Key,
      });

      // Auto-cache is tab-contingent: the render worker writes the database
      // directly, so a completed job is only observable from a live page that
      // is polling for it.
      if (!offlineAutoCacheRef.current) return;
      if (!job.mp3R2Key && !job.mp4R2Key) return;
      if (!isOfflineSupportedOnCurrentDevice() || !("caches" in window)) return;

      // Fire-and-forget: a worship video is hundreds of megabytes, and the
      // page is already done telling the user what happened.
      downloadOfflineArtifacts({
        songsetId,
        songsetName: songset?.name ?? "",
        renderJobId: job.id,
      })
        .then(() => {
          setIsOfflineAvailable(true);
          toast.success(t("audio.offline.downloaded"));
        })
        .catch((error: unknown) => {
          console.error("Auto-cache error:", error);
          toast.error(
            error instanceof NoArtifactsError
              ? t("audio.offline.noArtifacts")
              : t("audio.offline.downloadFailed")
          );
        });
    },
    [songset?.name, songsetId, t]
  );

  const handleRenderFailed = useCallback(
    (job: RenderJobSnapshot) => {
      // A poll tick can observe the cancelled status while our own DELETE is in
      // flight; the cancel path owns that transition (and its toast).
      if (isCancellingRef.current) return;
      toast.error(t("render.toast.failed"));
      setSongset((prev) =>
        prev
          ? {
              ...prev,
              renderState: "failed" as RenderState,
              renderErrorMessage: job.errorMessage,
              failedAt: new Date().toISOString(),
              isArtifactsStale: false,
              latestRenderJobEstimatedTotalSeconds: null,
            }
          : prev
      );
    },
    [t]
  );

  const handleRenderCancelled = useCallback(() => {
    // The local cancel path owns the toast and the patch; a poll tick that
    // observes the flip is just confirmation.
    if (isCancellingRef.current) return;
    setSongset((prev) =>
      prev
        ? {
            ...prev,
            renderState: "unrendered" as RenderState,
            latestRenderJobId: null,
            renderErrorMessage: null,
            failedAt: null,
            isArtifactsStale: false,
            latestRenderJobEstimatedTotalSeconds: null,
          }
        : prev
    );
  }, []);

  // The poll hit 401/403: the session expired and can never observe this job's
  // completion. The job itself is fine — the render worker writes the database
  // directly — so only the toast marks the state change (issue #210).
  const handleRenderAuthExpired = useCallback(() => {
    toast.error(t("render.toast.authExpired"));
  }, [t]);

  useRenderJobWatch({
    jobId: songset?.latestRenderJobId ?? null,
    enabled: songset?.renderState === "rendering",
    onCompleted: handleRenderComplete,
    onFailed: handleRenderFailed,
    onCancelled: handleRenderCancelled,
    onAuthExpired: handleRenderAuthExpired,
  });

  const handleCancelRender = useCallback(async () => {
    const jobId = songset?.latestRenderJobId;
    if (!jobId || isCancellingRef.current) return;
    setIsCancellingRender(true);
    isCancellingRef.current = true;
    try {
      const response = await fetch(`/api/render-jobs/${jobId}`, { method: "DELETE" });
      if (!response.ok) {
        // 400 carries the job's actual terminal status, 404 means the row is
        // gone. That reason is the only actionable thing the user can get, so
        // prefer it over a fixed string.
        const data = (await response.json().catch(() => null)) as { error?: string } | null;
        throw new Error(data?.error || t("render.toast.failedToCancelJob"));
      }
      toast.info(t("render.toast.cancelled"));
      setSongset((prev) =>
        prev
          ? {
              ...prev,
              renderState: "unrendered" as RenderState,
              latestRenderJobId: null,
              renderErrorMessage: null,
              failedAt: null,
              isArtifactsStale: false,
              latestRenderJobEstimatedTotalSeconds: null,
            }
          : prev
      );
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("render.toast.failedToCancelJob"));
    } finally {
      isCancellingRef.current = false;
      setIsCancellingRender(false);
    }
  }, [songset?.latestRenderJobId, t]);

  useEffect(() => {
    if (isNew && !autoOpenDoneRef.current && items.length === 0) {
      autoOpenDoneRef.current = true;
      setIsBrowseSheetOpen(true);
      router.replace(`/songsets/${songsetId}`);
    }
  }, [songsetId, router, isNew, items.length]);

  // Consume the ephemeral ?highlightSong= param: strip it from the URL while
  // preserving any other query params (e.g. ?new=true, ?share=true). The
  // highlightSongId value is retained in component state (not derived from
  // searchParams) so URL cleanup does not null it out mid-effect. It is
  // cleared only after SongList's scroll/highlight effect has run, via
  // onHighlightConsumed.
  useEffect(() => {
    if (!highlightSongParam) return;
    const next = new URLSearchParams(searchParams.toString());
    next.delete("highlightSong");
    const query = next.toString();
    router.replace(`/songsets/${songsetId}${query ? `?${query}` : ""}`);
  }, [highlightSongParam, songsetId, searchParams, router]);

  // Render-preflight "Open editor" lands here with the outgoing item id: open
  // that item's arrangement sheet (where paid AI-transition actions live), then
  // strip the ephemeral param. The open is a one-shot reaction to the URL, not
  // render-derived state: the strip below clears the param on the next
  // navigation, and deriving it during render would reopen a sheet the user
  // just closed.
  useEffect(() => {
    if (!glueItemParam) return;
    const target = items.find((item) => item.id === glueItemParam);
    // eslint-disable-next-line react-hooks/set-state-in-effect -- one-shot deep-link open; the ephemeral param is stripped immediately after
    if (target) setEditingArrangementItemId(target.id);
    const next = new URLSearchParams(searchParams.toString());
    next.delete("glueItem");
    const query = next.toString();
    router.replace(`/songsets/${songsetId}${query ? `?${query}` : ""}`);
  }, [glueItemParam, items, songsetId, searchParams, router]);

  const touchUpdatedAt = useCallback(() => {
    const now = new Date().toISOString();
    setSongset((prev) => (prev ? { ...prev, updatedAt: now } : prev));
  }, []);

  const markStale = useCallback(() => {
    setSongset((prev) =>
      prev
        ? {
            ...prev,
            renderState: "stale" as RenderState,
            isArtifactsStale: true,
            renderErrorMessage: null,
            failedAt: null,
          }
        : prev
    );
    touchUpdatedAt();
  }, [touchUpdatedAt]);

  // Handle item reorder (optimistic)
  const handleUpdateItems = useCallback(
    (newItems: SongListItem[]) => {
      void runItemMutation(async () => {
        const previousItems = itemsRef.current;
        commitItems(newItems);

        const updates = newItems.map((item, index) => ({
          itemId: item.id,
          position: index,
        }));

        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 30000);

        try {
          const response = await fetch(`/api/songsets/${songsetId}/items/reorder`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ updates }),
            signal: controller.signal,
          });
          clearTimeout(timeoutId);
          if (!response.ok) {
            commitItems(previousItems);
            toast.error(t("songsets.error.reorderFailed"));
            return;
          }
          markStale();
        } catch {
          clearTimeout(timeoutId);
          commitItems(previousItems);
          toast.error(t("songsets.error.reorderFailed"));
        }
      });
    },
    [commitItems, runItemMutation, songsetId, markStale, t]
  );

  // Handle item removal
  const handleRemoveItem = useCallback(
    async (itemId: string) =>
      runItemMutation(async () => {
        if (isRemoving) return;
        setIsRemoving(true);

        const currentItems = itemsRef.current;
        const removedItem = currentItems.find((item) => item.id === itemId);
        const removedIndex = currentItems.findIndex((item) => item.id === itemId);
        const prevRenderState = songset?.renderState;
        const prevIsArtifactsStale = songset?.isArtifactsStale;
        const prevRenderErrorMessage = songset?.renderErrorMessage;
        const prevFailedAt = songset?.failedAt;

        commitItems(currentItems.filter((item) => item.id !== itemId));
        setSongset((prev) =>
          prev
            ? {
                ...prev,
                itemCount: Math.max(0, prev.itemCount - 1),
                renderState: "stale" as RenderState,
                isArtifactsStale: true,
                renderErrorMessage: null,
                failedAt: null,
              }
            : prev
        );

        try {
          const response = await fetch(
            `/api/songsets/${songsetId}/items?itemId=${itemId}`,
            {
              method: "DELETE",
            }
          );

          if (!response.ok) {
            throw new Error(t("songsets.error.removeItemFailed"));
          }

          touchUpdatedAt();
        } catch {
          if (removedItem && removedIndex >= 0) {
            const restored = [...itemsRef.current];
            restored.splice(removedIndex, 0, removedItem);
            commitItems(restored);
            setSongset((prev) =>
              prev
                ? {
                    ...prev,
                    itemCount: prev.itemCount + 1,
                    renderState: prevRenderState ?? prev.renderState,
                    isArtifactsStale: prevIsArtifactsStale ?? prev.isArtifactsStale,
                    renderErrorMessage: prevRenderErrorMessage ?? prev.renderErrorMessage,
                    failedAt: prevFailedAt ?? prev.failedAt,
                  }
                : prev
            );
          }
          throw new Error(t("songsets.error.removeItemFailed"));
        } finally {
          setIsRemoving(false);
        }
      }),
    [
      commitItems,
      runItemMutation,
      songsetId,
      isRemoving,
      songset?.isArtifactsStale,
      songset?.renderState,
      songset?.renderErrorMessage,
      songset?.failedAt,
      t,
      touchUpdatedAt,
    ]
  );

  // Handle item duplication — the same Song may occupy several Items.
  const handleDuplicateItem = useCallback(
    async (itemId: string) =>
      runItemMutation(async () => {
        const response = await fetch(`/api/songsets/${songsetId}/items/${itemId}/duplicate`, {
          method: "POST",
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(
            (data as { error?: string }).error || t("songsets.error.duplicateItemFailed")
          );
        }
        const item: ApiSongsetItem = data;

        if (!itemsRef.current.some((entry) => entry.id === itemId)) {
          // The copy is persisted but its source is no longer in local state
          // (a queued remove from this or another tab). Adopt the server's
          // authoritative ordering instead of silently dropping the copy.
          const refreshed = await fetch(`/api/songsets/${songsetId}`);
          if (!refreshed.ok) {
            throw new Error(t("songsets.error.duplicateItemFailed"));
          }
          const authoritative: ApiResponse = await refreshed.json();
          commitItems(authoritative.items.map(toSongListItem));
          setSongset((prev) =>
            prev
              ? {
                  ...prev,
                  itemCount: authoritative.itemCount,
                  durationSeconds: authoritative.durationSeconds,
                  updatedAt: authoritative.updatedAt,
                  renderState: "stale" as RenderState,
                  isArtifactsStale: true,
                  renderErrorMessage: null,
                  failedAt: null,
                }
              : prev
          );
          return;
        }

        const currentItems = itemsRef.current;
        const index = currentItems.findIndex((entry) => entry.id === itemId);
        if (index < 0) return;
        // Mirror the server's write: the copy takes `source.position + 1` and
        // every later Item shifts down one, so local positions stay equal to
        // the persisted ones (gaps included) and the next append cannot
        // collide.
        const sourcePosition = currentItems[index].position;
        const shifted = currentItems.map((entry) =>
          entry.position > sourcePosition
            ? { ...entry, position: entry.position + 1 }
            : entry
        );
        const nextItems = [
          ...shifted.slice(0, index + 1),
          toSongListItem(item),
          ...shifted.slice(index + 1),
        ];
        commitItems(nextItems);
        // The copy changes the laid-out timeline, so the cached Songset
        // duration must be recomputed from the resulting Items.
        setSongset((prevSongset) =>
          prevSongset
            ? {
                ...prevSongset,
                itemCount: nextItems.length,
                durationSeconds: getSongsetDurationSeconds(nextItems.map(toArrangementInput)),
                renderState: "stale" as RenderState,
                isArtifactsStale: true,
                renderErrorMessage: null,
                failedAt: null,
              }
            : prevSongset
        );
        touchUpdatedAt();
      }),
    [commitItems, runItemMutation, songsetId, t, touchUpdatedAt]
  );

  // Handle transition update
  const handleUpdateTransition = useCallback(
    async (itemId: string, settings: TransitionSettings) => {
      const response = await fetch(`/api/songsets/${songsetId}/items`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          itemId,
          gapBeats: settings.gapBeats,
          crossfadeEnabled: settings.crossfadeEnabled ? 1 : 0,
          crossfadeDurationSeconds: settings.crossfadeDurationSeconds,
          keyShiftSemitones: settings.keyShiftSemitones,
          tempoRatio: settings.tempoRatio,
        }),
      });

      if (!response.ok) {
        throw new Error(t("songsets.error.updateTransitionFailed"));
      }

      // Update local state
      setItems((prev) => {
        const next = prev.map((item) =>
          item.id === itemId
            ? {
                ...item,
                gapBeats: settings.gapBeats,
                crossfadeEnabled: settings.crossfadeEnabled ? 1 : 0,
                crossfadeDurationSeconds: settings.crossfadeDurationSeconds,
                keyShiftSemitones: settings.keyShiftSemitones,
                tempoRatio: settings.tempoRatio,
              }
            : item
        );
        itemsRef.current = next;
        return next;
      });
      markStale();
    },
    [songsetId, markStale, t]
  );

  // AI-transition switch/beat changes: free PATCH, no job and no quota. The
  // sheet rolls its optimistic state back when this rejects.
  const handleUpdateGlue = useCallback(
    async (
      itemId: string,
      patch: {
        glueEnabled?: boolean;
        glueBeats?: 2 | 4 | 8 | 16;
        glueActiveTakeId?: string | null;
      }
    ) => {
      let response: Response;
      try {
        response = await fetch(`/api/songsets/${songsetId}/items`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ itemId, ...patch }),
        });
      } catch {
        throw new Error(t("glue.error.save"));
      }

      if (!response.ok) {
        throw new Error(t("glue.error.save"));
      }

      const detail = (await response.json().catch(() => null)) as
        | ApiSongsetItem
        | null;
      if (detail) {
        setItems((prev) =>
          prev.map((item) =>
            item.id === itemId ? toSongListItem(detail) : item
          )
        );
      }
      markStale();
    },
    [songsetId, markStale, t]
  );

  // Save the sheet's draft as this item's arrangement (one PUT; no autosave,
  // no render enqueue). Throws a translated error so the sheet stays open.
  const handleSaveArrangement = useCallback(
    async (arrangement: SongArrangement | null) => {
      if (!editingArrangementItemId) return;
      const savedItemId = editingArrangementItemId;

      let response: Response;
      try {
        response = await fetch(
          `/api/songsets/${songsetId}/items/${savedItemId}/arrangement`,
          {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ arrangement }),
          }
        );
      } catch {
        // Network failure has no HTTP status: surface the generic translated
        // save error instead of the raw "Failed to fetch".
        throw new Error(t("arrangement.error.saveFailed"));
      }

      if (!response.ok) {
        let code: string | null = null;
        try {
          const data = await response.json();
          code = typeof data?.code === "string" ? data.code : null;
        } catch {
          // Non-JSON error bodies fall through to the generic message.
        }
        throw new Error(
          code === "SONGSET_TOO_LONG"
            ? t("arrangement.error.tooLong")
            : code === "ARRANGEMENT_ANALYSIS_UNAVAILABLE" ||
                code === "ARRANGEMENT_STALE"
              ? t("arrangement.error.unavailableOrStale")
              : response.status === 401
                ? t("arrangement.error.unauthenticated")
                : t("arrangement.error.saveFailed")
        );
      }

      // #283 responses are the editor detail shape; accept it under `item` or
      // at the top level, and fall back to the draft we just saved.
      let detail: ArrangementDetailFields | null = null;
      try {
        const data: unknown = await response.json();
        const candidate =
          typeof data === "object" && data !== null && "item" in data
            ? data.item
            : data;
        detail = readArrangementDetail(candidate);
      } catch {
        // A 200 without a body still saved the arrangement.
      }

      const savedSteps =
        detail && detail.arrangementSteps !== undefined
          ? detail.arrangementSteps
          : arrangement === null
            ? null
            : JSON.stringify(arrangement);
      // Apply the saved arrangement to the CURRENT items, not a captured
      // snapshot: a transition edit committed while the PUT was in flight must
      // survive. The Songset duration derives from the same next state.
      setItems((prev) => {
        const nextItems = prev.map((item) =>
          item.id === savedItemId
            ? applySavedArrangement(item, savedSteps, detail)
            : item
        );
        itemsRef.current = nextItems;
        setSongset((prevSongset) =>
          prevSongset
            ? {
                ...prevSongset,
                durationSeconds: getSongsetDurationSeconds(
                  nextItems.map(toArrangementInput)
                ),
              }
            : prevSongset
        );
        return nextItems;
      });
      markStale();
      toast.success(
        arrangement === null
          ? t("arrangement.toast.reset")
          : t("arrangement.toast.saved")
      );
    },
    [editingArrangementItemId, songsetId, markStale, t]
  );

  // Handle render
  const handleRender = useCallback(() => {
    router.push(`/songsets/${songsetId}/render`);
  }, [songsetId, router]);

  // Handle worship playback
  const handleWorshipPlayback = useCallback(() => {
    // Cached ⇒ offline copy (cache-first controller boot): a full document
    // navigation is deterministic regardless of connectivity — the SW
    // document route serves the pre-cached controller. Not positively
    // online (probed Offline, or Unknown — issue #211's
    // fail-toward-offline): same full-document path. SPA navigation needs
    // an RSC fetch that dead-ends offline (issue #206's trap, at editor
    // granularity).
    if (isOfflineAvailable || connectivity !== "online") {
      window.location.assign(`/songsets/${songsetId}/play/controller`);
      return;
    }
    router.push(`/songsets/${songsetId}/play/controller`);
  }, [isOfflineAvailable, connectivity, songsetId, router]);

  // Handle Listen: unlock the shared context inside the click, then start the
  // audition. The provider re-fetches saved arrangements, so unsaved sheet
  // edits are not audible until Apply.
  const handleListen = useCallback(() => {
    unlockAudioContext();
    open({ songsetId, name: songset?.name ?? "" });
  }, [open, songset?.name, songsetId]);

  // Handle retry
  const handleRetry = useCallback(() => {
    router.push(`/songsets/${songsetId}/render`);
  }, [songsetId, router]);

  // Handle description update
  const handleUpdateDescription = useCallback(
    async (description: string) => {
      const response = await fetch(`/api/songsets/${songsetId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ description }),
      });

      if (!response.ok) {
        throw new Error(t("songsets.error.updateDescriptionFailed"));
      }

      // Update local state
      setSongset((prev) =>
        prev ? { ...prev, description } : null
      );
      touchUpdatedAt();
    },
    [songsetId, t, touchUpdatedAt]
  );

  // Handle name update (rename)
  const handleUpdateName = useCallback(
    async (name: string) => {
      const response = await fetch(`/api/songsets/${songsetId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });

      if (!response.ok) {
        throw new Error(t("songsets.error.renameFailed"));
      }

      // Update local state
      setSongset((prev) =>
        prev ? { ...prev, name } : null
      );
      touchUpdatedAt();
    },
    [songsetId, t, touchUpdatedAt]
  );

  // Handle duplicate
  const handleDuplicate = useCallback(async () => {
    const response = await fetch(`/api/songsets/${songsetId}/duplicate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: `${t("songsets.copyOfPrefix")}${songset?.name ?? ""}`,
        description: songset?.description,
      }),
    });

    if (!response.ok) {
      const data = await response.json();
      throw new Error(data.error || t("songsets.error.duplicateFailed"));
    }

    const newSongset = await response.json();

    // Navigate to the new songset
    router.push(`/songsets/${newSongset.id}`);
  }, [songsetId, router, songset, t]);

  // Handle delete
  const handleDelete = useCallback(async () => {
    const response = await fetch(`/api/songsets/${songsetId}`, {
      method: "DELETE",
    });

    if (!response.ok) {
      throw new Error(t("songsets.error.deleteFailed"));
    }

    // Best-effort: clear the songset's offline cache copy (issue #203).
    await removeOfflineSongset(songsetId);
  }, [songsetId, t]);

  // Handle share
  const handleShare = useCallback(() => {
    setShareDialogOpen(true);
  }, []);

  // Backward compat: ?share=true opens dialog then cleans URL
  useEffect(() => {
    if (isShare) {
      router.replace(`/songsets/${songsetId}`);
    }
  }, [isShare, songsetId, router]);

  // Handle download audio
  const handleDownloadAudio = useCallback(async () => {
    if (!songset?.lastCompletedRenderJobId) return;
    const toastId = toast.loading(t("songsets.toast.preparingDownload"));
    try {
      await fetchSignedUrlAndDownload(
        songset.lastCompletedRenderJobId,
        "audio",
        sanitizeFilename(songset.name),
        "mp3"
      );
      toast.success(t("songsets.toast.downloadStarted"), { id: toastId });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("songsets.error.downloadAudioFailed"), { id: toastId });
    }
  }, [songset, t]);

  // Handle download video
  const handleDownloadVideo = useCallback(async () => {
    if (!songset?.lastCompletedRenderJobId) return;
    const toastId = toast.loading(t("songsets.toast.preparingDownload"));
    try {
      await fetchSignedUrlAndDownload(
        songset.lastCompletedRenderJobId,
        "video",
        sanitizeFilename(songset.name),
        "mp4"
      );
      toast.success(t("songsets.toast.downloadStarted"), { id: toastId });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("songsets.error.downloadVideoFailed"), { id: toastId });
    }
  }, [songset, t]);

  // Handle add songs
  const handleAddSongs = useCallback(() => {
    setIsBrowseSheetOpen(true);
  }, []);

  const handleAddSong = useCallback(
    async (song: SongCardData) => {
      // BrowseSheet disables its cards at the cap; guard the callback too so a
      // stale card or a queued tap can never append a sixth Item.
      if (isSongsetFull) return;
      return runItemMutation(async () => {
        const primaryRecording = song.recordings[0];

        const response = await fetch(`/api/songsets/${songsetId}/items`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            songId: song.id,
            recordingHashPrefix: primaryRecording?.hashPrefix ?? null,
          }),
        });

        if (!response.ok) {
          throw new Error(t("songsets.error.addSongFailed"));
        }

        const item: ApiSongsetItem = await response.json();

        commitItems([...itemsRef.current, toSongListItem(item)]);

        setSongset((prev) =>
          prev
            ? {
                ...prev,
                itemCount: prev.itemCount + 1,
                renderState: "stale" as RenderState,
                isArtifactsStale: true,
                renderErrorMessage: null,
                failedAt: null,
              }
            : prev
        );
        touchUpdatedAt();
      });
    },
    [commitItems, isSongsetFull, runItemMutation, songsetId, t, touchUpdatedAt]
  );

  const editingArrangementItem = editingArrangementItemId
    ? items.find((item) => item.id === editingArrangementItemId) ?? null
    : null;

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
      </div>
    );
  }

  if (error || !songset) {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen p-4">
        <p className="text-destructive text-center">{error || t("songsets.error.notFound")}</p>
        <button
          onClick={() => backToList()}
          className="mt-4 text-primary hover:underline"
        >
          {t("songsets.backToSongsets")}
        </button>
      </div>
    );
  }
  return (
    <>
      <SongsetEditor
        songset={songset}
        items={items}
        onUpdateItems={handleUpdateItems}
        onRemoveItem={handleRemoveItem}
        onUpdateTransition={handleUpdateTransition}
        onEditArrangement={setEditingArrangementItemId}
        onDuplicateItem={handleDuplicateItem}
        onRender={handleRender}
        onCancelRender={handleCancelRender}
        isCancellingRender={isCancellingRender}
        onPlay={handleWorshipPlayback}
        onListen={handleListen}
        onRetry={handleRetry}
        onUpdateDescription={handleUpdateDescription}
        onRename={handleUpdateName}
        onDuplicate={handleDuplicate}
        onDelete={handleDelete}
        onShare={handleShare}
        onDownloadAudio={handleDownloadAudio}
        onDownloadVideo={handleDownloadVideo}
        onAddSongs={handleAddSongs}
        isRemoving={isRemoving}
        isItemMutationPending={isItemMutationPending}
        highlightSongId={highlightSongId}
        onHighlightConsumed={() => setHighlightSongId(null)}
        isOfflineAvailable={isOfflineAvailable}
        offlineStatusSlot={
          songset && renderJobR2Keys ? (
            <div className="px-4 pt-3">
              <OfflineStatus
                songsetId={songsetId}
                songsetName={songset.name}
                renderJobId={renderJobR2Keys.id}
                mp3R2Key={renderJobR2Keys.mp3R2Key}
                mp4R2Key={renderJobR2Keys.mp4R2Key}
                chaptersR2Key={renderJobR2Keys.chaptersR2Key}
              />
            </div>
          ) : null
        }
      />
      <BrowseSheet
        isOpen={isBrowseSheetOpen}
        onOpenChange={setIsBrowseSheetOpen}
        onAddSong={handleAddSong}
        existingSongCounts={items.reduce<Record<string, number>>((counts, item) => {
          counts[item.songId] = (counts[item.songId] ?? 0) + 1;
          return counts;
        }, {})}
        itemCount={items.length}
      />
      {editingArrangementItem && (
        <ArrangementEditorSheet
          key={editingArrangementItem.id}
          isOpen
          onOpenChange={(open) => {
            if (!open) setEditingArrangementItemId(null);
          }}
          item={editingArrangementItem}
          items={items}
          onSave={handleSaveArrangement}
          songsetId={songsetId}
          onUpdateGlue={handleUpdateGlue}
        />
      )}
      <ShareDialog
        open={shareDialogOpen}
        onOpenChange={setShareDialogOpen}
        songsetId={songsetId}
        songsetName={songset.name}
        durationSeconds={songset.durationSeconds ?? null}
        renderJobId={songset.lastCompletedRenderJobId ?? undefined}
      />
    </>
  );
}
