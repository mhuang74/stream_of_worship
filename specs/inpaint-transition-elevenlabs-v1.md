# ElevenLabs Glue in the Stream of Worship webapp — implementation plan

Status: proposed, awaiting confirmation. Branch untouched.

## 1. What ships

A Songset Item can own an **outgoing Glue** ("AI transition to next song"). When enabled and generated, the Glue replaces the gap/crossfade at that one junction in three places: the Arrangement Editor preview, the Songset Audition (Listen), and the rendered audio/video. The outgoing and incoming Song Arrangements remain untouched; the lyric/chapter timeline shifts by the Glue's length.

The trigger is an explicit **Generate AI transition** button in the Arrangement Editor's Preview panel, next to Play/Stop. Preview and Listen only play Glues that already exist; they never spend money. The render page can also generate missing Glues (preflight remedy). Listen/Preview fall back to the gap at any junction without a ready Glue.

**Settled decisions**

| Decision | Choice |
|---|---|
| Where generation runs | Webapp route (`nodejs`, `maxDuration = 60`); browser builds the excerpts; ElevenLabs called server-side |
| Excerpt length | 12 s per side (last 12 s of A's arranged audio, first 12 s of B's), per the vendor's copyright-safe recipe; both sides uploaded at most once **per user** |
| Cache | Per-user, content-addressed composition cache keyed by `(user_id, junction descriptor)`; uploads cached by **logical excerpt identity** (side + Recording content hash + Steps + length), not decoded bytes |
| Persistence | `glue_enabled`, `glue_beats` on the **outgoing** item; resolved `glue_key`/`glue_seconds` denormalized on the same item for duration-only reads |
| Generation inputs | Saved Arrangements only; dirty draft suppresses both adjacent Glues and disables Generate |
| Render when a Glue is missing | Hard block (409 `GLUE_NOT_READY`), no silent fallback. Render page lists the junctions and offers generation; explicit opt-out action available |
| Re-roll | None in v1; a different descriptor (beats, arrangement, recording) is a different Glue |
| Daily limit | 5 paid generation attempts per user per rolling 24 h; cache hits free; a paid attempt consumes one even if it later fails (credits are spent at ElevenLabs) |
| Video | Pre-roll the incoming song's title during the Glue window only; no outgoing lyrics; chapter boundaries unchanged |
| Preview generation failure | Play the gap, show a non-blocking error/warning |
| Consequence: generation/transition edits | Bump item/songset timestamps so the render badge turns stale (the rendered output no longer matches) |

## 2. Domain language (CONTEXT.md additions)

- **Junction** — the boundary between two adjacent Songset Items in playback order, owned by the outgoing Item. A Junction already owns the incoming item's gap/crossfade parameters; it can now additionally own a Glue.
- **Glue Excerpt** — the 12 s arranged edge of one side of a Junction (A's tail or B's head), identified by its source (Recording + canonical Steps + side + length), uploaded to ElevenLabs at most once per user and reusable across that user's Songsets.
- **Glue Descriptor** — the canonical content identity of a Junction for Glue purposes: both Recording content hashes, both canonical Arrangement Step lists (or `null` for an untouched Recording), the excerpt length, the beat count, the incoming tempo, the model and the prompt version.
- **Glue Composition** — a generated, gain-staged, faded Glue audio object addressed by `(user, Glue Descriptor)`. Shared by every Songset of that user whose Junction has the same Descriptor.
- Add to the existing **Glue** entry: a Glue replaces the gap/crossfade at its Junction; it never changes either side's audio.

ADR `docs/adr/0014-elevenlabs-glue-in-webapp.md`: why the webapp route + per-user descriptor cache, why 12 s excerpts, why browser-built excerpt bytes are trusted only within the owning user, why no re-roll, why the render blocks, and the 60 s Vercel trade-off. Alternatives recorded: render-worker/analysis-service excerpt construction (canonical bytes, rejected for v1 latency/scope but the escape hatch if cross-client byte reuse ever matters), render-worker generation (rejected: slower feedback, ElevenLabs inside Lambda), per-Songset cache (rejected: duplicate spend), 20 s/whole-side uploads (rejected: cost + copyright screening).

## 3. Data model

`delivery/webapp/src/db/schema.ts` + generated migration:

```text
glue_excerpts
  user_id bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE
  logical_key text NOT NULL       -- sha256({v, side, seconds, contentHash, steps})
  sha256 text NOT NULL            -- bytes actually uploaded (audit only)
  elevenlabs_song_id text NULL
  seconds real NOT NULL
  head_rms_db real NOT NULL       -- first 200 ms, mono RMS
  tail_rms_db real NOT NULL       -- last 200 ms, mono RMS
  rejected_at timestamptz NULL    -- copyright/entitlement refusal cache
  rejection text NULL
  created_at timestamptz NOT NULL DEFAULT now()
  PK (user_id, logical_key)

glue_compositions
  user_id bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE
  key text NOT NULL               -- sha256 of canonical descriptor
  r2_key text NOT NULL            -- glues/<user_id>/<key>.wav
  seconds real NOT NULL           -- actual staged length
  beats integer NOT NULL
  model_id text NOT NULL          -- music_v2_5
  prompt_version integer NOT NULL
  from_excerpt_key text NOT NULL
  to_excerpt_key text NOT NULL
  metrics text NOT NULL           -- JSON: status, reasons, r_a/r_b, envelopes, gains
  created_at timestamptz NOT NULL DEFAULT now()
  PK (user_id, key)

glue_generations
  id serial PK
  user_id bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE
  created_at timestamptz NOT NULL DEFAULT now()
  index (user_id, created_at)

songset_items (add)
  glue_enabled integer NOT NULL DEFAULT 0
  glue_beats real NOT NULL DEFAULT 4
  glue_key text NULL              -- only while enabled + ready
  glue_seconds real NULL          -- only while enabled + ready

render_jobs (add)
  glue_transitions text NULL      -- JSON snapshot, see §7
```

`glue_seconds`/`glue_key` are a denormalization for duration-only reads (list summaries, cap admissions). The source of truth is always Descriptor + `glue_compositions`, re-resolved in the editor and render paths for the owning user.

Trust boundary: the server cannot verify that the bytes a browser sends are really the arranged edges of the claimed Recording/Steps. Compositions and excerpts are therefore **scoped to the user**; a user can only seed audio that their own renders will play. Cross-user reuse is sacrificed; intra-user reuse across Songsets (the actual 5-song-set cost case) remains. Server-side canonical excerpt construction is the recorded alternative if cross-user sharing is ever required.

## 4. Descriptor and excerpt

### Descriptor (`src/lib/glue/descriptor.ts`, server-only)

```ts
{ v: 1, model: "music_v2_5", promptVersion: 1, excerptSeconds: 12,
  beats: <number>, toTempoBpm: <number>,          // rounded 6 dp
  from: { contentHash: string, steps: [[s,e],…] | null },
  to:   { contentHash: string, steps: [[s,e],…] | null } }
```

- Steps are the canonical parsed array (playback order, `sectionIndex` etc. excluded), bounds rounded to 6 dp with the repo's `Math.round(x*1e6)/1e6`.
- `null` = untouched Recording (full audio); do not synthesize `[[0,duration]]`.
- Key = `sha256(JSON.stringify(payload))`; explicit field order, no item ids/song titles.
- `toTempoBpm` participates so re-analysis changes the target length → new Glue.

### Excerpt builder (`src/lib/glue/excerpt.ts`, browser)

Shared by the editor and render page. Inputs: saved `TimelineItemSource[]`, junction index, `sharedAudioBytesCache`, `OfflineAudioContext(2, 1, 44100)`.

- Decode each side's Recording at 44.1 kHz via `audioBytesCache.load(prefix, fetchAudioBytes(recordingAudioUrl(prefix)))`.
- A: walk the canonical Steps backwards, taking `[step.end − need, step.end]` until 12 s; then re-order forward. B: walk forwards taking `[start, start + need]`.
- No Steps → take the Recording's tail/head.
- Short side → use all of it; reject a side < 1 s before calling.
- Interleave to stereo PCM16 LE (mono duplicated, clamp, symmetric rounding), 44-byte WAV header (`RIFF`/`fmt ` PCM 16-bit 2ch 44100). ~2.12 MB per 12 s side; two sides ≈ 4.23 MB, under Vercel's 4.5 MB request limit.
- Pure helpers (`extractEdge`, `encodeWavPcm16`) unit-tested with duck-typed buffers.
- Decoding and resampling are implementation-defined: Chrome/Safari can produce different PCM16 bytes for the same source. That is why the upload cache is keyed by **logical identity** server-side, not by the received bytes (see §5).

## 5. Generation route

`POST /api/songsets/[id]/glues` — `multipart/form-data`, `runtime = "nodejs"`, `maxDuration = 60`.

Fields: `fromItemId`, `fromUpdatedAt`, `toUpdatedAt`; files `fromExcerpt`, `toExcerpt`.

Flow:

1. Auth, ownership, item ordering. Derive the next **visible** item; 400 if none. Reject if `glue_enabled = 0`.
2. **Optimistic snapshot check** — compare both items' DB `updated_at` to the client's ISO values. Mismatch → 409 `GLUE_STALE_SNAPSHOT` *before* any spend. Closes the "old excerpt + new descriptor" poisoning window.
3. Load both recordings' `content_hash`, `tempo_bpm`; validate both saved arrangements structurally (and against current analysis exactly as `getSongsetRenderMetrics` does). Missing tempo → 422 `GLUE_TEMPO_UNAVAILABLE`.
4. Parse/validate both WAVs: RIFF PCM16, 2ch, 44100 Hz, 1 s ≤ duration ≤ 12.1 s, bytes ≤ 2.5 MB each. (The bytes are used only if that side's excerpt is not cached.)
5. Compute Descriptor; look up `glue_compositions` for `(user_id, key)`:
   - hit → repair the item's `glue_key`/`glue_seconds`, return `{key, seconds, status, reasons, cached: true, itemUpdatedAt}` (0 credits, no quota).
   - miss → continue.
6. Compute both **logical excerpt keys** server-side from the saved source: `sha256({v:1, side:"A"|"B", seconds: min(12, arrangedSideSeconds), contentHash, steps})`. Look up `glue_excerpts` for `(user_id, logical_key)`.
   - A cached rejection → translated error, no spend.
   - A hit needs no upload; the client's bytes for that side are discarded even if they differ (different browser decoder).
7. **Daily quota**, only if at least one upload or the compose will be paid: transaction with `SELECT users.id … FOR UPDATE`, count `glue_generations` in the last 24 h, 429 `GLUE_DAILY_LIMIT` at ≥ 5, else insert. Lock is released before any network call.
8. Upload the misses **in parallel** (`Promise.all`, two `POST /v1/music/upload` calls): store `(user_id, logical_key, sha256, song_id, seconds, head/tail RMS)`; a copyright rejection stores `rejected_at`/`rejection` too. No MP3 fallback in v1 (12 s WAV is 2 MB); a 413/415 surfaces a clear error. A cache-hit side reuses its stored `song_id` with the plan's whole-range reference.
9. Compose: `POST /v1/music?output_format=pcm_44100`, body:

```json
{ "model_id": "music_v2_5", "seed": <first 4 bytes of key>, "composition_plan": { "chunks": [
  { "song_id": "<A>", "range": { "start_ms": 0, "end_ms": <aRefMs> } },
  { "text": "<POC glue text>", "duration_ms": max(3000, round(beats*60000/tempo)),
    "positive_styles": [<POC styles>], "negative_styles": [<POC styles>],
    "context_adherence": "high",
    "conditioning_ref": { "song_id": "<B>", "range": { "start_ms": 0, "end_ms": min(8000,bRefMs) } },
    "condition_strength": "high" },
  { "song_id": "<B>", "range": { "start_ms": 0, "end_ms": <bRefMs> } } ] } }
```

The whole route budget is ~55 s: form parse, parallel uploads, compose, staging, R2 put, DB writes. Uploads and compose each abort at 50 s from their start; the route returns a typed timeout instead of being killed at 60. If measured real latency cannot fit, the recorded fallback is splitting into two routes (upload → compose) or moving generation to the worker, not losing paid audio silently.

Upstream errors map to typed messages (401/402/403 entitlement/credits, copyright, 422 plan, 429 concurrency, 5xx).

10. Stage (`src/lib/glue/stage.ts`, mirror of the POC's measured "plan geometry" path):
   - Decode PCM16 stereo; `aRef = A samples`, `gen = generation ms * 44.1`.
   - Returned length must be within 0.1 s of `aRef + gen + bRef`; glue window = `[aRef, min(total, aRef+gen)]`.
   - Envelope-check the returned references: 50 ms RMS-block Pearson of A's last 3 s vs the window's preceding 3 s, and B's first 3 s vs the following 3 s; ≥ 0.90 is `ok`, below is `check` (still keep the paid audio).
   - `takeTail(generated, round(glueSeconds*44100))`; tolerate ≤ 0.1 s shortfall; reject a glue RMS < −50 dBFS.
   - Gain-stage from A's last 200 ms to B's first 200 ms, per-sample dB ramp, clamp ±18 dB, peak-scale if > 0.99, then 20 ms linear fades. `seconds` = actual staged length.
11. `PutObject glues/<user_id>/<key>.wav` (content type `audio/wav`), then one transaction: insert `glue_compositions` + update item `glue_key`, `glue_seconds`, `updated_at`; bump the Songset `updated_at`.
12. Return `{key, seconds, status, reasons, cached: false, itemUpdatedAt}`.

Proxy: `GET /api/glues/[key]/audio` — auth required, `key` matches `^[0-9a-f]{64}$`, look up the composition for `(session user, key)`, mint a 60 s R2 URL, stream the body, `Content-Type: audio/wav`, `Cache-Control: private, max-age=31536000, immutable`. Mirror `api/recordings/[hashPrefix]/audio/route.ts`.

Env: `SOW_ELEVENLABS_API_KEY` (server). Missing → 503 with a clear message. Document in `DEPLOY-VERCEL.md`.

R2: add `putObject(key, body, contentType)` to `src/lib/r2/client.ts` (`PutObjectCommand`).

Client helper: `src/lib/glue/client.ts` → builds both WAVs, POSTs, parses typed errors (`code`), returns the result; shared by editor and render page.

## 6. Webapp integration

### Layout (`src/lib/arrangements.ts`)

- `ArrangementItemInput` + `TimelineItemSource`: `glueSeconds?: number | null` (outgoing, effective), `glueKey?: string | null` (outgoing).
- `TimelineItemSource` gets `glueKey`; `ArrangementTimelineItem` gets `glueBeforeSeconds` and `glue: {startSeconds, endSeconds, key} | null`.
- `layoutSongsetItems`, item `i > 0`, with `hasGlue = (items[i-1].glueSeconds ?? 0) > 0 && items[i-1].glueKey`:
  - `gapBefore = hasGlue ? 0 : calculateGapSeconds(item)`
  - `crossfade = hasGlue ? 0 : crossfadeSecondsFor(...)`
  - `start = max(0, previousEnd + (hasGlue ? glueSeconds : gapBefore) - crossfade)`
  - `glue = hasGlue ? {start: start - glueSeconds, end: start, key} : null`
- `getSongsetDurationSeconds` inherits it, so every duration call site includes the Glue.

### Duration / invalidation matrix

| Site | Change |
|---|---|
| `toArrangementInput` (`lib/db/songsets.ts`) | accept/pass `glueSeconds` |
| `arrangementItemColumns` | add `glueEnabled`, `glueBeats`, `glueKey`, `glueSeconds`, `contentHash`, `songTitle` |
| list summaries (`itemDurationInputs` JSON) | include `glue_seconds` |
| editor payload, `getSongset`, render page, metrics, create-songset | pass `glueSeconds` through `toArrangementInput` |
| `updateSongArrangement` | clear `glue_key`/`glue_seconds` on the saved item **and its predecessor**; prospective cap check includes the surviving glues |
| `updateSongsetItem` | on `songId`/`recordingHashPrefix` change clear this + predecessor; on `glueEnabled=0` or `glueBeats` change clear this; bump `updated_at` |
| add/delete/reorder item | clear all Glues in the Songset (compositions stay cached for this user; re-generation is a cache hit) |
| `duplicateSongset` | copy `glue_enabled`, `glue_beats`, `glue_key`, `glue_seconds` (descriptors are identical; the copy belongs to the same user) |
| `getSongsetRenderMetrics` | resolve descriptors for the owner, sum resolved Glue seconds, return the snapshot, throw `GLUE_NOT_READY` with junction details |
| `SongsetEditorClient` | after arrangement save also clear readiness on the saved item + predecessor; recompute local duration with glue-aware inputs |

### Editor payload

`SongsetItemDetail` adds `updatedAt`, `glueEnabled: boolean`, `glueBeats: number`, `glueReady: boolean`, `glueSeconds: number | null`, `glueKey: string | null`, `glueUnavailable: "no-next" | "no-tempo" | "no-analysis" | "invalid-arrangement" | null`.

`getSongsetEditorData` resolves each enabled junction (one `inArray` over `(user_id, key)`) and reports composition-backed values, not cached columns. `updateSongsetItem` re-resolves its outgoing junction and returns the fresh readiness, so toggling back on after a prior generation shows Ready immediately.

### Preview engine (`src/lib/audio/songset-audio.ts`, `useSongsetAudio.ts`)

- Decode each prepared item's `glue` WAV through the shared byte cache (`glue:<key>` key) and a neutral gain; no envelopes (the layout already zeroed crossfade/gap).
- `preparedGlues`, `scheduledGlues`; `scheduleGlue(index)` schedules at `glue.start − at` with a buffer offset when seeking into the Glue; called from `fullRebuild` and `scheduleNewlyDecoded`; torn down with the other graphs.
- Glue fetch/decode failure is **non-fatal**: set `glueWarning` in the session snapshot and play the gap. Exposed by `useSongsetAudio`; shown in the editor rail and the Listen player bar.
- `itemsKey` already includes glue fields, so readiness changes recreate the session.

### Arrangement Editor (`ArrangementEditorSheet.tsx`)

Controls in the Preview rail, above the transport:

- `Switch` "AI transition to {next song}" (hidden on the last item); PATCH `glueEnabled` immediately, optimistic + rollback.
- Beat selector 2 / 4 / 8 / 16 (default 4); PATCH `glueBeats`; changing beats clears readiness locally. Shows the target duration when the incoming tempo is known.
- **Generate AI transition** button next to Play/Stop when enabled, a next item exists, not ready, saved, both arrangements valid and analysed. Disabled with a reason otherwise ("Save changes to enable…", "Next song has no tempo analysis"). Shows `Generating…` and inline errors; estimated cost `2 × 12 × 15 + glueSeconds × 15` credits and "5 per day".
- Ready state shows `Ready · {duration}`. No regenerate action (v1).
- A note that the Glue replaces the gap/crossfade at this Junction.
- Preview sources suppress indices `itemIndex-1` and `itemIndex` while dirty (`boundsPreviewOverride` included), so a draft never auditions a Glue built from other audio.
- Strip: add a distinct `glue` band for any Glue in the window (incoming before the current song and outgoing before the next); the window's tail with an outgoing Glue ends at `nextStart + 8 beats` (not `songEnd + 8 beats`), so the arrival into B is audible.
- Listen/Preview do not generate.

### Render page (`RenderForm`, `RenderPageClient`, `getRenderPageData`)

- Payload adds the ordered glue-relevant items (id, title, recording prefix, content hash, arrangement, tempo, duration, `glueEnabled`, `glueBeats`) and `missingGlues` + per-junction reason.
- Missing Glues → warning panel listing "A → B", a `Generate missing transitions` button (sequential, browser-built excerpts, same helper), and an explicit `Disable AI transitions and render` action (confirmation; PATCHes the enabled junctions off).
- `POST /api/render-jobs` 409 `GLUE_NOT_READY` → toast naming the junctions; `createRenderJob` snapshots the resolved Glues.

### Listen / controller

- `ListenSongsetItemPayload` + `toListenTimelineSources`: pass outgoing `glueSeconds`/`glueKey`.
- Player bar shows the Glue warning when a fetch fails; otherwise plays ready Glues and gaps.
- The controller's chapter heading map uses the same timeline sources, so Section Label headings stay aligned with the shifted global times.

## 7. Render worker

`render_jobs.glue_transitions` snapshot (written at job creation):

```json
[{ "to_item_id": "<incoming item id>", "key": "<descriptor>", "r2_key": "glues/<user_id>/<key>.wav", "seconds": 2.786 }]
```

- `db.py`: `RenderJob.glue_transitions: str | None`, parse in `_row_to_render_job`.
- `asset_fetcher.py`: `download_glue(key)` → `glue-<hex>.wav` in the cache dir, signed URL with `content_type="audio/wav"`, streamed like `download_audio`.
- `pipeline.py`: after `fetch_songset_items`, parse the snapshot, map `to_item_id → item`, download all Glues into `dict[item_id, GlueClip]`; any missing item/object fails the job. Pass to `calculate_total_duration` and `generate_songset_audio`.
- `audio_engine.py`:
  - `GlueClip(path, seconds)`; `generate_songset_audio(..., glues: dict[str, GlueClip] | None)`, same for `calculate_total_duration`.
  - For item `i > 0` with a Glue: `gap_ms = crossfade_ms = 0`; `start_ms = current_end`; append an `audio_files` entry before the item: `{path: glue, duration_ms: glue_seconds, start_ms: current_end, crossfade_ms: 0, clips: [full]}`; then `start_ms = current_end + glue_ms`. The previous item's `next_crossfade_ms` is that entry's 0 → no fade-out. The Glue is already faded/staged.
  - `ExportResult.glue_windows: tuple[tuple[float,float], ...]` (start = previous end, end = incoming start) for the video.
  - `segments` stay one per song, shifted; lyrics/chapters inherit the shift automatically.
- `video_engine.py` / `frame_renderer.py`: thread optional `glue_windows` through `generate_video` → `encode_video_with_ffmpeg` → `render_frame_bytes`/`render_frame` → `_resolve_visual_state`. Inside a Glue window with no covering segment, return a state with `current_title = next segment's title`, `current_segment = None`, `current_song_lyrics = []` (no lyrics, title at the top, no intro card). Ordinary gaps unchanged. Cache key already distinguishes it. Test asserts a frame at the Glue midpoint has the incoming title and no lyric text.
- Chapter manifest: unchanged (Glue is uncovered time).

## 8. Errors, limits, privacy

- Typed UI messages: not configured, plan/entitlement, credits, copyright (half fee), invalid plan, concurrency, upstream/timeout, daily limit, stale snapshot, no tempo, invalid/stale arrangement.
- No automatic retries; the POC's measured behavior is that retries do not fix musical fit and cost credits.
- Route timeout risk (60 s): uploads are parallel; compose + staging + R2 + DB share the remaining budget; each upstream call aborts at 50 s so the route returns a typed error rather than being killed. A compose that exceeds ~50 s still loses its paid audio; accepted per decision, recorded in the ADR. Split routes / worker generation is the fallback if real latency demands it.
- 12 s excerpts of user-owned audio are sent to ElevenLabs. Excerpts and compositions are scoped to the user; the server trusts the browser-built PM16 bytes for that user (no cross-user reuse, no cross-user poisoning). R2 Glue objects are content-addressed under `glues/<user_id>/` and never deleted.
- Browser decode differences are absorbed by the logical excerpt key: a second browser reuses the first upload's `song_id` instead of paying again.

## 9. File map

Webapp:
- `src/db/schema.ts`, `drizzle/0028_*.sql`
- `src/lib/glue/{descriptor,excerpt,wav,stage,resolve,client,elevenlabs}.ts`
- `src/app/api/songsets/[id]/glues/route.ts`, `src/app/api/glues/[key]/audio/route.ts`
- `src/lib/db/songsets.ts`, `src/lib/render/job-manager.ts`, `src/app/api/render-jobs/route.ts`
- `src/lib/r2/client.ts`
- `src/lib/arrangements.ts`, `src/lib/arrangements-listen.ts`
- `src/lib/audio/{songset-audio,envelopes}.ts`, `src/hooks/useSongsetAudio.ts`
- `src/components/songset/ArrangementEditorSheet.tsx`, `SongList.tsx`, `src/app/songsets/[id]/SongsetEditorClient.tsx`, `src/components/audio/SongsetPlayerBar.tsx`
- `src/components/render/RenderForm.tsx`, `src/app/songsets/[id]/render/RenderPageClient.tsx`
- i18n en + zh-Hant; `DEPLOY-VERCEL.md`

Worker:
- `src/sow_render_worker/{db,asset_fetcher,audio_engine,video_engine,frame_renderer,pipeline}.py`

Docs: `CONTEXT.md`, `docs/adr/0014-*.md`.

## 10. Verification

Unit/integration (no paid calls):
- Descriptor determinism, field sensitivity, step-order sensitivity.
- Excerpt extraction across Steps/both sides/short recordings; WAV header and PCM16 encoding.
- Staging: geometry slice, shortfall, fades, gain ramp/clamp, peak scale, envelope pass/check, RMS fail.
- Layout: Glue suppresses gap/crossfade, shifts starts, adds duration; disabled/stale Glue does not.
- Route: auth, ownership, not enabled, no next, stale snapshot (no spend), invalid WAV, composition cache hit (no upstream/no quota), logical excerpt cache hit with *different* bytes (no upload), user scoping (another user's composition/excerpt is never visible), quota 429, upstream mapping, success writes R2/DB/snapshot.
- Resolution/readiness: editor payload, render metrics `GLUE_NOT_READY`, snapshot JSON, invalidation on arrangement save/PATCH/reorder.
- Audio engine: schedule/offset/seeking into a Glue, gap on fetch failure, teardown.
- Worker: mix insertion (no fade-out on A, correct `adelay`, total, shift), pipeline snapshot/download failure, frame pre-roll (midpoint title/no lyrics), chapters unaffected.
- `pnpm test`, `pnpm lint`, `pnpm build`; worker `pytest tests/`.

Manual:
- Apply the migration to the dev DB; set `SOW_ELEVENLABS_API_KEY`.
- Test user (browser recipe in root AGENTS.md): 2-song set, open the first song, toggle, Save, Generate with **one live paid call** (requires explicit go-ahead; ~405 credits), then Preview (Glue band + audio), Listen, toggle off/on (ready from cache), render and check output duration/chapters/pre-roll.
- If a live paid call is not authorized, seed a POC Glue (run1 junction 1) into R2 + `glue_compositions` for the same descriptor to verify playback/render, and rely on route tests for the live path.

## 11. Risks / assumptions

- Live compose latency under 50 s is assumed; unverified. If real calls exceed it, the fallback is split routes or moving generation to the render worker (a separate ADR), not silently losing credits.
- The 4.23 MB multipart body is close to Vercel's 4.5 MB limit; a shorter side lowers it. No MP3 fallback. The route validates the declared lengths.
- ElevenLabs requires a paid workspace with inpainting access; the POC's 12 s recipe passed copyright screening.
- Per-user caches mean two users generating the same junction each pay for their own uploads/composition. Accepted to close the cross-user trust boundary; intra-user reuse is unaffected.
- The server cannot verify excerpt bytes; a user can seed arbitrary audio for their own logical excerpt. Only that user's renders are affected. Recorded in the ADR.
- Editing an arrangement while a render is queued can make the snapshot's Glue mismatch the new audio; same class as the existing render-time audio fetch race, accepted for v1.
- No re-roll: a musically poor Glue can only be replaced by changing beats/arrangement (new descriptor, new spend). Accepted.
- `updated_at` is the optimistic snapshot; glue-setting PATCHes therefore invalidate a pending Generate by the same user in another tab (safe, no spend).

## 12. Out of scope

- Glues between non-adjacent songs, per-transition (rather than song-level) controls, re-rolls, crossfade + Glue combinations, Android/iOS UI, offline audition of Glues, chapter entries covering the Glue, pruning old R2 compositions, and canonical server-side excerpt construction (the recorded alternative if cross-user reuse/verification is required).
