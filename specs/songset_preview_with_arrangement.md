# Songset Preview Mix and Browser Audio Engine

## Context

Users need to hear a whole Songset as it will sound with the saved Song Arrangements, without spending a video Render. The only in-app listening today is the Arrangement Editor transport (`delivery/webapp/src/lib/audio/arrangement-preview.ts`): one `<audio>` per Songset Item, re-seeking `currentTime` at every Step boundary (the stutter) and capturing its inputs at session creation while the timeline is recomputed per render (edited time ranges play the wrong material).

Two deliverables:

1. **Preview Mix** — an audio-only, render-identical MP3 of the whole Songset, built by the existing render worker from persisted rows and cached in R2 under a content hash. Never enters render state, render history, or the one-active-job rule.
2. **Browser audio engine** — decodes each Recording once, keeps only the arranged clip samples, and schedules `AudioBufferSourceNode`s on the `AudioContext` clock. Serves as the instant fallback on a cache miss and replaces the editor's media-element transport.

Listening is cache-first: a cached Preview Mix plays; otherwise the browser mix starts immediately and a Preview Mix is dispatched for next time; playback never switches source mid-stream.

Songset rows and the Songset edit screen gain a **Listen** action, and the existing **Play** action is renamed **Worship** (button, kebab menu, dashboard card).

## Pinned decisions

| Decision | Value |
|---|---|
| Mix settings | `generate_songset_audio` defaults unchanged: libmp3lame 320 kbps, `loudnorm I=-14:TP=-1.5:LRA=11`, 44100 Hz, 2 channels |
| Cache key | `sha256` hex of the canonical payload below (`PREVIEW_MIX_SCHEMA_VERSION = 1`) |
| R2 keys | `previews/{songsetId}/{hash}/output.mp3`, `.../chapters.json`, `.../error.json` |
| Retention | 3 newest hashes per Songset, pruned by the worker after each successful upload |
| Persistence | R2 only. No DB rows, no `render_jobs` row, no `songsets.latestRenderJobId` / `last_completed_render_job_id` writes |
| Prewarm | Arrangement PUT (save `{arrangement}` or reset `{arrangement:null}`) dispatches a build after commit; failure is logged, never fails the save |
| Dispatch | Shared SQS queue in production (`SOW_RENDER_WORKER_MODE=sqs`), REST RIE in dev. Message carries a `kind` discriminator |
| Local tier | `decodeAudioData` per Recording → copy arranged clip ranges into small `AudioBuffer`s → `AudioBufferSourceNode` scheduling; no `MediaElementAudioSourceNode`; no RMS silent-output fallback |
| Tier policy | Cache hit → Preview Mix; miss → local tier plays immediately, Preview Mix builds in the background; a ready mix offers an explicit "use final mix" restart, never an automatic switch |
| Autoplay | Attempt `play()` once on open; on rejection show the Play button |
| Scope | Whole Songset, item 1 → N. The sheet keeps its song-scoped window (item + 8-beat bookends) |
| Out of bounds | No offline support for Preview Mixes; no Android app change; no downloadable MP3 from the dialog |

## Approach

### 1. Pin vocabulary and decisions in docs

1. `CONTEXT.md` — add three terms (glossary only, no implementation detail), inserted after **Song Arrangement**:
   - **Preview Mix**: the audio-only mix of a whole Songset built from its saved Song Arrangements with a Render's exact mixing settings, cached under a content hash of its inputs. Not a Render: no video, no chapters deliverable, absent from render history and render state, not shareable, not available offline. _Avoid_: preview render, quick render, render preview.
   - **Audition**: browser-built playback of the Songset timeline from saved or draft arrangements used while editing; loudness differs from a Render because no loudness normalization is applied. Covers the Arrangement Editor's bounds audition, transition review, and arrangement preview. _Avoid_: preview (unqualified), client preview.
   - **Worship Playback**: the two-screen controller experience for a completed Render, started from a Songset row's Worship action. Distinct from the **Worship Page** (`/worship`), which lists Songsets. _Avoid_: play, playback page.
2. `docs/adr/0012-preview-mix-is-hash-keyed-r2-not-a-render.md` — decisions: R2-only hash-keyed storage, exclusion from render state/history/one-active-job, identical mix settings, prewarm on arrangement save, keep-3 retention, shared queue accepted.
3. `docs/adr/0013-browser-audio-engine-decodes-clips-not-media-elements.md` — decisions: decode-whole-Recording + clip extraction + buffer scheduling; deleted MediaElementSource path and RMS fallback; bounded memory (one decoded Recording at a time); the long-Recording contingency in **Assumptions**.
4. `specs/song_arrangement_editor.md` §5 step 6 (currently mandates streaming media elements and rejects `decodeAudioData`) — replace that paragraph with: "Audition playback uses the shared browser audio engine (ADR-0013): `decodeAudioData` of the current and adjacent Recordings, arranged clip ranges copied into small buffers, samples scheduled on the `AudioContext` clock. The transport contract in this spec (scopes, windows, boundary behaviour) is unchanged."
5. New `specs/songset_preview_mix.md` — wire contracts for steps 4-7: routes and response JSON below, the hash payload, R2 keys and retention, the worker message shapes, the tier policy and dialog states, and acceptance checks from **Verification**.

### 2. Preview-mix library (webapp, server-only)

1. `delivery/webapp/src/lib/preview-mix/hash.ts` (new):

```ts
import { createHash } from "node:crypto"

export const PREVIEW_MIX_SCHEMA_VERSION = 1
export const PREVIEW_MIX_BITRATE_KBPS = 320
export const PREVIEW_MIX_LOUDNORM = "I=-14:TP=-1.5:LRA=11"
export const PREVIEW_MIX_SAMPLE_RATE = 44100
export const PREVIEW_MIX_CHANNELS = 2

export interface PreviewMixItemInput {
  itemId: string
  position: number
  recordingHashPrefix: string
  recordingContentHash: string | null
  arrangementSteps: string | null   // raw persisted JSON, verbatim
  gapBeats: number | null
  crossfadeEnabled: boolean
  crossfadeDurationSeconds: number | null
}

export function previewMixCanonicalJson(items: PreviewMixItemInput[]): string
export function computePreviewMixHash(items: PreviewMixItemInput[]): string
```

   `previewMixCanonicalJson` sorts by `(position, itemId)`, then emits one object literal in the field order above (plus `schemaVersion`, `bitrateKbps`, `loudnorm`, `sampleRate`, `channels`), with `crossfadeEnabled: Boolean(...)`. `computePreviewMixHash` = `sha256(canonical)` lowercase hex. No DB, no clock, no Math.random in the payload.

2. `delivery/webapp/src/lib/preview-mix/keys.ts` (new):

```ts
export const PREVIEW_MIX_HASH_PATTERN = /^[a-f0-9]{64}$/
export function isValidSongsetIdForKey(id: string): boolean  // /^[A-Za-z0-9_-]{1,64}$/
export function previewMixKeyPrefix(songsetId: string, hash: string): string  // `previews/{songsetId}/{hash}`
export function previewMixAudioKey(songsetId: string, hash: string): string
export function previewMixChaptersKey(songsetId: string, hash: string): string
export function previewMixErrorKey(songsetId: string, hash: string): string
```

3. `delivery/webapp/src/lib/db/songsets.ts` — add an owner-scoped read for the mix source, selecting **all** items (unlike `getSongsetEditorData`, which drops items whose Recording is deleted; a preview must not silently shorten the Songset):

```ts
export interface SongsetMixSourceRow {
  itemId: string
  position: number
  recordingHashPrefix: string | null
  recordingContentHash: string | null
  arrangementSteps: string | null
  gapBeats: number | null
  crossfadeEnabled: number | null
  crossfadeDurationSeconds: number | null
  songTitle: string | null
  durationSeconds: number | null
  tempoBpm: number | null
  recordingSections: unknown   // raw recordings.sections
  recordingBeats: unknown      // raw recordings.beats
  recordingDeletedAt: Date | null
}
export async function getSongsetMixSourceRows(
  songsetId: string,
  userId: number
): Promise<{ name: string; rows: SongsetMixSourceRow[] } | null>
```

   Implementation: one `songsets` row by `(id, userId)`; when absent return `null`; then one `songset_items` select ordered by `position`, left-joined to `recordings` and `songs`. Do not reuse `arrangementItemColumns` (it omits the columns above); do not add fields to it.

4. `delivery/webapp/src/lib/preview-mix/validate.ts` (new) — the pre-playback validator, moved verbatim from `src/lib/audio/arrangement-preview.ts` (`validateArrangementPreviewSources` → `validateSongsetAudioSources`), because the mix route and the browser engine both need it:

```ts
export type SongsetAudioErrorKind = "no-recording" | "invalid-arrangement" | "playback-failed"
export interface SongsetAudioError { kind: SongsetAudioErrorKind; itemId: string | null; message: string }
export function validateSongsetAudioSources(items: TimelineItemSource[]): SongsetAudioError | null
```

   Same checks, same messages, same kinds as today: `no-recording` for a missing `recordingHashPrefix`, `invalid-arrangement` from `validateSongArrangement`/`parseSongArrangement` per item (carrying that item's `itemId`). No React, no Web Audio, no `node:crypto` — safe from a client component.

5. `delivery/webapp/src/lib/preview-mix/server.ts` (new) — the single place that turns rows into both consumers:

```ts
export interface PreviewMixSnapshot {
  songsetName: string
  items: PreviewMixItemInput[]
  timelineSources: TimelineItemSource[]
  markers: { position: number; itemId: string; title: string; startSeconds: number; endSeconds: number }[]
}
export async function getPreviewMixSnapshot(songsetId: string, userId: number): Promise<PreviewMixSnapshot | null>
```

   Rules:
   - `items[i]` fields map 1:1 from the row; `recordingHashPrefix` falls back to `""`, `recordingContentHash` to `null`, `crossfadeEnabled` to `Boolean(crossfadeEnabled)`.
   - `timelineSources[i]` = `{ itemId, recordingHashPrefix, arrangementSteps, gapBeats, crossfadeEnabled, crossfadeDurationSeconds, tempoBpm, recordingDurationSeconds: durationSeconds, recordingSections: parseRecordingSections(recordingSections), recordingBeats: parseRecordingBeats(recordingBeats) }`.
   - `markers` computed from `buildArrangementTimeline(timelineSources)` (`item.startSeconds`, `item.startSeconds + item.durationSeconds`, `title = songTitle ?? ""`).
   - No validation here; the routes call the canonical validators (step 4).

6. `delivery/webapp/src/test/lib/preview-mix/hash.test.ts` (new) — stable hash across item array order; changes when `arrangementSteps`, `gapBeats`, `crossfadeEnabled`, `crossfadeDurationSeconds`, `recordingContentHash`, or `position` change; unchanged when `crossfadeEnabled` is `0` vs `false`; hex length 64.
7. `delivery/webapp/src/test/lib/preview-mix/validate.test.ts` (new) — the validation cases moved verbatim from the deleted `src/test/lib/audio/arrangement-preview.test.ts` (`no-recording`, invalid sibling arrangement with the offending `itemId`, null when every item is playable).

### 3. Worker preview path

1. `delivery/render-worker/src/sow_render_worker/preview_mix.py` (new):

```python
PREVIEW_MIX_PREFIX = "previews"
PREVIEW_MIX_KEEP = 3

def preview_mix_keys(songset_id: str, preview_hash: str) -> dict[str, str]:
    """{"audio": ..., "chapters": ..., "error": ...}"""

def execute_preview_mix(
    songset_id: str,
    user_id: int,
    preview_hash: str,
    conn: psycopg2.extensions.connection,
    asset_fetcher=None,
    uploader=None,
    lambda_context=None,
) -> None
```

   Body order (mirrors `pipeline.execute_render_pipeline` where it overlaps):
   1. Validate `preview_hash` with `re.fullmatch(r"[a-f0-9]{64}", preview_hash)`; raise `ValueError` otherwise.
   2. Ownership: `SELECT id FROM songsets WHERE id=%s AND user_id=%s`; raise `ValueError("Songset not found or access denied")` when absent.
   3. Clear the previous attempt's marker: `uploader.delete_preview_error(songset_id, preview_hash)` inside `try/except Exception: log.warning(...)`. This is what lets a retry leave the `failed` state; a build must not fail because an old marker could not be deleted.
   4. `songset_name, items = fetch_songset_items(conn, songset_id)`; empty → `ValueError("Songset has no items")`.
   5. Limits: raise when `len(items) > MAX_SONGSET_ITEMS` or `calculate_total_duration(items, asset_fetcher) > MAX_SONGSET_DURATION_SECONDS`, using the same messages as `pipeline.py`.
   6. Temp dir `asset_fetcher.get_job_temp_dir(f"preview-{preview_hash[:12]}")`; `generate_songset_audio(items, <temp>/output.mp3, asset_fetcher, job_id=f"preview-{preview_hash[:12]}")` — **no argument changes** (normalize/loudnorm/320k/44.1k/2ch defaults).
   7. `generate_chapters_manifest(list(audio_result.segments), asset_fetcher.download_lrc, audio_result.total_duration_seconds)`.
   8. `uploader.upload_preview_mix(songset_id, preview_hash, mp3_path, chapters)`; `uploader.delete_preview_error(songset_id, preview_hash)`; `uploader.prune_preview_mixes(songset_id, keep=PREVIEW_MIX_KEEP)`.
   9. `except Exception as exc:` → `uploader.write_preview_error(songset_id, preview_hash, str(exc)[:500])`, then re-raise so SQS retries. `finally:` `asset_fetcher.cleanup_temp()`.

   No `render_jobs`, no `songsets` writes, no progress rows.

2. `delivery/render-worker/src/sow_render_worker/uploader.py` — add:

```python
def upload_preview_mix(self, songset_id, preview_hash, mp3_path, chapters) -> None
def write_preview_error(self, songset_id, preview_hash, message: str) -> None
def delete_preview_error(self, songset_id, preview_hash) -> None
def prune_preview_mixes(self, songset_id, keep: int = 3) -> list[str]
```

   Keys exactly as pinned. `prune_preview_mixes`: `list_objects_v2(Bucket, Prefix=f"previews/{songset_id}/")` with pagination; group keys by the third path segment (the hash); best `LastModified` per hash; sort hashes by that timestamp descending; delete every object whose hash is beyond the first `keep`; return the deleted keys. Deleting a nonexistent key is not an error.

3. `delivery/render-worker/src/sow_render_worker/lambda_handler.py` — `_process_record` reads `kind = record_data.get("kind", "render")`; `"preview"` extracts `songsetId`/`userId`/`previewHash` (raise `ValueError` when missing) and calls `execute_preview_mix(...lambda_context=context)`; any other value falls through to the existing render path with the same validation as today.

4. Tests:
   - `delivery/render-worker/tests/test_preview_mix.py` — keys; owner check failure; empty Songset; valid hash requirement; upload keys and prune call; error marker written and successful retry deletes it; no `render_jobs`/`songsets` SQL executed (fake connection asserts).
   - `delivery/render-worker/tests/test_uploader.py` — extend: preview upload content types, prune groups by hash and keeps the 3 newest, idempotent delete.
   - `delivery/render-worker/tests/test_lambda_handler.py` — extend: `kind:"preview"` routes to `execute_preview_mix`; missing `kind` still routes to `execute_render_pipeline`.

### 4. Dispatch and HTTP routes

1. `delivery/webapp/src/lib/sqs/client.ts` — replace `SQSMessage` with:

```ts
export type RenderWorkerMessage =
  | { kind: "render"; jobId: string; songsetId: string; userId: number }
  | { kind: "preview"; songsetId: string; userId: number; previewHash: string }
```

   `delivery/webapp/src/lib/render/rest-client.ts` — import that type, drop the local duplicate, and `JSON.stringify` the message into the RIE record body. `delivery/webapp/src/lib/render/dispatcher.ts` — `dispatchToRenderWorker` keeps its render signature and adds `dispatchPreviewMix(message: Extract<RenderWorkerMessage, {kind:"preview"}>): Promise<void>`; both go through the same `getRenderWorkerMode()` branch and cached clients. Existing callers pass `{kind:"render", ...}`.

   Note in `specs/songset_preview_mix.md`: REST mode blocks until the worker finishes, so the build POST resolves only after the mix is written in local dev. Production uses SQS.

2. `delivery/webapp/src/app/api/songsets/[id]/preview-mix/route.ts` (new), `POST` + `GET`.

   Shared preamble: `auth.api.getSession` → 401; `getPreviewMixSnapshot(id, userId)` → 404 `{error:"Songset not found or access denied"}`; `computePreviewMixHash(snapshot.items)`.

   `GET` (poll; no side effects): construct `r2 = createR2ClientFromEnv()` inside `try`/`catch` → `503 {error:"R2 storage not configured"}` (same convention as `api/recordings/[hashPrefix]/audio/route.ts`). Then `await r2.getObjectSize(previewMixAudioKey(id, hash))`: non-null → `200 {status:"ready", hash, audioUrl: `/api/songsets/${id}/preview-mix/audio?hash=${hash}`, markers}`, with `markers` read from `chapters.json` via `const { url } = await r2.generateSignedUrl(previewMixChaptersKey(id, hash), "json", { expiresInSeconds: 60 })` then `fetch(url)`; an unreadable or malformed chapters object yields `markers: []` (the audio still plays). Else `getObjectSize(previewMixErrorKey(id, hash))` non-null → read that object the same way and return `200 {status:"failed", hash, error: <≤500 chars>}`; an unreadable marker returns `error:"Mix build failed"`. Else `200 {status:"missing", hash}`. `Cache-Control: no-store` on every branch.

   `POST` (ensure): `await getSongsetRenderMetrics(id, userId)` and map `SongsetRenderValidationError` exactly as `api/render-jobs/route.ts` does today (400 `SONGSET_TOO_LONG`, 404 `SONGSET_NOT_FOUND`, 409 with `error.message` + `code`). Then `validateSongsetAudioSources(snapshot.timelineSources)`; when it returns `no-recording` → `409 {error:"Songset has an item without a Recording", code:"NO_RECORDING"}`. Then: audio key exists → `200 {status:"ready", ...}` (same body as GET); else `await dispatchPreviewMix({kind:"preview", songsetId:id, userId, previewHash:hash})` → `202 {status:"building", hash, markers: snapshot.markers}`. A dispatch throw → `500 {error:"Failed to start mix build"}`. The route deletes and writes nothing: the worker clears the previous `error.json` at the start of every attempt (step 3).

   `validateSongsetAudioSources` comes from `src/lib/preview-mix/validate.ts` (step 2 item 4) — the same function the browser engine uses.

3. `delivery/webapp/src/app/api/songsets/[id]/preview-mix/audio/route.ts` (new) — `GET` with `?hash=`: 401 unauthenticated; `hash` must match `PREVIEW_MIX_HASH_PATTERN` and `id` must pass `isValidSongsetIdForKey` → 400; ownership via `db.query.songsets.findFirst({where: and(eq(songsets.id, id), eq(songsets.userId, Number(session.user.id)))})` → 404; `createR2ClientFromEnv()` throw → 503; `getObjectSize(previewMixAudioKey(id, hash))` null → 404; `const { url } = await r2.generateSignedUrl(previewMixAudioKey(id, hash), "audio", { expiresInSeconds: 60 })`; forward the request `Range` header; mirror `200/206`, `Content-Length`, `Content-Range`, `Accept-Ranges: bytes`, `Content-Type: audio/mpeg`, `Cache-Control: private, max-age=3600`; upstream fetch throw → 502. Copy the body handling verbatim from `api/recordings/[hashPrefix]/audio/route.ts:62-105` (it already streams the body and forwards ranges).

4. `delivery/webapp/src/app/api/songsets/[id]/preview-mix/timeline/route.ts` (new) — `GET`: 401/404 as above; return `200 {items: snapshot.timelineSources, markers: snapshot.markers, totalSeconds}` where `totalSeconds = buildArrangementTimeline(snapshot.timelineSources).totalSeconds`. `Cache-Control: no-store`.

5. Tests `delivery/webapp/src/test/api/songsets/preview-mix.test.ts` (new): 401; 404 unowned; hash matches `computePreviewMixHash`; `missing` then `building` after dispatch; `ready` body shape; `failed` with error text; POST 409 mapping for stale arrangement; audio route rejects a bad hash and streams 206 with `Content-Range`; timeline route payload.

### 5. Prewarm on arrangement save

`delivery/webapp/src/app/api/songsets/[id]/items/[itemId]/arrangement/route.ts` — after `updateSongArrangement` returns non-null and before `return NextResponse.json(item)`:

```ts
try {
  await prewarmPreviewMix(id, Number(session.user.id))
} catch {
  // never fails the save; the helper already logs
}
```

`prewarmPreviewMix(songsetId, userId)` lives in `delivery/webapp/src/lib/preview-mix/prewarm.ts` (new):
1. `getPreviewMixSnapshot`; return silently when null or `items.length === 0`.
2. `hash = computePreviewMixHash(items)`; return when `getObjectSize(previewMixAudioKey(songsetId, hash))` is non-null.
3. `dispatchPreviewMix({kind:"preview", songsetId, userId, previewHash: hash})`.
4. Catch everything, `console.warn("preview mix prewarm failed", …)`; never throw into the PUT response. The route awaits the helper (one HEAD + one SQS send) so the invocation is not cancelled after the response; it does not await the render.

Test `delivery/webapp/src/test/api/songsets/arrangement-persistence.test.ts` — extend: a successful save dispatches exactly one preview message with the post-commit hash; a reset does too; a rejected save does not dispatch; a dispatch rejection still returns 200.

### 6. Browser audio engine and hook

1. `delivery/webapp/src/lib/audio/songset-audio.ts` (new; replaces `arrangement-preview.ts`):

```ts
import type { SongsetAudioError } from "@/lib/preview-mix/validate"
export { validateSongsetAudioSources } from "@/lib/preview-mix/validate"
export type { SongsetAudioError, SongsetAudioErrorKind } from "@/lib/preview-mix/validate"

export interface SongsetAudioSessionOptions {
  audioUrlFor?: (hashPrefix: string) => string            // default `/api/recordings/${hashPrefix}/audio`
  createAudioContext?: () => AudioContext
  fetchArrayBuffer?: (url: string) => Promise<ArrayBuffer>
  prefetchLeadSeconds?: number                            // default 20
  tickIntervalMs?: number                                 // default 200
}

export interface SongsetAudioSession {
  readonly timeline: ArrangementTimeline
  readonly totalSeconds: number
  play(): Promise<void>
  pause(): void
  stop(): void
  seek(seconds: number): void
  getTimelinePositionSeconds(): number
  isPlaying(): boolean
  getError(): SongsetAudioError | null
  onStateChange(listener: () => void): () => void
}

export function createSongsetAudioSession(items: TimelineItemSource[], options?: SongsetAudioSessionOptions): SongsetAudioSession
```

   The validator lives in `src/lib/preview-mix/validate.ts` (step 2 item 4) and is re-exported here so the sheet and dialog keep one import surface.

   Session behaviour, in this order:
   - `timeline = buildArrangementTimeline(items)`.
   - `ensureDecoded(itemIndex)`: memoized `Promise` per item index. Fetch `audioUrlFor(item.recordingHashPrefix)` (`throw` `no-recording` when the hash is null), `await fetchArrayBuffer`, `context.decodeAudioData`. For each clip of that item, allocate `context.createBuffer(channels, Math.max(1, Math.round((clip.sourceEndSeconds - clip.sourceStartSeconds) * decoded.sampleRate)), decoded.sampleRate)` and copy each channel with `copyToChannel(decoded.getChannelData(ch).subarray(offset, offset + length), ch)` where `offset = clamp(round(clip.sourceStartSeconds * decoded.sampleRate), 0, decoded.length)`. Store the clip buffers, then drop the decoded buffer reference.
   - `scheduleFrom(position)`: stop/disconnect every previously created source; for each item create a `GainNode` connected to `destination`; for each clip with `clip.endSeconds > position`: `when = ctx.currentTime + max(0, clip.startSeconds - position)`, `offset = clip.sourceStartSeconds + max(0, position - clip.startSeconds)`, `duration = clip.endSeconds - max(position, clip.startSeconds)`, `source.start(when, offset, duration)` through the item gain. An item whose clips are not yet decoded is awaited by `play`/`seek` before scheduling.
   - Gain envelopes per item (identical to the deleted engine and the worker's sequential `afade` pair): incoming `0 → 1` across `[item.startSeconds, item.startSeconds + item.crossfadeSeconds]`; outgoing `1 → 0` across `[itemEnd − successor.crossfadeSeconds, itemEnd]`, where `itemEnd = item.startSeconds + item.durationSeconds`. Use `setValueAtTime`/`linearRampToValueAtTime` relative to `ctx.currentTime`, recomputed from the current position on every reschedule.
   - Clock: `position = startedPosition + (ctx.currentTime - startedAtContextTime)`, clamped to `totalSeconds`.
   - `play()`: `ctx.resume()`, `ensureDecoded(current)`, `scheduleFrom(position)`, start the ticker; `pause()` records the position, stops sources, `ctx.suspend()`; `seek(s)` clamps, and when playing reschedules from `s`; `stop()` clears timers, stops sources, closes the context, and makes the session inert (same as today).
   - Ticker (every `tickIntervalMs`): recompute position; if `position >= totalSeconds` pause at the end; else fire `ensureDecoded` for the first not-yet-decoded item with `item.startSeconds <= position + prefetchLeadSeconds` (fire-and-forget, errors surface as `playback-failed`); `notify()`.
   - Errors: fetch/decode rejection → `playback-failed` with the item id, stop playback, notify. No fallback mode, no analyser, no element.

2. `delivery/webapp/src/hooks/useSongsetAudio.ts` (new; replaces `useArrangementPreview.ts`) — same state machine as today (session created on first play, `itemsKey = JSON.stringify(items)` teardown, latest-ref error callback, `player.stop()` before starting), with:
   - `useSongsetAudio({ items, active, onPreviewError })` (no `onFallbackNotice`).
   - Result: `{ status, errorMessage, timelinePositionSeconds, totalSeconds, play, pause, stop, seek, startAtSeconds }` — **`startAt(itemId)` is deleted** (only a test used it) and **`isFallbackMode` is deleted**.
   - Re-export `SongsetAudioError` / `SongsetAudioErrorKind` so the sheet keeps one import surface.

3. `delivery/webapp/src/components/songset/ArrangementEditorSheet.tsx`:
   - Import `useSongsetAudio` instead of `useArrangementPreview`; drop the `onFallbackNotice` option.
   - Delete `fallbackNotice` state (line ~937), `handleFallbackNotice`, `showFallbackNotice` (line ~1899), and the notice JSX block that renders `t("arrangement.preview.fallbackNotice")` (~line 2550).
   - Everything else (windows, pass logic, strip, `preview.status/seek/startAtSeconds/play/pause/stop`) is unchanged.

4. Deletions: `delivery/webapp/src/lib/audio/arrangement-preview.ts`, `delivery/webapp/src/hooks/useArrangementPreview.ts`, `delivery/webapp/src/test/lib/audio/arrangement-preview.test.ts`, `delivery/webapp/src/test/hooks/useArrangementPreview.test.tsx`, and the `arrangement.preview.fallbackNotice` key in both locales of `src/lib/i18n/messages/arrangement.ts`.

5. Tests:
   - `delivery/webapp/src/test/lib/audio/songset-audio.test.ts` (new): fake `AudioContext` (recording `createBufferSource`, `start` arguments, `createGain` automation) asserting (a) first clip starts at offset `sourceStart + (position − clip.start)`, (b) a later clip is scheduled at `ctx.now + (clip.start − position)`, (c) crossfade ramps are scheduled on the incoming and successor-owned outgoing windows, (d) `seek` reschedules from the new position, (e) an item is decoded once per session and its full buffer is not retained (assert the clip buffers are distinct objects), (f) decode failure yields `playback-failed`, (g) `stop()` clears the ticker and closes the context.
   - `delivery/webapp/src/test/hooks/useSongsetAudio.test.tsx` (new): session creation on first play only; teardown on `items` change and on `active=false`; error forwarding; pending seek before play.

### 7. Listen dialog and row actions

1. `delivery/webapp/src/components/songset/ListenMixDialog.tsx` (new). Props: `{ songsetId: string; songsetName: string; isOpen: boolean; onOpenChange: (open: boolean) => void }`. Test ids: `listen-dialog`, `listen-play`, `listen-seek`, `listen-time`, `listen-tier`, `listen-build-status`, `listen-use-final`, `listen-retry`.

   State machine on open:
   1. `const { stop: stopGlobalPlayer } = useAudioPlayerContext()`; call it once, so two players never overlap.
   2. `POST /api/songsets/{id}/preview-mix` → `{status, hash, audioUrl?, markers, error?}`.
      - `ready`: set the server `<audio src={audioUrl}>`, position the seek bar with `markers`, and attempt `play()` once; on rejection show the Play button.
      - `building` (`202`): render the elapsed counter from `Date.now()` and start polling; fetch `GET .../preview-mix/timeline` and, on success, create the local session via `useSongsetAudio({ items, active: isOpen })` and attempt `play()` once (fallback to the Play button on rejection).
      - `failed`: show `error` + Retry (Retry re-POSTs) **and still start the local tier** — a failed build must not block listening. When the long-Recording guard applies (see contingencies), show the error + Retry only.
      - Non-2xx: map `code` `NO_RECORDING` → `previewMix.error.noRecording`, `ARRANGEMENT_*` → `previewMix.error.invalidArrangement`, else `previewMix.error.build`.
   3. Poll `GET .../preview-mix` every 1500 ms while open and not `ready`: on `ready` stop polling, keep the current playback running, and reveal the "use final mix" action which switches to the server `<audio>` at the current position; on `failed` stop polling and show the error + Retry. **Ignore `failed` for the first `BUILD_FAILURE_GRACE_MS = 5000` ms after the POST that started this attempt** (a worker may not yet have cleared the previous attempt's marker, step 3); after the grace, `failed` is terminal until Retry re-POSTs and resets the clock.
   4. Controls: play/pause, a range input over `[0, total]` (server: `audio.currentTime`; local: `session.seek`), `m:ss / m:ss`, the current marker title, a tier badge (`previewMix.tier.final` / `previewMix.tier.local`), and the marker strip (clicking a marker seeks).
   5. Close: `session.stop()`, pause the `<audio>`, clear polling and the elapsed timer.

   Both tiers must be driven by the same `position` state updated from `timeupdate` (server) or `session.getTimelinePositionSeconds()` on a 250 ms interval (local).

2. `delivery/webapp/src/components/songset/SongsetRow.tsx`:
   - `SongsetMenuAction`: `"render" | "worship" | "listen" | "downloadOffline" | "removeOffline"`.
   - Props: rename `onPlay` → `onWorshipPlayback`; add `onListen?: () => void`.
   - Button row: keep the `canPlayFreshRender` Worship button (label `t("songsets.action.worship")`, unchanged gating, `Play` icon), and add a Listen button when `onListen` is provided: `<Button variant="outline" size="sm" className="shrink-0 gap-1.5" onClick={onListen}><Headphones className="size-4" />{t("songsets.action.listen")}</Button>` (import `Headphones` from `lucide-react`).
   - Menu: `showMenuItem("worship")` item (icon `Play`, label `songsets.action.worship`) in place of the old `"play"` item, plus `showMenuItem("listen")` item (icon `Headphones`, label `songsets.action.listen`) directly below it.
3. `delivery/webapp/src/components/songset/SongsetList.tsx` — rename the `onPlay` prop to `onWorshipPlayback`, add `onListen?: (id: string) => void`, and pass both through to `SongsetRow`.
4. `delivery/webapp/src/app/songsets/SongsetsClient.tsx` — rename `handlePlay` → `handleWorshipPlayback`; add `listeningSongset` state (`{id,name} | null`); `handleListen = (id) => setListeningSongset(...)`; render `<ListenMixDialog>` next to `SongsetList`; pass `onWorshipPlayback`/`onListen`.
5. `delivery/webapp/src/app/worship/WorshipClient.tsx` — same rename; `menuActions={["worship","listen","downloadOffline","removeOffline"]}`; mount `ListenMixDialog` for the selected row.
6. Songset edit screen (`/songsets/[id]`):
   - `delivery/webapp/src/app/songsets/[id]/SongsetEditorClient.tsx` — rename the `handlePlay` callback to `handleWorshipPlayback`; add `isListenOpen` state; `handleListen = () => setIsListenOpen(true)`; render `<ListenMixDialog songsetId={songset.id} songsetName={songset.name} isOpen={isListenOpen} onOpenChange={setIsListenOpen} />`; pass `onListen={handleListen}` to `SongsetEditor`.
   - `delivery/webapp/src/components/songset/SongsetEditor.tsx` — add `onListen: () => void` to `SongsetEditorProps` and a `Headphones` menu item directly after the `onPlay` item: `{t("songsets.action.listen")}`. Leave the `onPlay` prop name and its menu item unchanged.
   - The dialog plays **saved** arrangements only; unsaved sheet edits are not in the mix until Apply.

### 8. Strings and test updates

1. `delivery/webapp/src/lib/i18n/messages/songsets.ts` — rename `songsets.action.play` to `songsets.action.worship` (`"Worship"` / `"敬拜"`) and add `songsets.action.listen` (`"Listen"` / `"試聽"`), both locales. Update **every** `t("songsets.action.play")` call site: `SongsetRow.tsx:190` (button) and `:228` (menu), `DashboardSongsetCard.tsx:98`, `SongsetEditor.tsx:358`. Leave `songsets.action.playAnyway` alone (its text is "Play anyway", a stale-render warning, not the action name).
2. `delivery/webapp/src/lib/i18n/messages/previewMix.ts` (new bundle) + register in `messages.ts` (`previewMixBundle` in the import list and the bundle array). Keys, both locales: `previewMix.title` (Listen to mix / 試聽混音), `previewMix.description`, `previewMix.tier.final` (Final mix / 完成混音), `previewMix.tier.local` (Local preview / 本機試聽), `previewMix.status.building` (Preparing final mix… {seconds}s / 正在準備完成混音…{seconds} 秒), `previewMix.status.localNotice` (Playing local preview — loudness may differ from the render / 正在播放本機試聽，音量可能與完成渲染不同), `previewMix.status.finalReady`, `previewMix.action.useFinal`, `previewMix.action.play`, `previewMix.action.pause`, `previewMix.action.retry`, `previewMix.action.close`, `previewMix.markersAria`, `previewMix.seekAria`, `previewMix.currentItem`, `previewMix.error.noRecording`, `previewMix.error.invalidArrangement`, `previewMix.error.playbackFailed`, `previewMix.error.build`.
3. Update tests asserting the old labels/props: `src/test/components/songset/SongsetRow.test.tsx` (Worship label + Listen button/menu, `onWorshipPlayback`), `src/test/components/songset/SongsetList.test.tsx`, `src/test/components/songset/SongsetEditor.test.tsx` (new `onListen` prop), `src/test/components/songset/SongsetEditorClient.test.tsx`, `src/test/app/worship-client.test.tsx`, `src/test/components/songset/ArrangementEditorSheet.test.tsx` (drop `isFallbackMode`, mock the new hook), `src/test/lib/i18n/messages.test.ts` (locale parity for the renamed and new keys).
4. New `src/test/components/songset/ListenMixDialog.test.tsx`: cache hit → server audio only, no timeline fetch; cache miss → local session created, POST dispatched once, poll flips to ready and reveals "use final mix"; `failed` shows Retry **and still creates the local session**; the long-Recording guard (`durationSeconds > 420`) skips the local session; close stops the session.

## Critical files & anchors

- `delivery/webapp/src/lib/arrangements.ts` — `buildArrangementTimeline`, `calculateGapSeconds`, `crossfadeSecondsFor`, `TimelineClip`; the engine and the markers must reuse these, never re-derive the layout.
- `delivery/render-worker/src/sow_render_worker/audio_engine.py` — `generate_songset_audio` (defaults are the pinned mix settings) and `calculate_total_duration` (admission).
- `delivery/render-worker/src/sow_render_worker/pipeline.py` — `fetch_songset_items` (arrangement re-validation + limits) to reuse in `execute_preview_mix`.
- `delivery/webapp/src/app/api/render-jobs/route.ts` — the exact `SongsetRenderValidationError` → HTTP mapping the preview POST must copy.
- `delivery/webapp/src/components/songset/ArrangementEditorSheet.tsx` lines ~930-960 (fallback state), ~1150-1170 (hook options), ~1899 and ~2540-2560 (fallback notice) — the only sheet regions that change.

## Verification

Prerequisites: `delivery/webapp/.env` with DB/R2 credentials; `SOW_RENDER_WORKER_MODE=sqs` for the production path or `rest` with a running worker for local.

1. `cd delivery/webapp && pnpm test -- run src/test/lib/preview-mix src/test/lib/audio/songset-audio.test.ts src/test/hooks/useSongsetAudio.test.tsx src/test/components/songset/ListenMixDialog.test.tsx src/test/api/songsets/preview-mix.test.ts src/test/api/songsets/arrangement-persistence.test.ts src/test/components/songset/SongsetRow.test.tsx src/test/components/songset/SongsetList.test.tsx src/test/components/songset/SongsetEditor.test.tsx src/test/components/songset/SongsetEditorClient.test.tsx src/test/components/songset/ArrangementEditorSheet.test.tsx src/test/lib/i18n/messages.test.ts src/test/app/worship-client.test.tsx` — all pass.
2. `cd delivery/webapp && pnpm lint && pnpm build` — clean.
3. `cd delivery/render-worker && uv run --extra dev pytest tests/test_preview_mix.py tests/test_uploader.py tests/test_lambda_handler.py tests/test_audio_engine.py -v` — all pass.
4. Production-path smoke (SQS mode, worker deployed): open `/songsets`, press **Listen** on a Songset with saved arrangements. Expect, in order: the local tier starts within a few seconds (status line shows the local notice and elapsed build seconds); the row's render badge does not change; `GET /api/songsets/{id}` still reports the same `renderState`; no row appears in that Songset's render history.
5. Confirm the artifact: after the build, `GET /api/songsets/{id}/preview-mix` returns `status:"ready"`, and the audio route answers `curl -r 0-1023 -D -` with `206` and `Content-Range`. Reopen Listen: the dialog plays the final mix immediately, with markers and working scrub (seeking is a native `<audio>` seek).
6. Confirm invalidation and prewarm: save a bounds edit in the sheet; within seconds a new `previews/{songsetId}/{newHash}/` prefix exists in R2 and the old one remains (keep-3); reopen Listen → final mix plays; undo the edit and reopen → the previous hash still exists (no re-render).
7. Confirm retention: trigger four distinct hashes for one Songset; `previews/{songsetId}/` contains exactly three hash prefixes afterwards.
8. Confirm the editor transport: open the sheet, play the arrangement preview and a transition review, edit a bound and Apply, then audition again — the new range plays, boundaries are seamless, and there is no stutter or wrong-range playback. On iOS Safari this is the manual check; on Android Chrome desktop emulation is not a substitute.

## Assumptions & contingencies

- **Listen vs Listen page naming**: the nav page `/listen` is catalog browsing. The row action is labelled "Listen" (`試聽`) and the dialog title "Listen to mix" (`試聽混音`); CONTEXT.md's new terms define the distinction. If the label collision proves confusing, rename only the dialog title (`previewMix.title`), not the route.
- **REST mode blocks**: `dispatchPreviewMix` in `rest` mode returns after the worker finishes, so the build POST resolves late in local dev only. Production is SQS. A non-blocking dev dispatch is out of scope for this change; local dev accepts the wait.
- **Long Recordings**: decoded PCM is ~384 KB/s stereo at 48 kHz, so a Recording over ~7 minutes risks an iOS memory spike. Contingency: when any snapshot item's `durationSeconds > 420`, the dialog skips the local tier and shows only the build status (final mix still builds). Implement this as a guard in `ListenMixDialog` before creating the session; no engine change.
- **Duplicate builds**: prewarm and the dialog POST can both pass the `HeadObject` check and dispatch the same hash. Output is idempotent and the key is overwritten; accepted, no lock.
- **Worker failure surfacing**: no job row means no progress or failure channel except `error.json`. Any exception writes that marker; the worker clears it at the start of the next attempt, so a retry is not stuck in `failed`. SQS retries may overwrite it with success. The dialog treats `failed` as terminal once the 5 s post-POST grace has passed.
- **Retention does not run on Songset delete**: like `renders/` today, deleting a Songset leaves its `previews/` objects. Accepted; storage is bounded by the keep-3 sweep per Songset and small at this scale.
- **The hash excludes analysis**: `arrangementSteps` (the persisted bounds) is in the payload, but `recordings.sections`/`beats` are not, because the mix audio does not depend on them. If a Recording is re-analysed and the saved bounds no longer validate, the worker's re-validation fails and writes `error.json` for that hash; the dialog then shows the failure and the local tier fails the same validation. Re-saving the arrangement produces a new hash. Accepted.
- **Unsaved edits are not audible in the mix**: the dialog plays saved arrangements. The sheet's own audition remains the way to hear a change before Apply.
