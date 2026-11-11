# Preview-Latency & Sound-Quality Test Page — Implementation Summary

**Date:** 2026-11-11
**Spec:** `specs/songset_preview_with_arrangement.md` (§6 browser "local tier")
**Branch:** `sow_arrangement_editor_fixes_1111` (commits `b680a79e`, `79898475`)
**Songset under test:** `i3-Ps8YpofpBmJ8_rNLzN` ("Test Arrangement")

---

## Overview

Two deliverables:

1. **A written review** of the spec's core assumption — that the browser-built local tier (decode → clip extraction → `AudioBufferSourceNode` scheduling) delivers acceptable UX. Findings are summarised below; the assumption holds on desktop broadband, with measurable risk concentrated in first-sample latency and mid-set gaps on slow links.
2. **A working dev probe page** at `/dev/preview-latency/[songsetId]` that plays a real songset through the spec's local-tier engine while measuring fetch/decode latency and memory, and lets a reviewer audition boundary behaviour.

The page lives inside the webapp because the data and audio APIs (`GET /api/songsets/[id]`, `/api/recordings/{hashPrefix}/audio`) are auth-gated and same-origin only, so it runs on the existing dev server (`https://localhost:8080`) and reuses the canonical TS timeline math directly — no timing duplication, no proxy.

**Throwaway by design:** dev tooling, no i18n keys, no unit tests, and it must be deleted before any production merge.

---

## Files Created

| File | Description |
|------|-------------|
| `delivery/webapp/src/app/dev/preview-latency/[songsetId]/page.tsx` | Server component: session gate → `redirect("/login")`, `getSongsetEditorData` → `notFound()`, maps `songset.items` to `TimelineItemSource` |
| `delivery/webapp/src/app/dev/preview-latency/[songsetId]/PreviewLatencyClient.tsx` | Client probe: sequential fetch → decode → clip extraction, metric table, transport, per-item jump buttons |
| `delivery/webapp/src/app/dev/preview-latency/[songsetId]/envelopes.ts` | Gain-envelope math (`incomingEnvelope`, `outgoingEnvelope`, `envelopeValueAt`, `composedGainAt`, `scheduleEnvelope`) — extracted so it is numerically checkable |

No existing files were modified; `delivery/webapp/src/lib/build-info.ts` churn from `prebuild` was reverted before committing.

---

## Review Findings

| # | Finding | Evidence |
|---|---------|----------|
| 1 | "Plays immediately" is overstated: `play()` awaits `ensureDecoded(current)`, i.e. the whole first Recording downloads and decodes before one sample sounds | Measured 5.0 s cold / 2.3 s warm for item 1 |
| 2 | Late-decode silence gap is a real spec bug: the ticker's `ensureDecoded` is fire-and-forget, nothing re-runs `scheduleFrom` when a slow decode lands, so that item plays silence | Code reading of §6; the probe sidesteps it by preparing everything up front |
| 3 | `prefetchLeadSeconds = 20` is fixed and network-blind; on a slow link a 12 MB fetch+decode exceeds the lead and compounds #2 | Item 2 measured 1.18 s fetch + 1.42 s decode warm |
| 4 | Memory math confirms the 420 s guard, and above it iOS silently degrades to "wait for the final mix" | Transient decoded PCM measured 147.0 MB for two Recordings |
| 5 | Sound quality is the strong part: clock-scheduled `AudioBufferSourceNode`s with linear ramps mirroring the worker's `afade` pair are sample-accurate; MP3 decode is transparent; only loudness differs (no `loudnorm`), which `localNotice` discloses | Envelope composition verified numerically (below) |
| 6 | Verdict: assumption holds on desktop broadband; first-sample latency and slow-link gaps are the quantifiable risks | Measurements below |

---

## Probe Design

### Prepare (the measurement)

"Prepare all" runs sequentially over every timeline item — item 0's fetch+decode total *is* the time-to-first-sample number:

1. `fetch("/api/recordings/{hashPrefix}/audio")` → `arrayBuffer()`; compressed size is read **before** decode, because `decodeAudioData` detaches the `ArrayBuffer` (bug caught by running the page: it reported `0 B`).
2. `decodeAudioData(buffer)` on a single shared `AudioContext({ sampleRate: 44100 })`.
3. Spec §6 `ensureDecoded` clip extraction, verbatim: one `AudioBuffer` per clip (`createBuffer(channels, round(clipLength × sampleRate), decoded.sampleRate)`, `copyToChannel(decoded.getChannelData(ch).subarray(offset, offset + length), ch)`), with `offset` clamped to `decoded.length` and the copied length clamped to what remains. The decoded buffer is released when the loop iteration ends.
4. Clip buffers are retained in a ref keyed by item index; the mp3/PCM/clip byte totals and a Chrome `usedJSHeapSize` delta are shown per item and in summary.

Preparing everything up front measures **worst-case** latency deliberately and sidesteps finding #2; the UI says so.

### Playback

- `scheduleFrom(position)` rebuilds the graph from scratch: every clip with `endSeconds > position` becomes an `AudioBufferSourceNode` started at `ctx.currentTime + max(0, clip.startSeconds − position)` with `offset = max(0, position − clip.startSeconds)` and `duration = clipLength − offset`, clamped to `buffer.duration`.
- **Two gain nodes per item in series** (`source → gainIn → gainOut → destination`), mirroring `lib/audio/arrangement-preview.ts:326-338` / `scheduleGains`: `gainIn` carries the item's own fade-in `0 → 1` over `[start, start + crossfadeSeconds]`, `gainOut` carries the successor-owned fade-out `1 → 0` over `[itemEnd − successorCrossfade, itemEnd]`. The envelopes multiply like the worker's two sequential `afade` filters.
- Clock: `position = startPosition + (ctx.currentTime − startedContextTime)`, 250 ms ticker, auto-stop at `totalSeconds`.
- Controls: Play/Pause (reschedule from the current position), Stop, range slider (seek reschedules), and per-item jump buttons to audition boundary seams.

### Envelope correctness

The first implementation used a **single** piecewise gain node. That cannot express the product: sorting breakpoints put a `1.0` point inside a running ramp whenever an item was shorter than its fade-in plus the successor's fade-out, so the item stepped to full volume mid-overlap (for a 5 s item with two 3 s fades: a `1.0` plateau across `t = 2…3`). Replaced with the two-node series chain, and the math extracted to `envelopes.ts` so it is verifiable.

---

## Verification

### Static

| Check | Result |
|-------|--------|
| `pnpm typecheck` | clean |
| `pnpm lint` | 0 errors, 8 pre-existing warnings in unrelated files |
| `pnpm build` | clean; route registered as dynamic `/dev/preview-latency/[songsetId]` |

Note: `pnpm build` requires the env file — `pnpm exec env-cmd -f /opt/sow/.env next build`. A bare `pnpm build` fails with `SOW_DATABASE_URL environment variable is required` (pre-existing, environmental).

### Browser (real songset, logged in)

| Check | Result |
|-------|--------|
| Auth gate | HTTP 307 → `/login?callbackUrl=%2Fdev%2Fpreview-latency%2F…` when logged out |
| Duration parity | Page total 4:13.03 = 253.03 s; `GET /api/songsets/{id}` returns `durationSeconds: 253.0305415860735` |
| Prepare | Metrics table fills per item; no console errors |
| Transport | play 0:02.51 @ 2.5 s; pause holds (2:04.89 → 2:04.89); resume continues (2:06.41); stop → 0:00.00; jump 2 → 1:59.70, HUD "item 2"; slider → 1:00.00 |
| Audio graph | Instrumented `createGain`/`connect`: 4 gains = 2 per item, edges `gain1→gain2→AudioDestinationNode`, `gain3→gain4→AudioDestinationNode`; jump re-schedules only the active item (6 gains total) |
| Envelope math | 18 assertions on composition and `AudioParam` automation sequences — all pass |

### Measurements (songset `i3-Ps8YpofpBmJ8_rNLzN`)

Timeline: 2 items, 253.03 s total. Crossfade is **disabled** on both items, so the boundary is a 1.161 s beat gap, not a seam.

| item | hash | start | duration | gap | crossfade | clips |
|---|---|---|---|---|---|---|
| 1 | `2889fbb03e37` | 0.000 | 116.620 | 0.000 | 0.000 | 5 |
| 2 | `196669aa37bd` | 117.781 | 135.250 | 1.161 | 0.000 | 3 |

| measurement | cold | warm |
|---|---|---|
| time to first sample (item 1 fetch + decode) | 5015 ms (3971 + 1044) | 2312–3116 ms |
| total prepare wall time | 8.02 s | 4.97 s |
| fetch / decode split | – | 2.37 s / 2.55 s |
| sum mp3 | 10.0 MB (4.3 + 5.7) | same |
| sum decoded PCM, transient peak | 147.0 MB | same |
| sum retained clip buffers | 84.7 MB | same |
| longest single Recording | 4:09.02 | same |
| `usedJSHeapSize` delta | 64.0–143.2 MB | – |

---

## Known Limits

- **No audible crossfade check.** This account has no crossfade-enabled songset (9 songsets, none with `crossfadeEnabled`), so the overlap path is verified numerically and by graph wiring, not by ear. To hear a real seam, enable crossfade on an item and open `/dev/preview-latency/<id>`.
- **Desktop Chrome only.** iOS Safari needs a real device; the 420 s memory guard still applies conceptually (the probe decodes everything anyway, to expose the true peak).
- **Sequential prepare** is intentional worst case; the spec prefetches 20 s ahead instead.
- `performance.memory` is Chrome-only; the heap delta reads "unavailable" elsewhere.

---

## Cleanup

Delete `delivery/webapp/src/app/dev/preview-latency/` before any production merge. No i18n keys, no tests, and no production code paths depend on it.
