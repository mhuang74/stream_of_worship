"use client";

// PROTOTYPE (throwaway): measures the spec songset_preview_with_arrangement.md
// browser engine; delete before production merge.
//
// Prepares every item up front (fetch -> decodeAudioData -> clip extraction)
// and reports fetch/decode latency and retained memory, then plays the
// crossfade timeline through AudioBufferSourceNodes scheduled on the
// AudioContext clock. Preparing everything measures worst-case latency
// deliberately and sidesteps the spec's late-decode silence gap.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  buildArrangementTimeline,
  type ArrangementTimelineItem,
  type TimelineItemSource,
} from "@/lib/arrangements";

// Spec §6 guard: above this total the local tier defers to the worker mix.
const SPEC_MEMORY_GUARD_SECONDS = 420;

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

interface ItemMetrics {
  itemId: string;
  hashPrefix: string;
  fetchMs: number;
  decodeMs: number;
  bytes: number;
  decodedSeconds: number;
  sampleRate: number;
  channels: number;
  clipBytes: number;
  clipCount: number;
}

interface ClipBuffer {
  buffer: AudioBuffer;
  startSeconds: number;
  endSeconds: number;
}

interface GainPoint {
  t: number;
  v: number;
}

type Phase = "idle" | "preparing" | "ready" | "playing" | "error";

function readHeapBytes(): number | null {
  const perf = performance as Performance & {
    memory?: { usedJSHeapSize: number };
  };
  return perf.memory ? perf.memory.usedJSHeapSize : null;
}

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${bytes} B`;
}

function formatSeconds(seconds: number): string {
  const total = Math.max(0, seconds);
  const minutes = Math.floor(total / 60);
  const rest = total - minutes * 60;
  return `${minutes}:${rest.toFixed(2).padStart(5, "0")}`;
}

// Gain automation breakpoints for one item, in absolute timeline seconds,
// mirroring the worker's sequential afade pair: the incoming item owns the
// crossfade (0 -> 1 over its own crossfadeSeconds) and fades out over the
// successor's crossfadeSeconds window.
function gainPoints(
  item: ArrangementTimelineItem,
  nextItem: ArrangementTimelineItem | null,
): GainPoint[] {
  const itemEnd = item.startSeconds + item.durationSeconds;
  const points: GainPoint[] = [];
  if (item.crossfadeSeconds > 0) {
    points.push({ t: item.startSeconds, v: 0 });
    points.push({ t: item.startSeconds + item.crossfadeSeconds, v: 1 });
  } else {
    points.push({ t: item.startSeconds, v: 1 });
  }
  if (nextItem && nextItem.crossfadeSeconds > 0) {
    points.push({ t: itemEnd - nextItem.crossfadeSeconds, v: 1 });
    points.push({ t: itemEnd, v: 0 });
  }
  points.sort((a, b) => a.t - b.t);
  return points;
}

function gainValueAt(points: GainPoint[], t: number): number {
  if (points.length === 0) return 1;
  if (t <= points[0].t) return points[0].v;
  for (let i = 1; i < points.length; i++) {
    const prev = points[i - 1];
    const next = points[i];
    if (t <= next.t) {
      if (next.t === prev.t) return next.v;
      return prev.v + ((next.v - prev.v) * (t - prev.t)) / (next.t - prev.t);
    }
  }
  return points[points.length - 1].v;
}

export function PreviewLatencyClient({
  songsetId,
  songsetName,
  items,
}: {
  songsetId: string;
  songsetName: string;
  items: TimelineItemSource[];
}) {
  const timeline = useMemo(() => buildArrangementTimeline(items), [items]);
  const totalSeconds = timeline.totalSeconds;

  const [phase, setPhase] = useState<Phase>("idle");
  const [prepareError, setPrepareError] = useState<string | null>(null);
  const [metrics, setMetrics] = useState<Array<ItemMetrics | null>>(() =>
    items.map(() => null),
  );
  const [prepareWallMs, setPrepareWallMs] = useState<number | null>(null);
  const [heapDeltaBytes, setHeapDeltaBytes] = useState<number | null>(null);
  const [positionSeconds, setPositionSeconds] = useState(0);

  const ctxRef = useRef<AudioContext | null>(null);
  const clipBuffersRef = useRef<ClipBuffer[][]>([]);
  const nodesRef = useRef<{ sources: AudioBufferSourceNode[]; gains: GainNode[] }>(
    { sources: [], gains: [] },
  );
  const positionRef = useRef(0);
  const startPositionRef = useRef(0);
  const startedCtxTimeRef = useRef(0);

  const preparedCount = metrics.filter(Boolean).length;

  const stopAll = useCallback(() => {
    const { sources, gains } = nodesRef.current;
    for (const source of sources) {
      try {
        source.stop();
      } catch {
        // Already stopped; nothing to release.
      }
      source.disconnect();
    }
    for (const gain of gains) gain.disconnect();
    nodesRef.current = { sources: [], gains: [] };
  }, []);

  const scheduleFrom = useCallback(
    (position: number) => {
      const ctx = ctxRef.current;
      if (!ctx) return;
      stopAll();
      const now = ctx.currentTime;
      const sources: AudioBufferSourceNode[] = [];
      const gains: GainNode[] = [];

      timeline.items.forEach((item, index) => {
        const itemEnd = item.startSeconds + item.durationSeconds;
        if (itemEnd <= position) return;

        const gain = ctx.createGain();
        gain.connect(ctx.destination);

        const points = gainPoints(item, timeline.items[index + 1] ?? null);
        gain.gain.setValueAtTime(gainValueAt(points, position), now);
        for (const point of points) {
          if (point.t <= position) continue;
          gain.gain.linearRampToValueAtTime(point.v, now + (point.t - position));
        }

        const clipBuffers = clipBuffersRef.current[index] ?? [];
        for (const clip of clipBuffers) {
          if (clip.endSeconds <= position) continue;
          const offsetInClip = Math.min(
            Math.max(0, position - clip.startSeconds),
            clip.buffer.duration,
          );
          const duration = Math.min(
            clip.buffer.duration,
            clip.endSeconds - clip.startSeconds,
          ) - offsetInClip;
          if (duration <= 0) continue;
          const source = ctx.createBufferSource();
          source.buffer = clip.buffer;
          source.connect(gain);
          source.start(now + Math.max(0, clip.startSeconds - position), offsetInClip, duration);
          sources.push(source);
        }

        gains.push(gain);
      });

      nodesRef.current = { sources, gains };
    },
    [stopAll, timeline],
  );

  useEffect(() => {
    if (phase !== "playing") return;
    const id = window.setInterval(() => {
      const ctx = ctxRef.current;
      if (!ctx) return;
      const raw =
        startPositionRef.current + (ctx.currentTime - startedCtxTimeRef.current);
      if (raw >= totalSeconds) {
        stopAll();
        positionRef.current = totalSeconds;
        startPositionRef.current = totalSeconds;
        setPositionSeconds(totalSeconds);
        setPhase("ready");
        return;
      }
      positionRef.current = raw;
      setPositionSeconds(raw);
    }, 250);
    return () => window.clearInterval(id);
  }, [phase, totalSeconds, stopAll]);

  useEffect(() => {
    return () => {
      stopAll();
      void ctxRef.current?.close();
      ctxRef.current = null;
    };
  }, [stopAll]);

  const handlePrepare = async () => {
    setPhase("preparing");
    setPrepareError(null);
    setMetrics(items.map(() => null));
    setPrepareWallMs(null);
    setHeapDeltaBytes(null);
    clipBuffersRef.current = [];
    stopAll();
    positionRef.current = 0;
    setPositionSeconds(0);

    const ctx = ctxRef.current ?? new AudioContext({ sampleRate: 44100 });
    ctxRef.current = ctx;
    await ctx.resume();

    const heapBefore = readHeapBytes();
    const wallStart = performance.now();

    for (let index = 0; index < items.length; index++) {
      const item = items[index];
      const timelineItem = timeline.items[index];
      const hashPrefix = item.recordingHashPrefix;
      if (!hashPrefix) {
        setPhase("error");
        setPrepareError(`Item ${item.itemId}: recordingHashPrefix is null`);
        return;
      }
      try {
        const fetchStart = performance.now();
        const response = await fetch(`/api/recordings/${hashPrefix}/audio`);
        if (!response.ok) {
          throw new Error(`HTTP ${response.status} fetching ${hashPrefix}`);
        }
        const body = await response.arrayBuffer();
        // decodeAudioData detaches the ArrayBuffer, so record its size first.
        const bytes = body.byteLength;
        const fetchEnd = performance.now();

        const decoded = await ctx.decodeAudioData(body);
        const decodeEnd = performance.now();

        // Spec §6 ensureDecoded clip extraction, verbatim: one AudioBuffer per
        // clip holding only the clip's source range.
        const sampleRate = decoded.sampleRate;
        const clipBuffers: ClipBuffer[] = [];
        let clipBytes = 0;
        for (const clip of timelineItem.clips) {
          const offset = Math.min(
            Math.max(Math.round(clip.sourceStartSeconds * sampleRate), 0),
            decoded.length,
          );
          const wanted = Math.round(
            (clip.sourceEndSeconds - clip.sourceStartSeconds) * sampleRate,
          );
          const length = Math.max(
            1,
            Math.min(wanted, Math.max(1, decoded.length - offset)),
          );
          const buffer = ctx.createBuffer(
            decoded.numberOfChannels,
            length,
            sampleRate,
          );
          for (let channel = 0; channel < decoded.numberOfChannels; channel++) {
            const slice = decoded.getChannelData(channel).subarray(offset, offset + length);
            buffer.copyToChannel(slice, channel);
          }
          clipBytes += length * decoded.numberOfChannels * 4;
          clipBuffers.push({
            buffer,
            startSeconds: clip.startSeconds,
            endSeconds: clip.endSeconds,
          });
        }
        clipBuffersRef.current[index] = clipBuffers;

        const record: ItemMetrics = {
          itemId: item.itemId,
          hashPrefix,
          fetchMs: fetchEnd - fetchStart,
          decodeMs: decodeEnd - fetchEnd,
          bytes,
          decodedSeconds: decoded.duration,
          sampleRate,
          channels: decoded.numberOfChannels,
          clipBytes,
          clipCount: clipBuffers.length,
        };
        // `decoded` is dropped here: only the clip buffers survive.
        setMetrics((prev) => {
          const next = [...prev];
          next[index] = record;
          return next;
        });
      } catch (error) {
        setPhase("error");
        setPrepareError(
          `Item ${item.itemId} (${hashPrefix}): ${
            error instanceof Error ? error.message : String(error)
          }`,
        );
        return;
      }
    }

    setPrepareWallMs(performance.now() - wallStart);
    const heapAfter = readHeapBytes();
    setHeapDeltaBytes(
      heapBefore !== null && heapAfter !== null ? heapAfter - heapBefore : null,
    );
    setPhase("ready");
  };

  const handlePlay = async () => {
    const ctx = ctxRef.current;
    if (!ctx || preparedCount === 0 || phase === "preparing") return;
    await ctx.resume();
    const from = positionRef.current >= totalSeconds ? 0 : positionRef.current;
    scheduleFrom(from);
    positionRef.current = from;
    startPositionRef.current = from;
    startedCtxTimeRef.current = ctx.currentTime;
    setPositionSeconds(from);
    setPhase("playing");
  };

  const handlePause = () => {
    const ctx = ctxRef.current;
    if (!ctx) return;
    const raw = Math.min(
      totalSeconds,
      startPositionRef.current + (ctx.currentTime - startedCtxTimeRef.current),
    );
    stopAll();
    positionRef.current = raw;
    startPositionRef.current = raw;
    setPositionSeconds(raw);
    setPhase(preparedCount > 0 ? "ready" : "idle");
  };

  const handleStop = () => {
    stopAll();
    positionRef.current = 0;
    startPositionRef.current = 0;
    setPositionSeconds(0);
    setPhase(preparedCount > 0 ? "ready" : "idle");
  };

  const seekTo = (target: number) => {
    const clamped = Math.min(Math.max(target, 0), totalSeconds);
    positionRef.current = clamped;
    startPositionRef.current = clamped;
    setPositionSeconds(clamped);
    const ctx = ctxRef.current;
    if (phase === "playing" && ctx) {
      scheduleFrom(clamped);
      startedCtxTimeRef.current = ctx.currentTime;
    }
  };

  const totals = useMemo(() => {
    let bytes = 0;
    let decodedBytes = 0;
    let clipBytes = 0;
    let fetchMs = 0;
    let decodeMs = 0;
    let longest = 0;
    for (const record of metrics) {
      if (!record) continue;
      bytes += record.bytes;
      decodedBytes += record.decodedSeconds * record.sampleRate * record.channels * 4;
      clipBytes += record.clipBytes;
      fetchMs += record.fetchMs;
      decodeMs += record.decodeMs;
      longest = Math.max(longest, record.decodedSeconds);
    }
    return { bytes, decodedBytes, clipBytes, fetchMs, decodeMs, longest };
  }, [metrics]);

  const currentItemIndex = useMemo(() => {
    let found = -1;
    timeline.items.forEach((item, index) => {
      if (item.startSeconds <= positionSeconds) found = index;
    });
    return found;
  }, [timeline, positionSeconds]);

  const firstSampleMs = metrics[0]
    ? metrics[0].fetchMs + metrics[0].decodeMs
    : null;

  return (
    <main
      style={{
        maxWidth: 1100,
        margin: "0 auto",
        padding: "24px 20px 64px",
        display: "flex",
        flexDirection: "column",
        gap: 20,
      }}
    >
      <header>
        <h1 style={{ fontSize: 22, fontWeight: 600, margin: 0 }}>
          Preview latency &amp; sound-quality probe
        </h1>
        <p style={{ margin: "4px 0 0", opacity: 0.7, fontSize: 13 }}>
          {songsetName} · <code style={{ fontFamily: MONO }}>{songsetId}</code> ·
          spec <code style={{ fontFamily: MONO }}>songset_preview_with_arrangement.md</code>{" "}
          §6 local tier · throwaway dev page
        </p>
      </header>

      <section>
        <h2 style={{ fontSize: 15, fontWeight: 600, margin: "0 0 8px" }}>
          Timeline ({formatSeconds(totalSeconds)} total,{" "}
          {timeline.items.length} items)
        </h2>
        <table style={{ borderCollapse: "collapse", fontFamily: MONO, fontSize: 12 }}>
          <thead>
            <tr style={{ textAlign: "left", opacity: 0.6 }}>
              <th style={{ padding: "4px 12px 4px 0" }}>#</th>
              <th style={{ padding: "4px 12px 4px 0" }}>hash</th>
              <th style={{ padding: "4px 12px 4px 0" }}>start</th>
              <th style={{ padding: "4px 12px 4px 0" }}>duration</th>
              <th style={{ padding: "4px 12px 4px 0" }}>gap</th>
              <th style={{ padding: "4px 12px 4px 0" }}>crossfade</th>
              <th style={{ padding: "4px 12px 4px 0" }}>clips</th>
            </tr>
          </thead>
          <tbody>
            {timeline.items.map((item, index) => (
              <tr key={item.itemId}>
                <td style={{ padding: "2px 12px 2px 0" }}>{index + 1}</td>
                <td style={{ padding: "2px 12px 2px 0" }}>
                  {item.recordingHashPrefix ?? "—"}
                </td>
                <td style={{ padding: "2px 12px 2px 0" }}>
                  {item.startSeconds.toFixed(3)}
                </td>
                <td style={{ padding: "2px 12px 2px 0" }}>
                  {item.durationSeconds.toFixed(3)}
                </td>
                <td style={{ padding: "2px 12px 2px 0" }}>
                  {item.gapBeforeSeconds.toFixed(3)}
                </td>
                <td style={{ padding: "2px 12px 2px 0" }}>
                  {item.crossfadeSeconds.toFixed(3)}
                </td>
                <td style={{ padding: "2px 12px 2px 0" }}>{item.clips.length}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {totalSeconds > SPEC_MEMORY_GUARD_SECONDS ? (
          <p style={{ margin: "8px 0 0", fontSize: 12, color: "var(--warning, #b45309)" }}>
            {formatSeconds(totalSeconds)} exceeds spec §6&apos;s{" "}
            {SPEC_MEMORY_GUARD_SECONDS}s guard — the real engine would defer to the
            worker mix here. This page decodes everything anyway to expose the true peak.
          </p>
        ) : null}
      </section>

      <section style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <button type="button" onClick={() => void handlePrepare()} disabled={phase === "preparing"}>
          {phase === "preparing" ? "Preparing…" : "Prepare all"}
        </button>
        <button
          type="button"
          onClick={() => void (phase === "playing" ? handlePause() : handlePlay())}
          disabled={preparedCount === 0 || phase === "preparing"}
        >
          {phase === "playing" ? "Pause" : "Play"}
        </button>
        <button type="button" onClick={handleStop} disabled={preparedCount === 0}>
          Stop
        </button>
        <span style={{ fontFamily: MONO, fontSize: 12, opacity: 0.8 }}>
          {formatSeconds(positionSeconds)} / {formatSeconds(totalSeconds)}
        </span>
        <span style={{ fontFamily: MONO, fontSize: 12, opacity: 0.6 }}>
          phase: {phase}
          {currentItemIndex >= 0 ? ` · item ${currentItemIndex + 1}` : ""}
        </span>
      </section>

      <section>
        <input
          type="range"
          min={0}
          max={Math.max(totalSeconds, 0.01)}
          step={0.05}
          value={positionSeconds}
          onChange={(event) => seekTo(Number(event.target.value))}
          disabled={preparedCount === 0}
          style={{ width: "100%" }}
        />
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
          {timeline.items.map((item, index) => (
            <button
              key={item.itemId}
              type="button"
              onClick={() => seekTo(item.startSeconds)}
              disabled={preparedCount === 0}
              style={{ fontFamily: MONO, fontSize: 11 }}
            >
              jump {index + 1} @ {item.startSeconds.toFixed(2)}s
            </button>
          ))}
        </div>
        <p style={{ margin: "8px 0 0", fontSize: 12, opacity: 0.6 }}>
          Jump to an item start to audition the incoming crossfade seam; jump a
          second or two earlier to hear the whole overlap.
        </p>
      </section>

      {prepareError ? (
        <p style={{ margin: 0, color: "var(--error, #b91c1c)", fontFamily: MONO, fontSize: 12 }}>
          {prepareError}
        </p>
      ) : null}

      <section>
        <h2 style={{ fontSize: 15, fontWeight: 600, margin: "0 0 8px" }}>
          Measurements
        </h2>
        <table style={{ borderCollapse: "collapse", fontFamily: MONO, fontSize: 12 }}>
          <thead>
            <tr style={{ textAlign: "left", opacity: 0.6 }}>
              <th style={{ padding: "4px 12px 4px 0" }}>#</th>
              <th style={{ padding: "4px 12px 4px 0" }}>hash</th>
              <th style={{ padding: "4px 12px 4px 0" }}>fetch</th>
              <th style={{ padding: "4px 12px 4px 0" }}>decode</th>
              <th style={{ padding: "4px 12px 4px 0" }}>mp3</th>
              <th style={{ padding: "4px 12px 4px 0" }}>decoded</th>
              <th style={{ padding: "4px 12px 4px 0" }}>rate</th>
              <th style={{ padding: "4px 12px 4px 0" }}>ch</th>
              <th style={{ padding: "4px 12px 4px 0" }}>clips</th>
              <th style={{ padding: "4px 12px 4px 0" }}>clip MB</th>
            </tr>
          </thead>
          <tbody>
            {metrics.map((record, index) => (
              <tr key={items[index]?.itemId ?? index}>
                <td style={{ padding: "2px 12px 2px 0" }}>{index + 1}</td>
                <td style={{ padding: "2px 12px 2px 0" }}>
                  {record ? record.hashPrefix : "—"}
                </td>
                <td style={{ padding: "2px 12px 2px 0" }}>
                  {record ? `${record.fetchMs.toFixed(0)} ms` : "—"}
                </td>
                <td style={{ padding: "2px 12px 2px 0" }}>
                  {record ? `${record.decodeMs.toFixed(0)} ms` : "—"}
                </td>
                <td style={{ padding: "2px 12px 2px 0" }}>
                  {record ? formatBytes(record.bytes) : "—"}
                </td>
                <td style={{ padding: "2px 12px 2px 0" }}>
                  {record ? formatSeconds(record.decodedSeconds) : "—"}
                </td>
                <td style={{ padding: "2px 12px 2px 0" }}>
                  {record ? record.sampleRate : "—"}
                </td>
                <td style={{ padding: "2px 12px 2px 0" }}>
                  {record ? record.channels : "—"}
                </td>
                <td style={{ padding: "2px 12px 2px 0" }}>
                  {record ? record.clipCount : "—"}
                </td>
                <td style={{ padding: "2px 12px 2px 0" }}>
                  {record ? formatBytes(record.clipBytes) : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        <dl style={{ fontFamily: MONO, fontSize: 12, margin: "12px 0 0", lineHeight: 1.7 }}>
          <div>
            <dt style={{ display: "inline", opacity: 0.6 }}>
              time to first sample (item 1 fetch+decode):{" "}
            </dt>
            <dd style={{ display: "inline", margin: 0, fontWeight: 600 }}>
              {firstSampleMs === null ? "—" : `${firstSampleMs.toFixed(0)} ms`}
            </dd>
          </div>
          <div>
            <dt style={{ display: "inline", opacity: 0.6 }}>total prepare wall time: </dt>
            <dd style={{ display: "inline", margin: 0 }}>
              {prepareWallMs === null ? "—" : `${(prepareWallMs / 1000).toFixed(2)} s`}
            </dd>
          </div>
          <div>
            <dt style={{ display: "inline", opacity: 0.6 }}>sum mp3: </dt>
            <dd style={{ display: "inline", margin: 0 }}>{formatBytes(totals.bytes)}</dd>
          </div>
          <div>
            <dt style={{ display: "inline", opacity: 0.6 }}>sum decoded PCM (transient peak): </dt>
            <dd style={{ display: "inline", margin: 0 }}>{formatBytes(totals.decodedBytes)}</dd>
          </div>
          <div>
            <dt style={{ display: "inline", opacity: 0.6 }}>sum retained clip buffers: </dt>
            <dd style={{ display: "inline", margin: 0 }}>{formatBytes(totals.clipBytes)}</dd>
          </div>
          <div>
            <dt style={{ display: "inline", opacity: 0.6 }}>longest single Recording: </dt>
            <dd style={{ display: "inline", margin: 0 }}>
              {formatSeconds(totals.longest)}
            </dd>
          </div>
          <div>
            <dt style={{ display: "inline", opacity: 0.6 }}>usedJSHeapSize delta: </dt>
            <dd style={{ display: "inline", margin: 0 }}>
              {heapDeltaBytes === null
                ? "unavailable (Chrome only)"
                : formatBytes(heapDeltaBytes)}
            </dd>
          </div>
          <div>
            <dt style={{ display: "inline", opacity: 0.6 }}>fetch / decode split: </dt>
            <dd style={{ display: "inline", margin: 0 }}>
              {(totals.fetchMs / 1000).toFixed(2)} s /{" "}
              {(totals.decodeMs / 1000).toFixed(2)} s
            </dd>
          </div>
        </dl>

        <p style={{ margin: "12px 0 0", fontSize: 12, opacity: 0.6 }}>
          All items prepared up front = worst-case latency; the spec prefetches
          20 s ahead instead. Decoded PCM (≈384 KB/s stereo at 44.1 kHz) is
          released after each item&apos;s clips are extracted, so the transient
          peak is one Recording above the retained clip total.
        </p>
      </section>
    </main>
  );
}
