# Songset Preview v2: Browser-Only Audition and the Listen Dialog

Supersedes `specs/songset_preview_with_arrangement.md` (v1). v1's server-side Preview Mix is not built.

## 1. Context

Users need to hear a whole Songset as its saved Song Arrangements dictate, without spending a Render. Today the only in-app listening is the Arrangement Editor transport (`delivery/webapp/src/lib/audio/arrangement-preview.ts`): one `<audio>` element per Songset Item, re-seeking `currentTime` at every Step boundary (audible stutter), whose elements capture their `src` at session creation, so an edited time range plays the wrong material until the session is torn down.

A throwaway probe at `/dev/preview-latency/[songsetId]` (deleted by this change) played a real Songset through the browser engine: 2 items, 253.03 s, 10.0 MB of MP3, desktop Chrome. Time to first sample was 2.3–5.0 s; preparing every item took 5–8 s; transient decoded PCM peaked at 147 MB, and the retained clip buffers were 84.7 MB. Gain composition was verified numerically (two series gain nodes per item, envelope product matching the worker's sequential `afade` pair), MP3 decode proved transparent, and the only audible difference against a Render is loudness (no `loudnorm`). It sounded high-quality and responded snappily.

Decision: one browser engine serves both surfaces — the editor's audition and a new whole-Songset **Listen** action — and no server-built mix exists. No worker path, no R2 preview keys, no content hash, no SQS message kind, no prewarm, no polling, no tier switch, no new HTTP routes. A 5-song Songset must not wait for everything to decode, so startup is incremental: only the item being heard is fetched and decoded before the first sample, and the next item is prepared while the current one plays. Time to first sample is therefore independent of song count.

## 2. Pinned decisions

| Decision | Value |
|---|---|
| Playback tier | Browser only. No Preview Mix, no final-mix switch |
| New HTTP routes | None. The dialog reads `GET /api/songsets/{id}` (existing, auth + ownership) |
| Startup | Decode the item at the play position, then play; prepare the next item during playback |
| Prepare-ahead | Exactly 1 item; at most one fetch/decode in flight |
| Retained memory | `retainedBudgetSeconds = 480` of clipped PCM (480 s × 44.1 kHz × 2 ch × 4 B ≈ 169 MB); finished items evicted oldest-first |
| Length guard | None (product decision). Decode failures surface as `playback-failed` with Retry and a Render hint |
| AudioContext | One shared context per document, unlocked synchronously inside the click that opens a listening surface, never closed by a session |
| Loudness | No `loudnorm`; the surface states that preview loudness differs from a Render |
| Labels | Existing **Play** action becomes **Worship** (`敬拜`); new **Listen** action (`試聽`) |
| Editor transport | Same engine; the media-element engine is deleted |
| Out of bounds | No offline copies, no Android change, no downloadable mix, no loudness normalization |

## 3. Approach

### 3.1 Docs to update

The spec instructs these; it does not perform them.

1. `CONTEXT.md` — add two glossary terms after **Song Arrangement** (glossary only, no implementation detail):
   - **Audition**: browser-built playback of a Songset timeline from saved or draft arrangements; used while editing and by the Listen action; loudness differs from a Render because no loudness normalization is applied. _Avoid_: preview (unqualified), preview mix, client render.
   - **Worship Playback**: the two-screen controller experience for a completed Render, started from a Songset row's Worship action. Distinct from the **Worship Page** (`/worship`), which lists Songsets. _Avoid_: play, playback page.

   No **Preview Mix** term is added: v1's term has no implementation.
2. `docs/adr/0012-browser-audio-engine-decodes-clips-not-media-elements.md` (new ADR). Decisions recorded: `decodeAudioData` of one Recording at a time; arranged clip ranges copied into small `AudioBuffer`s; clock-scheduled `AudioBufferSourceNode`s with two series gain nodes per item (the product mirrors the worker's two sequential `afade` filters); no `MediaElementAudioSourceNode`; no analyzer/RMS fallback; memory bounded by a 480 s retained-clip budget with eviction; no server-side cache and no loudness normalization. Cite the probe numbers from §1 (first sample 2.3–5.0 s, 147 MB transient PCM, 84.7 MB retained clip PCM, numerically verified envelope composition).
3. `specs/song_arrangement_editor.md` §5 step 6 (line 117, which today mandates streaming media elements and rejects `decodeAudioData`) — replace that paragraph with: "Audition playback uses the shared browser audio engine (ADR-0012): `decodeAudioData` of the current and adjacent Recordings, arranged clip ranges copied into small buffers, samples scheduled on the `AudioContext` clock. The transport contract in this spec (scopes, windows, boundary behaviour) is unchanged." Step 6 also names `arrangement-preview.ts` / `useArrangementPreview.ts`: change those two names to `songset-audio.ts` / `useSongsetAudio.ts`. Apply the same hook rename to that spec's **Critical files & anchors** entry at line 130, so no deleted path is left named.

### 3.2 `delivery/webapp/src/lib/audio/audio-context.ts` (NEW)

```ts
export function getAudioContext(): AudioContext
export function unlockAudioContext(): void
```

- `getAudioContext()` lazily creates `new AudioContext()` with **no options** (iOS Safari rejects a `sampleRate` option) and returns the singleton. It is never closed.
- `unlockAudioContext()` resumes the context and starts one 1-sample silent `AudioBufferSourceNode`. Its doc comment must state that callers invoke it synchronously inside a click handler (iOS/Chrome gesture unlock); calls made after an `await` do not unlock.
- No `"use client"` directive: the module touches browser globals only inside function bodies.

### 3.3 `delivery/webapp/src/lib/audio/envelopes.ts` (NEW; moved from the probe)

Move `delivery/webapp/src/app/dev/preview-latency/[songsetId]/envelopes.ts` verbatim; keep the exported API: `EnvelopePoint`, `incomingEnvelope`, `outgoingEnvelope`, `envelopeValueAt`, `composedGainAt`, `scheduleEnvelope`, and the two-node-product rationale comment. Replace the file's `// PROTOTYPE (throwaway) … delete before merge` header (and its reference to the v1 spec) with a production header citing ADR-0012: this is the production envelope math, and the two-node product mirrors the render worker's two sequential `afade` filters. No "delete before merge" banner, no prototype wording, no v1 section number survives the move. Production code must never import from `src/app/dev/`.

### 3.4 `delivery/webapp/src/lib/audio/songset-audio.ts` (NEW; replaces `arrangement-preview.ts`)

Public surface, pinned exactly:

```ts
import type { ArrangementTimeline, TimelineItemSource } from "@/lib/arrangements"

export type SongsetAudioErrorKind = "no-recording" | "invalid-arrangement" | "playback-failed"

export interface SongsetAudioError {
  kind: SongsetAudioErrorKind
  itemId: string | null
  message: string
}

export interface SongsetAudioProgress {
  itemId: string
  itemIndex: number        // 0-based
  itemCount: number
  phase: "fetching" | "decoding" | "ready"
  receivedBytes: number
  totalBytes: number | null
}

export interface SongsetAudioSessionOptions {
  audioUrlFor?: (hashPrefix: string) => string   // default `/api/recordings/${hashPrefix}/audio`
  createAudioContext?: () => AudioContext        // default getAudioContext
  fetchArrayBuffer?: (
    url: string,
    onProgress: (receivedBytes: number, totalBytes: number | null) => void,
  ) => Promise<ArrayBuffer>
  tickIntervalMs?: number                        // default 200
  retainedBudgetSeconds?: number                 // default 480
}

export interface SongsetAudioSession {
  readonly timeline: ArrangementTimeline
  readonly totalSeconds: number
  play(): Promise<void>
  pause(): void
  stop(): void
  seek(seconds: number): void
  startAtSeconds(seconds: number): Promise<void>
  getTimelinePositionSeconds(): number
  isPlaying(): boolean
  getProgress(): SongsetAudioProgress | null
  getError(): SongsetAudioError | null
  onStateChange(listener: () => void): () => void
}

export function createSongsetAudioSession(
  items: TimelineItemSource[],
  options?: SongsetAudioSessionOptions,
): SongsetAudioSession

export function validateSongsetAudioSources(items: TimelineItemSource[]): SongsetAudioError | null
```

`validateSongsetAudioSources` is moved verbatim from `validateArrangementPreviewSources` (`arrangement-preview.ts`): same checks, same kinds, same messages — `no-recording` for a missing `recordingHashPrefix` (also for an empty item list), `invalid-arrangement` per item via `parseSongArrangement`/`validateSongArrangement` carrying that item's `itemId`, plus the existing "no playable sections" and "Recording duration unavailable" cases. No React, no Web Audio, no `node:crypto` imports.

Behaviour, in this order:

1. `timeline = buildArrangementTimeline(items)`; `totalSeconds = timeline.totalSeconds`. Never re-derive layout.
2. **Fetch with progress** (default `fetchArrayBuffer`): `fetch(url)`; throw `HTTP {status}` when `!response.ok`; `totalBytes = Number(response.headers.get("content-length")) || null`; read `response.body.getReader()` and report cumulative `receivedBytes` per chunk; concatenate the chunks into one `ArrayBuffer` and return it. Fall back to `response.arrayBuffer()` when `body` is null.
3. **Prepare memo** per item index: `prepare(index)` returns the same in-flight promise for concurrent callers; a rejected prepare deletes its memo entry so a later call retries; a successful prepare keeps the memoized clip buffers. Progress state moves to `phase: "decoding"` once the fetch resolves and to `phase: "ready"` once the clip buffers are stored. `getProgress()` returns non-null **only while a prepare is in flight** (`phase` `"fetching"` or `"decoding"`) and `null` once that prepare settles, on `pause()`, and on `stop()`; a settled prepare's `"ready"` phase is observable only inside the notification that prepare emits, never from a later `getProgress()` call. Progress is in-flight state, not a status field — a playing session with nothing being prepared reports `null`, which is what keeps the dialog's "Preparing the next song…" label from being permanently on.
4. **Clip extraction** — exactly the probe's math. For each `TimelineClip` of the item: `offset = clamp(round(clip.sourceStartSeconds * decoded.sampleRate), 0, decoded.length)`; `length = max(1, min(round((clip.sourceEndSeconds - clip.sourceStartSeconds) * decoded.sampleRate), max(1, decoded.length - offset)))`; `buffer = ctx.createBuffer(decoded.numberOfChannels, length, decoded.sampleRate)`; `buffer.copyToChannel(decoded.getChannelData(ch).subarray(offset, offset + length), ch)`. Store `{ buffer, startSeconds: clip.startSeconds, endSeconds: clip.endSeconds }` per clip and drop the decoded buffer reference.
5. **Eviction**: before preparing item `i`, evict stored items whose timeline end (`startSeconds + durationSeconds`) is `<= currentPosition`, oldest first, until `retainedSeconds + seconds(i) <= retainedBudgetSeconds`. Never evict the item containing the current position, a later item, or the only remaining item. `retainedSeconds` is the sum of the stored clip durations. `stop()` releases every clip buffer. The budget is a **soft target**, never a guard: evict every item that may lawfully be evicted, then prepare the requested item anyway. `prepare` must not loop, must not reject, and must not skip an item because it alone exceeds `retainedBudgetSeconds` (an item longer than 480 s simply keeps `retainedSeconds` over budget while it is retained); a Songset is never refused for length (§2).
6. **Scheduler**: one `GainNode` pair per item in series (`source → gainIn → gainOut → destination`). Incoming envelope `0 → 1` over `[item.startSeconds, item.startSeconds + item.crossfadeSeconds]`; outgoing envelope `1 → 0` over `[itemEnd − successorCrossfade, itemEnd]` where `itemEnd = item.startSeconds + item.durationSeconds` and `successorCrossfade` is the successor's own `crossfadeSeconds`; both are recomputed from the schedule position through `scheduleEnvelope`. For each clip with `clip.endSeconds > position`: `when = ctx.currentTime + max(0, clip.startSeconds − position)`; `offset = clamp(clip.sourceStartSeconds + max(0, position − clip.startSeconds), 0, buffer.duration)`; `duration = clip.endSeconds − max(position, clip.startSeconds)`, skipped when `duration <= 0`; `source.start(when, offset, duration)`.
7. **Two scheduling modes**: a *full rebuild* (stop and disconnect every existing source/gain, then schedule every item whose timeline end is `> position`) on `play` and `seek`; an *additive schedule* of only the newly decoded item when a background decode lands mid-playback. A late decode never rebuilds the graph: the sounding item must not be retriggered.
8. **Clock**: `position = startPosition + (ctx.currentTime − startedAtContextTime)`, clamped to `totalSeconds`; a `tickIntervalMs` ticker; `getTimelinePositionSeconds()` reads that value; wall-clock time is never used.
9. **Playback control**:
   - `play()` — `await ctx.resume()`; clear any recorded error; `await prepare(itemIndexAt(position))`; full rebuild; start the clock; start the ticker; notify. It rejects only after recording the failure in `getError()`.
   - `startAtSeconds(seconds)` — clamp `seconds`, set the position, then behave exactly like `play()`.
   - `pause()` — capture the position from the clock, stop the sources, stop the ticker, keep the context running (never call `suspend()`).
   - `seek(seconds)` — clamp; set the position. While playing: if the target item is decoded, full rebuild from the position; otherwise set an internal `awaitingDecode` flag that freezes the clock (the ticker must not advance the position), `prepare(itemIndexAt(position))`, then full rebuild and restart the clock from the requested position. While paused: only set the position and start `prepare(itemIndexAt(position))` in the background.
   - `stop()` — stop sources, clear the ticker, release all clip buffers, reset position and error, mark the session inert so every later call is a no-op. Never close the context.
10. **Ticker duties**, each tick: recompute the position (skipped while `awaitingDecode`); when `position >= totalSeconds`, stop sources and report paused-at-end; when the item at the position has no buffers and nothing is in flight for it, `prepare` it (additive schedule on completion); when playing and the next unprepared item exists, `prepare` it (prepare-ahead 1, only after the current item's prepare settled); notify listeners.
11. **Errors**: fetch non-2xx or decode rejection → `playback-failed` carrying that item's `itemId` and the error text as `message`; a missing `recordingHashPrefix` → `no-recording`; either way stop playback, clear progress, notify. No fallback mode, no analyzer, no media element.
12. Notify through `onStateChange`; progress notifications are throttled to at most one per 100 ms.

### 3.5 `delivery/webapp/src/hooks/useSongsetAudio.ts` (NEW; replaces `useArrangementPreview.ts`)

- Keep today's lifecycle verbatim: the session is created on the first `play`; teardown on `itemsKey` change (`JSON.stringify(items)`), on `active=false`, and on unmount; `player.stop()` runs before the session starts; the error callback goes through a latest-ref.
- Options: `{ items, active, onPreviewError }` — `onFallbackNotice` is dropped.
- Result: `{ status, errorMessage, timelinePositionSeconds, totalSeconds, progress, play, pause, stop, seek, startAtSeconds, retry }`. `startAt(itemId)` and `isFallbackMode` are deleted.
- `retry()` recreates the session at the current position and calls `play()`; it is the only exit from `playback-failed`.
- `status` keeps the existing union `"idle" | "loading" | "playing" | "paused" | "error"`; `progress` is the session's `getProgress()` snapshotted on every state change.
- Re-export `SongsetAudioError` / `SongsetAudioErrorKind` so the sheet keeps one import surface.

### 3.6 `delivery/webapp/src/components/songset/ListenMixDialog.tsx` (NEW)

Props `{ songsetId: string; songsetName: string; isOpen: boolean; onOpenChange: (open: boolean) => void }`. Every mount site passes `key={songsetId}`, so switching rows remounts the dialog and tears the previous session down.

Test ids: `listen-dialog`, `listen-play`, `listen-progress`, `listen-status`, `listen-seek`, `listen-time`, `listen-retry`, `listen-close`.

Open sequence, in this order:

1. Call `useAudioPlayerContext().stop()` once, so two transports never sound together.
2. `GET /api/songsets/{id}`. 401 or 404 → `songsets.listen.error.notFound`; any other non-2xx → `songsets.listen.error.build`.
3. Map the payload with a pure mapper in `delivery/webapp/src/lib/arrangements-listen.ts` (NEW), which stays client-safe by declaring its own structural input type (no `@/lib/db/...` import):

   ```ts
   import type { RecordingSection, TimelineItemSource } from "@/lib/arrangements"

   export interface ListenSongsetItemPayload {
     id: string
     recordingHashPrefix: string | null
     arrangementSteps: string | null
     gapBeats: number | null
     crossfadeEnabled: number | null
     crossfadeDurationSeconds: number | null
     song: { title: string | null } | null
     recording: {
       tempoBpm: number | null
       durationSeconds: number | null
       sections: RecordingSection[] | null
       beats: number[] | null
     } | null
   }

   export function toListenTimelineSources(
     items: ListenSongsetItemPayload[],
   ): TimelineItemSource[]
   ```

   Per item: `{ itemId: item.id, recordingHashPrefix, arrangementSteps, gapBeats, crossfadeEnabled, crossfadeDurationSeconds, tempoBpm: item.recording?.tempoBpm ?? null, recordingDurationSeconds: item.recording?.durationSeconds ?? null, recordingSections: item.recording?.sections ?? null, recordingBeats: item.recording?.beats ?? null }`.
4. When the payload's `deletedItemCount > 0` → `songsets.listen.error.deletedRecording` and create no session (the API drops items whose Recording is soft-deleted, so playing would silently shorten the Songset).
5. `validateSongsetAudioSources(items)` → map `no-recording` / `invalid-arrangement` to their strings.
6. `useSongsetAudio({ items, active: isOpen, onPreviewError })`; attempt `play()` once after the timeline loads; on rejection show the Play button (`songsets.listen.action.play`).

Rendering states:

| Session state | UI |
|---|---|
| `idle \| loading` | `listen-progress`: with a known `totalBytes`, `songsets.listen.loading` = `"Loading {title}… {percent}%"`; otherwise `songsets.listen.loadingIndeterminate` = `"Loading {title}…"`. `{title}` is that item's `song.title`, falling back to `songsets.listen.untitled`. A determinate `<progress>` element carries the percentage for assistive tech |
| `playing \| paused` | Play/Pause; range seek over `[0, totalSeconds]` driven by `timelinePositionSeconds`; `m:ss / m:ss`; the current item title; a proportional seek bar whose segment widths come from `buildArrangementTimeline` and tile the timeline total, with the transition zone drawn once at the incoming Item's start, one active segment, click/drag/keys seeking with an 8 px boundary snap, and no seeking when unseekable; the permanent `songsets.listen.localNotice` |
| playing while `progress` is non-null **and** `progress.phase !== "ready"` | `songsets.listen.preparingNext` in `listen-status` — this is what tells the user the mid-set work is happening. The label must disappear the moment `getProgress()` returns `null` (§3.4 item 3); it is never a permanent banner |
| `error` | `errorMessage` + Retry (`retry()`) + `songsets.listen.error.renderHint` |

Close (`listen-close` or `onOpenChange(false)`) sets `active=false`, which tears the session down.

Seek semantics: seeking into an evicted or not-yet-fetched item shows the loading state and holds the timeline clock until that item is ready. The position the user asked for is honoured — the engine never plays a different offset and never leaves a silent gap.

### 3.7 Row and editor actions, i18n, deletions

Actions:

1. `SongsetMenuAction`: `"render" | "worship" | "listen" | "downloadOffline" | "removeOffline"` (`SongsetRow.tsx:43`).
2. `SongsetRow.tsx`: rename the `onPlay` prop to `onWorshipPlayback`; add `onListen?: () => void`. The button row keeps the `canPlayFreshRender` Worship button (`Play` icon, label `songsets.action.worship`) and adds a Listen button when `onListen` is set:

   ```tsx
   <Button variant="outline" size="sm" className="shrink-0 gap-1.5" onClick={onListen}>
     <Headphones className="size-4" />
     {t("songsets.action.listen")}
   </Button>
   ```

   `Headphones` comes from `lucide-react`. In the kebab menu the Worship item (`songsets.action.worship`) replaces the old `"play"` item and the Listen item (`songsets.action.listen`) sits directly below it.
3. `SongsetList.tsx`: rename `onPlay` → `onWorshipPlayback`; add `onListen?: (id: string) => void`.
4. `src/app/songsets/SongsetsClient.tsx`: rename `handlePlay` → `handleWorshipPlayback`; add `listeningSongset: { id: string; name: string } | null`; `handleListen = (id) => { unlockAudioContext(); setListeningSongset(...) }`; render `<ListenMixDialog key={listeningSongset?.id} … />`.
5. `src/app/worship/WorshipClient.tsx`: same rename; `menuActions={["worship", "listen", "downloadOffline", "removeOffline"]}`; mount the dialog for the selected row; same `unlockAudioContext()` inside its Listen handler.
6. `src/app/songsets/[id]/SongsetEditorClient.tsx`: rename `handlePlay` → `handleWorshipPlayback`; add `isListenOpen`; `handleListen = () => { unlockAudioContext(); setIsListenOpen(true) }`; render the dialog; pass `onListen={handleListen}`.
7. `SongsetEditor.tsx`: add `onListen: () => void` and a `Headphones` menu item (`songsets.action.listen`) directly after the existing Play item; leave the existing `onPlay` prop and item unchanged.
8. The dialog plays **saved** arrangements only; unsaved sheet edits are not audible until Apply.

Strings in `delivery/webapp/src/lib/i18n/messages/songsets.ts`, both locales (Traditional Chinese values pinned):

| Key | en | zh-Hant |
|---|---|---|
| `songsets.action.worship` (renamed from `songsets.action.play`) | Worship | 敬拜 |
| `songsets.action.listen` | Listen | 試聽 |
| `songsets.listen.title` | Listen to Songset | 試聽歌集 |
| `songsets.listen.loading` | Loading {title}… {percent}% | 正在載入 {title}…{percent}% |
| `songsets.listen.loadingIndeterminate` | Loading {title}… | 正在載入 {title}… |
| `songsets.listen.preparingNext` | Preparing the next song… | 正在準備下一首… |
| `songsets.listen.localNotice` | Browser preview — loudness differs from the final render | 瀏覽器試聽，音量可能與完成渲染不同 |
| `songsets.listen.untitled` | Untitled | 未命名 |
| `songsets.listen.action.play` | Play | 播放 |
| `songsets.listen.action.pause` | Pause | 暫停 |
| `songsets.listen.action.retry` | Retry | 重試 |
| `songsets.listen.action.close` | Close | 關閉 |
| `songsets.listen.seekAria` | Seek preview | 調整試聽位置 |
| `songsets.listen.currentItem` | Now playing | 現正播放 |
| `songsets.listen.error.noRecording` | A song in this Songset has no Recording | 此歌集有歌曲沒有錄音 |
| `songsets.listen.error.invalidArrangement` | A saved arrangement no longer matches its Recording | 已儲存的編排與錄音不符 |
| `songsets.listen.error.deletedRecording` | A song in this Songset no longer has a Recording | 此歌集有歌曲的錄音已刪除 |
| `songsets.listen.error.playbackFailed` | Could not prepare the audio for this Songset | 無法準備此歌集的音訊 |
| `songsets.listen.error.notFound` | Songset not found | 找不到歌集 |
| `songsets.listen.error.build` | Could not load this Songset | 無法載入此歌集 |
| `songsets.listen.error.renderHint` | Render this Songset to listen to it in full | 渲染此歌集以完整試聽 |

Update every `t("songsets.action.play")` call site: `SongsetRow.tsx:190`, `SongsetRow.tsx:228`, `DashboardSongsetCard.tsx:98`, `SongsetEditor.tsx:358`. Leave `songsets.action.playAnyway` alone (its text is a stale-render warning, not an action name).

Deletions (clean cutover; no aliases, no shims):

- `delivery/webapp/src/lib/audio/arrangement-preview.ts`, `delivery/webapp/src/hooks/useArrangementPreview.ts`
- `delivery/webapp/src/test/lib/audio/arrangement-preview.test.ts`, `delivery/webapp/src/test/hooks/useArrangementPreview.test.tsx`
- `delivery/webapp/src/app/dev/preview-latency/` (the probe; move `envelopes.ts` to `src/lib/audio/` first)
- the `arrangement.preview.fallbackNotice` key in both locales of `delivery/webapp/src/lib/i18n/messages/arrangement.ts`, plus the sheet's `fallbackNotice` state (`ArrangementEditorSheet.tsx:937`), `handleFallbackNotice`, `showFallbackNotice` (`:1899`) and the notice JSX (`:2550`)

### 3.8 Data change: `deletedItemCount`

In `delivery/webapp/src/lib/db/songsets.ts`, `getSongsetEditorData`:

- add `deletedItemCount: number` to the returned object, computed as `itemRows.length - visibleItemRows.length` (items whose Recording is soft-deleted are filtered out of `items`);
- extend the `SongsetDetail` interface (and the returned type) with that field.

No other query, route, or component changes. This is how the Listen dialog refuses to play a silently shortened Songset.

### 3.9 Tests

New:

- `delivery/webapp/src/test/lib/audio/songset-audio.test.ts` — fake `AudioContext`/`AudioBufferSourceNode` asserting: (a) the first clip starts at `offset = sourceStart + (position − clip.startSeconds)`; (b) a later clip is scheduled at `ctx.currentTime + (clip.startSeconds − position)`; (c) crossfade ramps land on the incoming item window and on the successor-owned outgoing window; (d) `seek` while playing rebuilds from the new position; (e) `seek` into an unfetched item freezes the clock until the decode resolves and then plays from the requested position; (f) an item is fetched and decoded once per session and its decoded buffer is not retained (the clip buffers are distinct objects); (g) a late decode schedules only that item (the sounding item is not retriggered); (h) eviction drops finished items once the retained budget is exceeded and `stop()` releases everything; (i) a rejected decode is retried on the next `prepare` call; (j) `stop()` clears the ticker and makes the session inert.
- `delivery/webapp/src/test/hooks/useSongsetAudio.test.tsx` — the session is created on the first play only; teardown on `items` change and on `active=false`; error forwarding; the `progress` snapshot is forwarded; `retry()` recreates and replays.
- `delivery/webapp/src/test/components/songset/ListenMixDialog.test.tsx` — loading progress renders `{title}` and a percentage; only `GET /api/songsets/{id}` is called (no POST); autoplay rejection shows Play; a failed session shows Retry plus the Render hint and Retry re-creates the session; `deletedItemCount > 0` blocks playback; close stops the session; a `key` change remounts.

Updated: `src/test/components/songset/ArrangementEditorSheet.test.tsx` (mock the new hook, drop `isFallbackMode`, assert the progress label), `SongsetRow.test.tsx`, `SongsetList.test.tsx`, `SongsetEditor.test.tsx`, `SongsetEditorClient.test.tsx`, `src/test/app/worship-client.test.tsx`, and `src/test/lib/i18n/messages.test.ts` (locale parity for the renamed and the new keys).

## 4. Critical files & anchors

- `delivery/webapp/src/lib/arrangements.ts` — `ArrangementTimelineItem`, `TimelineClip` (`sourceStartSeconds`/`sourceEndSeconds` are Recording offsets, `startSeconds`/`endSeconds` are timeline positions), `buildArrangementTimeline`. The engine and the markers must reuse these and never re-derive layout.
- `delivery/webapp/src/lib/audio/arrangement-preview.ts:1-45` — the constraint comment that mandates media elements and the iOS RMS fallback; both are deleted. `:300-350` (`wireGraph`) is the two-series-gain parity to preserve in the new engine.
- `delivery/webapp/src/app/dev/preview-latency/[songsetId]/PreviewLatencyClient.tsx:210-340` — the clip-extraction and scheduling code the engine reproduces, plus `envelopes.ts` (moved).
- `delivery/webapp/src/lib/db/songsets.ts:730-905` — `getSongsetEditorData`, the `visibleItemRows` filter that drops soft-deleted Recordings (the source of `deletedItemCount`), and the `SongsetItemDetail` shape the mapper consumes.
- `delivery/webapp/src/app/api/songsets/[id]/route.ts` — the existing `GET` the dialog reuses (401/404 behaviour already correct).

File map:

| Path | State |
|---|---|
| `specs/songset_preview_with_arrangement_v2.md` | NEW (this document) |
| `docs/adr/0012-browser-audio-engine-decodes-clips-not-media-elements.md` | NEW |
| `delivery/webapp/src/lib/audio/audio-context.ts` | NEW |
| `delivery/webapp/src/lib/audio/envelopes.ts` | NEW (moved from the probe) |
| `delivery/webapp/src/lib/audio/songset-audio.ts` | NEW |
| `delivery/webapp/src/lib/arrangements-listen.ts` | NEW |
| `delivery/webapp/src/hooks/useSongsetAudio.ts` | NEW |
| `delivery/webapp/src/components/songset/ListenMixDialog.tsx` | NEW |
| `delivery/webapp/src/test/lib/audio/songset-audio.test.ts` | NEW |
| `delivery/webapp/src/test/hooks/useSongsetAudio.test.tsx` | NEW |
| `delivery/webapp/src/test/components/songset/ListenMixDialog.test.tsx` | NEW |
| `CONTEXT.md`, `specs/song_arrangement_editor.md` | MODIFIED (§3.1) |
| `delivery/webapp/src/lib/db/songsets.ts` | MODIFIED (§3.8) |
| `delivery/webapp/src/lib/i18n/messages/songsets.ts`, `.../arrangement.ts` | MODIFIED (§3.7) |
| `delivery/webapp/src/components/songset/{SongsetRow,SongsetList,SongsetEditor,ArrangementEditorSheet}.tsx` | MODIFIED (§3.5, §3.7) |
| `delivery/webapp/src/components/dashboard/DashboardSongsetCard.tsx` | MODIFIED (label only, §3.7) |
| `delivery/webapp/src/app/songsets/SongsetsClient.tsx`, `delivery/webapp/src/app/songsets/[id]/SongsetEditorClient.tsx`, `delivery/webapp/src/app/worship/WorshipClient.tsx` | MODIFIED (§3.7) |
| `delivery/webapp/src/lib/audio/arrangement-preview.ts`, `delivery/webapp/src/hooks/useArrangementPreview.ts`, `delivery/webapp/src/test/lib/audio/arrangement-preview.test.ts`, `delivery/webapp/src/test/hooks/useArrangementPreview.test.tsx`, `delivery/webapp/src/app/dev/preview-latency/` | DELETED (§3.7) |

## 5. Verification

1. `cd delivery/webapp && pnpm exec vitest run src/test/lib/audio/songset-audio.test.ts src/test/hooks/useSongsetAudio.test.tsx src/test/components/songset/ListenMixDialog.test.tsx src/test/components/songset/SongsetRow.test.tsx src/test/components/songset/SongsetList.test.tsx src/test/components/songset/SongsetEditor.test.tsx src/test/components/songset/SongsetEditorClient.test.tsx src/test/components/songset/ArrangementEditorSheet.test.tsx src/test/lib/i18n/messages.test.ts src/test/app/worship-client.test.tsx` — all pass. (`pnpm test` is `vitest run`; `pnpm exec vitest run` avoids the extra `run` positional argument.)
2. `cd delivery/webapp && pnpm lint && pnpm build` — clean. The build needs the env file: `pnpm exec env-cmd -f /opt/sow/.env next build` (pre-existing requirement).
3. Real browser, signed in, a Songset with saved arrangements: press **Listen**; first sound arrives within ~5 s while the loading label shows a percentage; the loading label disappears; `listen-status` shows "Preparing the next song…" during the next song's download; playback crosses the boundary with no silence and no wrong-range audio.
4. Repeat step 3 on a two-song Songset twice: once after a hard reload (cold) and once with a warm cache. The warm run's first sound is measurably faster.
5. Seek: forward into a later song (the loading state appears, then playback starts from the requested second); backward into a finished song (same, never a silent gap and never a wrong offset).
6. Editor regression: open the arrangement sheet and press preview — the loading label appears with progress, then playback starts; edit a bound, Apply, audition again — the new range plays.
7. Close the dialog mid-playback: audio stops, no console errors, and the memory figure stops growing (Chrome `performance.memory` heap delta after close returns near the pre-open value).
8. `git grep -nE "arrangement-preview|useArrangementPreview|preview-latency" -- delivery/webapp/src` returns nothing. (Scope the grep to `delivery/webapp/src`: `specs/` legitimately names the removed paths.)

## 6. Assumptions & contingencies

- **No length guard (product decision, 2026-11-11).** A Songset with a very long Recording may exhaust memory or be rejected by `decodeAudioData`. The `playback-failed` path (message + Retry + Render hint) is the only mitigation. The retained budget is a soft target, not an admission rule (§3.4 item 5): a Songset whose items exceed 480 s of clipped PCM still prepares and plays, and memory pressure surfaces as a decode/tab failure, never as a refusal. If iOS tab crashes become common in practice, add v1's 420 s recording-duration guard as a preflight in the dialog; nothing else changes.
- **`GET /api/songsets/{id}` payload cost.** It carries analysis (`sections`, `beats`) the engine needs for arrangement validation. If p95 load time exceeds ~1 s, add a `?include=listen` server-side projection; do not fork the payload now.
- **Repeat listens re-download.** The recordings proxy sends `Cache-Control: private, max-age=3600`, so a warm browser cache serves repeat listens within the hour. Do not add an app-level cache.
- **Worship rename acceptance.** If the label proves confusing in review, revert `songsets.action.worship` to "Play" text only; the Listen action and all code stay unchanged.
- **One shared AudioContext per document.** Sessions never close it: a closed context cannot be re-unlocked without a gesture, and the app keeps one context anyway.
