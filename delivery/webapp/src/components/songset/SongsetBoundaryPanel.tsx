"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { useAudioPlayerContext } from "@/contexts/AudioPlayerContext";
import { useSongsetListen } from "@/contexts/SongsetListenContext";
import { useGlueJob } from "@/hooks/useGlueJob";
import { useLocale } from "@/hooks/useLocale";
import { buildArrangementTimeline, type TimelineItemSource } from "@/lib/arrangements";
import { unlockAudioContext } from "@/lib/audio/audio-context";
import { createSongsetAudioSession, type SongsetAudioSession } from "@/lib/audio/songset-audio";
import {
  cancelGlueJobRequest, createGlueJobRequest, fetchActiveGlueJobs,
  redispatchGlueJobRequest, resumeGlueJobRequest, retryGlueProcessingRequest,
  type GlueTakeMode,
} from "@/lib/glue/client";
import type { BoundarySettings, SongsetBoundary } from "@/lib/glue/boundary-types";
import type { GlueBeats } from "@/lib/glue/descriptor";
import type { GlueQuoteFields } from "@/lib/glue/quote";
import type { ResolvedSongsetBoundary } from "@/lib/glue/resolve";
import type { TranslationKey } from "@/lib/i18n/messages";

interface Props {
  songsetId: string;
  boundaries: ResolvedSongsetBoundary[];
  sources: TimelineItemSource[];
  disabled: boolean;
  onSave: (target: SongsetBoundary, patch: Partial<BoundarySettings>) => Promise<void>;
  onRefresh: () => Promise<void>;
}

export function SongsetBoundaryPanel({ boundaries, sources, ...props }: Props) {
  const { t } = useLocale();
  const player = useAudioPlayerContext();
  const listen = useSongsetListen();
  const previewRef = useRef<SongsetAudioSession | null>(null);
  const [previewTarget, setPreviewTarget] = useState<SongsetBoundary | null>(null);
  const [previewError, setPreviewError] = useState<SongsetBoundary | null>(null);

  const stopPreview = useCallback(() => {
    previewRef.current?.destroy();
    previewRef.current = null;
    setPreviewTarget(null);
  }, []);
  useEffect(() => () => { previewRef.current?.destroy(); }, []);
  useEffect(() => {
    if (!listen.request || !previewRef.current) return;
    // A new Listen session owns the transport.
    stopPreview();
  }, [listen.request, stopPreview]);

  function preview(boundary: ResolvedSongsetBoundary) {
    const { target, selectedTake, previewBarSeconds, fromItemId } = boundary;
    const source = sources.find((item) => item.itemId === fromItemId);
    if (!selectedTake || !source || !previewBarSeconds) return;
    stopPreview();
    if (previewTarget === target) return;
    unlockAudioContext();
    listen.close();
    player.stop();
    setPreviewError(null);
    const items = [{ ...source, [target]: selectedTake }];
    const timeline = buildArrangementTimeline(items);
    const song = timeline.items[0];
    const bar = Math.min(previewBarSeconds, song.durationSeconds);
    const start = target === "intro" ? 0 : song.startSeconds + song.durationSeconds - bar;
    const end = target === "intro" ? song.startSeconds + bar : timeline.totalSeconds;
    const session = createSongsetAudioSession(items, {
      endAtSeconds: end,
      onGlueFailure: () => {
        if (previewRef.current !== session) return;
        stopPreview();
        setPreviewError(target);
      },
    });
    previewRef.current = session;
    setPreviewTarget(target);
    session.onStateChange(() => {
      if (previewRef.current === session && session.getTimelinePositionSeconds() >= end) {
        stopPreview();
      }
    });
    void session.startAtSeconds(start).catch(() => {
      if (previewRef.current !== session) return;
      stopPreview();
      setPreviewError(target);
    });
  }

  const visible = boundaries.filter((boundary) => boundary.eligible || boundary.suspended);
  if (!visible.length) return null;
  return (
    <section id="ai-transitions" aria-labelledby="ai-transitions-heading"
      className="mx-4 my-3 rounded-lg border bg-card p-4">
      <h2 id="ai-transitions-heading" className="mb-3 font-semibold">{t("glue.boundary.heading")}</h2>
      <div className="grid gap-4 md:grid-cols-2">
        {visible.map((boundary) => (
          <BoundaryControl key={boundary.target} {...props} boundary={boundary}
            onPreview={() => preview(boundary)}
            previewing={previewTarget === boundary.target}
            previewFailed={previewError === boundary.target}
            onStopPreview={stopPreview}
          />
        ))}
      </div>
    </section>
  );
}

function BoundaryControl({
  boundary, songsetId, disabled, onSave, onRefresh, onPreview, previewing,
  previewFailed, onStopPreview,
}: Omit<Props, "boundaries" | "sources"> & {
  boundary: ResolvedSongsetBoundary;
  onPreview: () => void;
  previewing: boolean;
  previewFailed: boolean;
  onStopPreview: () => void;
}) {
  const { t } = useLocale();
  const { target, eligible, suspended, glueEnabled, descriptorKey } = boundary;
  const label = t(`glue.boundary.${target}`);
  const controlId = `songset-${target}`;
  const [jobId, setJobId] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<TranslationKey | null>(null);
  const [confirmation, setConfirmation] = useState<{
    quote: GlueQuoteFields; takeMode: GlueTakeMode; requestId: string;
  } | null>(null);
  const watching = jobId ?? boundary.activeJob?.id ?? null;
  const job = useGlueJob({ jobId: watching, songsetId, fromItemId: boundary.fromItemId || target, target });
  const status = job.status?.id === watching ? job.status : null;
  const currentStatus = status?.descriptorKey === descriptorKey ? status : null;
  const active = status?.active ?? Boolean(watching && (!job.terminal || boundary.activeJob));
  const settledRef = useRef<string | null>(null);
  useEffect(() => {
    if (watching || !glueEnabled || !eligible) return;
    let cancelled = false;
    void fetchActiveGlueJobs(songsetId).then((result) => {
      if (cancelled || !result.ok) return;
      const latest = result.data.latestByBoundary?.[target];
      if (latest && (latest.active || (latest.descriptorKey === descriptorKey &&
          ["failed", "dispatch-failed"].includes(latest.phase)))) {
        setJobId(latest.id);
      }
    });
    return () => { cancelled = true; };
  }, [watching, glueEnabled, eligible, songsetId, target, descriptorKey]);
  useEffect(() => {
    if (!job.terminal || !status) return;
    const key = `${status.id}:${status.attemptId}:${status.phase}`;
    if (settledRef.current === key) return;
    settledRef.current = key;
    void onRefresh();
  }, [job.terminal, status, onRefresh]);

  async function save(patch: Partial<BoundarySettings>) {
    setError(null);
    setPending(true);
    onStopPreview();
    try {
      await onSave(target, patch);
    } catch {
      setError("glue.error.save");
    } finally {
      setPending(false);
    }
  }

  async function quoteGeneration() {
    setError(null);
    setPending(true);
    const takeMode = boundary.selectedTake || boundary.staleTake || boundary.availableTakes.length
      ? "new-take" : "initial";
    try {
      const response = await fetch(
        `/api/songsets/${encodeURIComponent(songsetId)}/glue-quote?${new URLSearchParams({
          target, fromItemId: boundary.fromItemId, takeMode,
        })}`, { cache: "no-store", signal: AbortSignal.timeout(30_000) }
      );
      if (!response.ok) throw new Error("Quote unavailable");
      const quote = await response.json() as GlueQuoteFields;
      setConfirmation({ quote, takeMode, requestId: crypto.randomUUID() });
    } catch {
      setError("glue.error.quote");
    } finally {
      setPending(false);
    }
  }

  async function generate() {
    if (!confirmation) return;
    const { quote, takeMode, requestId } = confirmation;
    setPending(true);
    setError(null);
    try {
      const result = await createGlueJobRequest(songsetId, {
        target, takeMode, clientRequestId: requestId, fromItemId: quote.fromItemId,
        expectedFromUpdatedAt: quote.expectedFromUpdatedAt,
        expectedToUpdatedAt: quote.expectedToUpdatedAt,
        expectedDescriptorKey: quote.descriptorKey,
      });
      if (!result.ok) {
        if (result.error.jobId) setJobId(result.error.jobId);
        setError(result.error.code === "GLUE_STALE_SNAPSHOT"
          ? "glue.error.staleSnapshot" : "glue.error.unknown");
      } else if (result.data.status === "ready") {
        await onRefresh();
      } else {
        setJobId(result.data.jobId);
      }
    } finally {
      setPending(false);
      setConfirmation(null);
    }
  }

  async function recover(cancel = false) {
    if (!watching) return;
    setPending(true);
    setError(null);
    try {
      const action = cancel ? cancelGlueJobRequest
        : status?.phase === "dispatch-failed" ? redispatchGlueJobRequest
        : status?.providerContacted && status.errorCode === "GLUE_RETRY_PROCESSING_FREE"
          ? retryGlueProcessingRequest : resumeGlueJobRequest;
      const result = await action(watching);
      if (result.ok) {
        await job.refresh();
        if (!cancel) job.restart();
        await onRefresh();
      } else {
        setError("glue.error.unknown");
      }
    } finally {
      setPending(false);
    }
  }

  const locked = disabled || pending;
  const phase = active ? status?.phase ?? boundary.activeJob?.phase ?? "queued"
    : currentStatus?.phase === "dispatch-failed" ? "failed" : boundary.state;
  const showRetry = !active && (currentStatus?.phase === "dispatch-failed" || currentStatus?.retryKind === "free");
  const quota = confirmation?.quote;
  return (
    <div className="min-w-0 space-y-3 rounded-md border p-3" data-testid={controlId}>
      <div className="flex items-center justify-between gap-3">
        <label className="text-sm font-medium" htmlFor={controlId}>{label}</label>
        <Switch id={controlId} checked={glueEnabled} disabled={locked}
          onCheckedChange={(enabled) => void save({ enabled })} />
      </div>
      {suspended && <p className="text-sm text-muted-foreground">{t("glue.boundary.suspended")}</p>}
      {eligible && glueEnabled && (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <label className="text-sm" htmlFor={`${controlId}-beats`}>{t("glue.beatsLabel")}</label>
            <select id={`${controlId}-beats`} value={boundary.glueBeats} disabled={locked}
              className="rounded-md border bg-background px-2 py-1 text-sm"
              onChange={(event) => void save({ beats: Number(event.target.value) as GlueBeats })}>
              {[2, 4, 8, 16].map((beats) => <option key={beats} value={beats}>
                {t("glue.beatsOption").replace("{beats}", String(beats))}
              </option>)}
            </select>
          </div>
          <p className="text-sm text-muted-foreground" aria-live="polite">
            {boundary.unavailableReason === "no-tempo" ? t("glue.boundary.tempo")
              : !active && phase === "stale" ? t("glue.reason.stale")
              : t(`glue.state.${phase}` as TranslationKey)}
          </p>
          {boundary.availableTakes.length > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              <label className="text-sm" htmlFor={`${controlId}-history`}>{t("glue.takes.label")}</label>
              <select id={`${controlId}-history`} className="max-w-full rounded-md border bg-background px-2 py-1 text-sm"
                value={boundary.selectedTake?.id ?? ""} disabled={locked || active}
                onChange={(event) => void save({ takeId: event.target.value })}>
                <option value="" disabled>{t("glue.boundary.restore")}</option>
                {boundary.availableTakes.map((take) => <option key={take.id} value={take.id}>
                  {t("glue.boundary.take").replace("{number}", String(take.ordinal)).replace("{seconds}", take.seconds.toFixed(2))}
                </option>)}
              </select>
            </div>
          )}
          <div className="flex flex-wrap gap-2">
            {boundary.selectedTake && (
              <Button type="button" size="sm" variant="outline" onClick={onPreview} disabled={disabled}>
                {t(previewing ? "glue.boundary.stop" : "glue.boundary.preview")}
              </Button>
            )}
            {active && status && !status.providerContacted && (
              <Button type="button" size="sm" variant="outline" onClick={() => void recover(true)} disabled={locked}>
                {t("glue.boundary.cancel")}
              </Button>
            )}
            {showRetry && (
              <Button type="button" size="sm" variant="outline" onClick={() => void recover()} disabled={locked}>
                {t("glue.boundary.retry")}
              </Button>
            )}
            {!active && (
              <Button type="button" size="sm" onClick={() => void quoteGeneration()}
                disabled={locked || !descriptorKey || boundary.unavailableReason === "active-job"}>
                {t(boundary.selectedTake || boundary.staleTake ? "glue.boundary.regenerate" : "glue.boundary.generate")}
              </Button>
            )}
          </div>
        </>
      )}
      {(error || previewFailed) && (
        <p className="text-sm text-destructive" role="alert">{t(error ?? "glue.boundary.previewFailed")}</p>
      )}
      <AlertDialog open={confirmation !== null} onOpenChange={(open) => { if (!open && !pending) setConfirmation(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{label}</AlertDialogTitle>
            <AlertDialogDescription>
              {quota && t("glue.boundary.confirm").replace("{seconds}", quota.generationSeconds.toFixed(2))}
              {quota && quota.quotaLimit !== null && (
                <span className="mt-2 block">{t("glue.remaining")
                  .replace("{remaining}", String(quota.quotaRemaining))
                  .replace("{limit}", String(quota.quotaLimit))}</span>
              )}
              {quota?.blockedSides[0] && (
                <span className="mt-2 block">{t(quota.blockedSides[0].code === "GLUE_EXCERPT_TOO_SHORT"
                  ? "glue.reason.tooShort" : "glue.reason.copyright")}</span>
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("glue.confirm.dismiss")}</AlertDialogCancel>
            <AlertDialogAction disabled={pending || !quota || quota.quotaRemaining <= 0 || quota.blockedSides.length > 0}
              onClick={(event) => { event.preventDefault(); void generate(); }}>
              {t("glue.confirm.confirm")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
