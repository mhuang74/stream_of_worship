# ElevenLabs Glue in Stream of Worship — v2 implementation specification

**Status:** proposed  
**Supersedes:** `specs/inpaint-transition-elevenlabs-v1.md` for all future implementation. v1 is a historical document and must remain unchanged. In particular, v2 replaces its synchronous 60-second multipart route, browser-built audio, and no-reroll policy; it preserves the useful canonical arrangement, staging, layout, cache-identity, and audio/video timing work described below.

This is an execution specification, not a proposal to call ElevenLabs during implementation or verification. Implement top-to-bottom. Do not make a paid provider call without the separate explicit authorization required by §7.3.

## 1. Product decisions and end-to-end journey

### 1.1 Settled decisions

| Decision | v2 behavior |
|---|---|
| Execution | Durable background jobs on the existing Python render-worker image, dispatched to a dedicated SQS queue and Lambda deployment. The browser never waits for provider generation. |
| Generation cost | Every generation action is explicit and confirmed. The confirmation shows the exact estimated **new** upload credits plus generation credits, remaining paid jobs, and reset time. |
| Additional takes | **Try another** is explicit and paid. It creates a new take with a new id, ordinal, and seed; prior takes and their audio remain available. |
| Existing audio | An existing ready take for the current descriptor can be selected or restored free. Enabling Glue with such a take auditions it immediately, even if the user also starts a new paid take. |
| Cache scope | Excerpts and takes remain per-user. A descriptor/take cache hit costs no credits and consumes no paid quota. |
| Inputs | Only saved Song Arrangements are generation inputs. The prompt is fixed English instrumental-only text and styles for every catalog language; real Traditional Chinese songs are acceptance fixtures. |
| Missing enabled Glue | Render preflight blocks it. It never silently substitutes an ordinary gap/crossfade. Each affected junction offers Retry, Open editor, or a confirmed Disable action. |
| Preview and Listen | They audition only already available audio and never initiate paid work. Initial failure leaves the ordinary transition usable; failed Try another never replaces the selected ready take. |

The four load-bearing product choices are therefore **durable jobs**, **retained takes with explicit paid Try another**, **English-only instrumental prompts plus real Chinese-catalog QA**, and **blocking render preflight with explicit remedies**.

### 1.2 Domain language

Use existing domain terms in code and documentation:

- **Songset Item** — a Song placed in a Songset.
- **Junction** — the boundary owned by an outgoing Songset Item and its next visible incoming Item.
- **Glue** — generated audio replacing that Junction's ordinary gap/crossfade; user-facing English is **AI transition**.
- **Song Arrangement** — the saved canonical Arrangement Steps for a Recording.
- **Audition** — local Arrangement Editor Preview or whole-Songset Listen playback.
- **Render** — creation of MP3, MP4, and chapter artifacts from one immutable resolved snapshot.

Internal terms such as Glue, descriptor, take, and junction need not appear in user-facing copy. Follow `docs/translation-guidelines.md` for zh-Hant: use 「敬拜歌單」, Taiwan terminology, concise natural copy, full-width punctuation, and actionable errors. Song titles, Lyrics, themes, and user text are never translated or rewritten by this feature.

### 1.3 Required user journey

1. In the Arrangement Editor Preview rail, the user enables **AI transition** on outgoing Item A.
2. The user chooses 2, 4, 8, or 16 beats of incoming Song B's tempo. The default is 4. The UI calculates and displays `max(3, beats × 60 / toTempoBpm)` seconds, or the precise reason duration is unavailable. The 3-second provider floor affects generated length; the staged take may be shorter as specified in §2.2.
3. Before confirmation, the UI shows exact estimated new credits: `15 × (uncached A excerpt seconds + uncached B excerpt seconds + requested generation seconds)`. It identifies cached free sides and shows paid jobs remaining and the rolling-window reset time.
4. The user confirms one paid job. The response is `202` with a job id; navigation, refresh, and tab closure do not cancel it. There is no hidden batch generation.
5. While the job runs, the user may navigate freely. Editor and render pages rediscover active jobs on load and show the persisted phase and elapsed time, never fabricated percent completion. Playback continues with the selected ready take, if any, otherwise the ordinary transition.
6. When ready, the selected take is auditioned in Arrangement Editor Preview and Listen. The UI shows actual staged duration and measured QA status. No automatic musical-quality judgment is claimed.
7. The user may explicitly confirm **Try another**, with new-cost and quota information, or select an earlier ready take of the same current descriptor for free. A failed new take never changes the current selection.
8. Render preflight resolves every enabled Junction. An enabled-but-unavailable Glue blocks render and lists **A → B**, localized reason, current job status, Retry marked free or paid, **Open editor**, and the confirmed action **Disable AI transitions and use ordinary transitions**.
9. With all enabled Glues ready, the user renders MP3, MP4, and chapters from the same selected takes resolved by preflight. No later take selection can mutate an in-progress render snapshot.

### 1.4 Junction states and allowed actions

The resolved UI state is derived from `glue_enabled`, saved-input validity, descriptor match, selected take, active/latest job, and effective readiness. Persist the job and take facts described in §2; do not persist a second contradictory junction-state enum.

| State | Meaning | Playback and generation behavior |
|---|---|---|
| `disabled` | Glue is off. | Ordinary gap/crossfade. Enabling resolves an older matching ready take immediately. |
| `needs-generation` | Enabled, saved and valid, but no effective ready take. | Ordinary transition in Preview/Listen; Generate is available when no other blocker applies. Render is blocked. |
| `queued` | Durable job committed and awaiting dispatch/claim. | Current ready take or ordinary transition. Show queue elapsed time. |
| `preparing` | Worker validates persisted inputs, builds excerpts, and rechecks descriptor. | Same playback rule. No paid request has necessarily occurred. |
| `uploading` | One or more paid excerpt uploads are in progress or complete. | Paid-attempt cancellation/refund is no longer offered. |
| `composing` | Paid compose is in progress or its provider outcome is being recorded. | Same cancellation rule. Never auto-repeat an ambiguous request. |
| `storing-response` | Successful raw provider bytes are being persisted and verified. | Existing playback remains unchanged. Recovery after this point is free. |
| `staging` | Persisted response is decoded, sliced, measured, staged, and stored. | Retry processing is free after failures with a verified raw response. |
| `ready` | An enabled selected ready take matches the current descriptor. | Audition/render uses it. Try another and free earlier-take selection are available. |
| `failed` | Latest attempt has a safe terminal failure and no newer active job. | Initial failure uses ordinary transition; failed Try another retains current ready take. Retry action states free vs paid. |
| `stale` | Inputs changed so the selected/enabled take no longer matches the derived descriptor. | Ordinary transition for that Junction; older takes remain cached and restorable if inputs revert. |

An unsaved draft affecting either side makes the adjacent Glue **stale for Audition** and disables generation until save, even if the persisted descriptor still matches. Play remains available with the ordinary draft transition. A draft on A affects A's outgoing Glue and its predecessor's incoming Glue; the same adjacency rule applies to B.

State-transition invariants:

- `queued → preparing → uploading → composing → storing-response → staging → ready` is the successful new-take path. Cached excerpts skip only their own upload requests, not validation. Free staging recovery enters at `staging` after verifying the persisted raw response.
- `takeMode: "initial"` with an already selected current ready take returns `ready` without a job. With another existing current ready take, it selects the newest such take and returns that free resolution without provider work.
- Any nonterminal job can become `failed` with a stable code and retry classification. `ready` and `failed` are terminal for the job; per-Junction `stale` is subsequently derived when inputs change.
- A stale or cancelled job discovered before the first provider request finishes without spend. A cancellation after the first provider request is recorded as cancellation requested, with the explicit paid/no-refund outcome; the worker checks it between stages and preserves any returned response before stopping.
- A new job never deletes takes or cache objects. Only successful staging selects the new take, transactionally.

## 2. Domain, schema, and canonical audio

### 2.1 Storage model

Add the following in `delivery/webapp/src/db/schema.ts`, a generated Drizzle migration, and the worker's DB models in the same change. Use the repository's existing id/timestamp types and conventions. All descriptor JSON is stored as canonical text as well as by hash; JSON below is illustrative, not permission for database-specific syntax that differs from existing migrations.

```text
glue_takes
  id                    uuid PK, immutable
  user_id               user FK, NOT NULL
  descriptor_key        char(64), NOT NULL
  descriptor_json       text, NOT NULL
  ordinal               integer, NOT NULL, >= 1
  seed                  bigint, NOT NULL
  status                text, NOT NULL: ready | failed
  r2_key                text, NULL until ready, immutable once set
  seconds               numeric, NULL until ready, > 0 once ready
  raw_response_r2_key   text, NULL
  raw_response_sha256   char(64), NULL
  raw_response_bytes    bigint, NULL
  metrics_json          text, NOT NULL default '{}'
  reasons_json          text, NOT NULL default '[]'
  error_code            text, NULL
  created_at            timestamptz, NOT NULL default now()
  UNIQUE (user_id, descriptor_key, ordinal)
  index (user_id, descriptor_key, status, created_at DESC)

glue_jobs
  id                    uuid PK
  user_id               user FK, NOT NULL
  songset_id            songset FK, NOT NULL
  from_item_id          songset_item FK, NOT NULL
  to_item_id            songset_item FK, NOT NULL
  client_request_id     uuid, NOT NULL
  take_mode             text, NOT NULL: initial | new-take
  descriptor_key        char(64), NOT NULL
  descriptor_json       text, NOT NULL
  expected_from_updated_at / expected_to_updated_at
                        timestamptz, NOT NULL
  phase                 text, NOT NULL: queued | preparing | uploading |
                        composing | storing-response | staging | ready |
                        failed | cancelled | dispatch-failed
  active                boolean, NOT NULL, maintained with phase
  dispatch_state        text, NOT NULL: pending | dispatched | failed | local
  dispatch_attempt      integer, NOT NULL default 0
  attempt_id            uuid, NOT NULL, unique per accepted dispatch attempt
  pending_take_id       uuid, NULL until allocation, then immutable and UNIQUE
  take_ordinal          integer, NULL until allocation, then immutable, >= 1
  seed                  bigint, NULL until allocation, then immutable
  paid_reservation_id   uuid, NULL
  paid_reservation_state text, NULL: reserved | consumed | released
  provider_contacted    boolean, NOT NULL default false
  provider_phase        text, NULL: upload-a | upload-b | compose
  provider_status       integer, NULL
  retry_kind            text, NULL: free | paid | none
  resulting_take_id     glue_take FK, NULL
  active_take_id        glue_take FK, NULL
  raw_response_r2_key / raw_response_sha256 / raw_response_bytes
                        immutable once a successful compose response is stored
  error_code            stable machine code, NULL
  error_detail          sanitized safe detail, NULL
  heartbeat_at          timestamptz, NULL
  created_at / started_at / updated_at / finished_at
                        timestamptz as applicable
  UNIQUE (user_id, client_request_id)
  partial UNIQUE (user_id) WHERE active
  indexes (user_id, active, created_at DESC), (phase, heartbeat_at),
          (songset_id, from_item_id, created_at DESC)

glue_paid_attempts
  id                    uuid PK
  user_id               user FK, NOT NULL
  glue_job_id           glue_job FK, NOT NULL UNIQUE
  state                 text, NOT NULL: reserved | consumed | released
  consumed_at / released_at / created_at
                        timestamptz as applicable
  index (user_id, created_at DESC)

glue_excerpts
  user_id               user FK, NOT NULL
  logical_key           char(64), NOT NULL
  version               integer, NOT NULL
  side                  text, NOT NULL: A | B
  seconds               numeric, NOT NULL, >= 1 and <= 12
  content_hash          text, NOT NULL
  canonical_steps_json  text, NULL
  audio_sha256          char(64), NULL until constructed/uploaded
  elevenlabs_song_id    text, NULL
  phase                 text, NOT NULL: constructed | uploaded | rejected | failed
  rejection_code        text, NULL
  safe_failure_detail   text, NULL
  created_at / updated_at
                        timestamptz, NOT NULL
  PK (user_id, logical_key)

songset_items (add/retain)
  glue_enabled          boolean/integer per existing convention, NOT NULL default false
  glue_beats            numeric/integer per existing convention, NOT NULL default 4
                        CHECK value in (2, 4, 8, 16)
  glue_active_take_id   uuid NULL REFERENCES glue_takes(id)

render_jobs (add)
  glue_transitions      text/json per existing convention, NULL
```

Schema and lifecycle invariants:

- The partial unique active-job index, not a read-then-write check, enforces one active Glue job per user. `active` is true only for queued/preparing/uploading/composing/storing-response/staging. `ready`, `failed`, `cancelled`, and `dispatch-failed` set it false in the same transaction as the terminal phase.
- Job creation atomically allocates and persists `pending_take_id`, `take_ordinal`, and `seed` with the paid reservation. Once allocated, all three are immutable. Every worker attempt and restart uses those exact stored values; it must never generate a replacement id, ordinal, or seed. On successful staging, `resulting_take_id` equals `pending_take_id`, and the ready `glue_takes` row copies the persisted ordinal and seed. No `pending` take status is needed because the reservation lives on `glue_jobs` until the take row is inserted.
- A paid reservation row and job creation are atomic. A consumed row remains the immutable quota ledger even if the provider fails. A released row has no `consumed_at` and does not count. `provider_contacted` becomes true immediately before the first upload or compose request can be sent; consumption is recorded in the same transition, before network I/O.
- A take is effective only when Glue is enabled, the selected take is `ready`, its `user_id` is the Songset owner, and its stored descriptor text/key exactly equals the currently derived descriptor. `glue_active_take_id` alone never proves readiness.
- Do not add v1's denormalized `glue_key` or `glue_seconds`. Every duration, list, editor, Listen, and render reader resolves the selected take. Descriptor changes make a take stale without deleting it; reverting inputs can make it effective again. There is no broad “clear all Glues” rule.
- Adding, deleting, reordering, changing Recording/order, or saving an Arrangement only changes which descriptors resolve. Never destructively delete cached excerpts or takes. Duplication copies `glue_enabled`, `glue_beats`, and `glue_active_take_id` only for the same user; the copied take is still re-resolved against the copied Junction's descriptor.
- `glue_excerpts` caches by logical identity, never by decoded byte hash. `audio_sha256` is audit/validation metadata. A copyright rejection is a successful negative cache entry; an infrastructure failure is not.
- Raw-response and staged R2 keys are immutable and include unique job/take identity. DB updates never overwrite an existing object's content. Storage lifecycle/pruning is out of scope.
- Job error fields contain only allowlisted codes and sanitized detail. Full stacks and restricted provider diagnostics stay in server logs.

### 2.2 Canonical descriptor, excerpts, and takes

#### Implementation boundary and dependencies

Port the behavioral algorithms from `lab/poc-scripts/gen_elevenlabs_glue.py` into production worker modules; **never import the POC**. The POC is the measured reference for source-R2 download, canonical Arrangement Step playback order, FFmpeg decode, 44.1 kHz stereo PCM16 WAV construction, plan geometry, envelope QA, gain staging, peak limiting, fades, and paid-call behavior. Production code owns its tests and safe error taxonomy.

Use `uv add` in `delivery/render-worker/pyproject.toml` for:

- `numpy>=1.26,<2`
- `soundfile>=0.12,<1`

Mirror the exact resolved versions in the Lambda `requirements.txt`. Choose versions with CPython 3.11 binary wheels accepted by the documented `public.ecr.aws/lambda/python:3.11` glibc 2.26 base. Keep `--only-binary=:all:` and prove the Docker image build; never accept a compiler-only source build. Do not weaken the existing Docker schema2/AMD64 deployment requirements.

#### Canonical descriptor

Add a server-only TypeScript descriptor builder and a byte-compatible Python mirror. Reuse `src/lib/arrangements.ts` and its Python mirror for parsing/validation; do not reparse arrangements in UI components or invent worker-specific Step ordering.

The exact logical payload is:

```json
{
  "v": 2,
  "model": "music_v2_5",
  "promptVersion": 2,
  "excerptSeconds": 12,
  "beats": 4,
  "toTempoBpm": 86.13281,
  "from": {"contentHash": "sha256-a", "steps": [[0.0, 12.5]]},
  "to": {"contentHash": "sha256-b", "steps": null}
}
```

Rules:

- Explicit field order exactly as above. `descriptor_key = sha256(UTF8(JSON.stringify(payload)))`; the Python mirror must produce the same compact JSON and key. No whitespace, item ids, Songset ids, titles, Lyrics, localized strings, or take id enter the descriptor.
- `beats` is one of 2/4/8/16. `toTempoBpm` and all numeric Step bounds are rounded to exactly 6 decimal places using the repository's canonical half-up rule (`Math.round(x*1e6)/1e6`; Python `math.floor(x*1e6+0.5)/1e6`). Tempo canonicalization is identical in both languages. Do not use Python's half-even `round`.
- `steps` is the canonical array of `[startSeconds,endSeconds]` pairs in stored playback order; `sectionIndex`, labels, and `manualBounds` are excluded. `null` means the untouched full Recording, not `[[0,duration]]`.
- `from` and `to` contain the actual Recording content hashes. Recording replacement, arrangement edits, beat changes, and tempo re-analysis therefore produce a different descriptor. Reordering changes which sides form a Junction.
- `promptVersion:2` and `v:2` are mandatory because lifecycle and takes differ from v1. Store both canonical JSON and hash on jobs/takes. Tests pin cross-language exact strings, field sensitivity, ordering, and 6-dp rounding.

#### Worker-built canonical excerpts

The worker, not the browser, downloads source Recordings from R2 and builds both excerpts after claiming the job. There is no multipart browser audio, Vercel 4.5 MB exposure, browser decode dependency, or client-supplied WAV validation path.

For each side:

1. Download the selected Recording through the worker's atomic R2 asset-fetch path. Validate download integrity and decode through FFmpeg to contiguous float32 44.1 kHz stereo PCM16. Mono is duplicated; decode/resampling behavior is worker-controlled.
2. Parse and validate the saved Song Arrangement against current analysis exactly as the render path does. Concatenate every canonical Arrangement Step in stored playback order. `steps:null` uses the untouched Recording.
3. A outgoing tail: walk canonical Steps backwards, taking `[step.end-need,step.end]` until the excerpt budget is filled, then restore forward playback order. B incoming head: walk forwards taking `[step.start,step.start+need]`. This is equivalent to slicing the arranged tail/head and avoids treating Step metadata as audio order.
4. Target exactly 12 s per side. A short arranged side uses all available audio. Reject either side under 1 s before any paid request. Store actual excerpt seconds, not an assumed 12.
5. Convert with symmetric PCM16 rounding/clamping and write a canonical 44-byte RIFF PCM16 little-endian WAV: 2 channels, 44100 Hz, 16-bit samples. Compute SHA-256 for audit/integrity. Use bounded memory and streaming/file-backed FFmpeg operations where the full Recording would otherwise be unnecessarily duplicated.

The logical excerpt identity is:

```text
sha256(canonical_json({
  v: 2,
  side: "A" | "B",
  seconds: <actual arranged excerpt seconds, canonical 6-dp>,
  contentHash: <Recording content hash>,
  steps: <canonical steps or null>
}))
```

Cache key is `(user_id,logical_key)`. Cache successful `elevenlabs_song_id` and copyright rejection under this key. Never key an upload by decoded bytes. The worker-built bytes are trustworthy, but per-user cache scope remains unchanged in v2.

#### Provider plan and fixed prompt

For uncached sides, upload the canonical WAV to `POST https://api.elevenlabs.io/v1/music/upload`. Do not add the POC's MP3 retry as an automatic paid retry. On 413/415, fail with an actionable provider error; any later format fallback requires a new specification that addresses charged ambiguity.

Compose once per accepted paid take via `POST /v1/music?output_format=pcm_44100`:

```json
{
  "model_id": "music_v2_5",
  "seed": 123456789,
  "composition_plan": {
    "chunks": [
      {"song_id": "A_ID", "range": {"start_ms": 0, "end_ms": 12000}},
      {
        "text": "[Glue]\n{instrumental only — no vocals, no words; seamless musical transition from the outgoing verse into the incoming chorus: warm piano, ambient pads, soft cymbal swell, natural build, no hard cut}",
        "duration_ms": 3000,
        "positive_styles": ["contemporary worship", "warm piano", "ambient pads", "soft build", "seamless transition", "great production quality"],
        "negative_styles": ["vocals", "singing", "vocal", "choir", "spoken word", "lyrics", "abrupt ending", "silence"],
        "context_adherence": "high",
        "conditioning_ref": {"song_id": "B_ID", "range": {"start_ms": 0, "end_ms": 8000}},
        "condition_strength": "high"
      },
      {"song_id": "B_ID", "range": {"start_ms": 0, "end_ms": 12000}}
    ]
  }
}
```

Use actual `aRefMs`/`bRefMs` for short sides; `conditioning_ref.end_ms = min(8000,bRefMs)`. `duration_ms = round(max(3,beats*60/toTempoBpm)*1000)`. Reject durations over 120000 ms before spend. Each new take has a unique id/ordinal and a newly generated seed. Because ElevenLabs is not reproducible even with a seed, compositions are content-addressed by user + descriptor **plus unique take identity**, never deduplicated across takes.

The request contains only model/seed, song ids/ranges, numeric timing, and the fixed English prompt/styles above. Never send Song titles, Lyrics, worship themes, locale, transliteration, or any Chinese text. There is one audio path for Chinese and English Recordings: no language detection and no Chinese prompt variant.

#### Raw response, slicing, QA, and staging

Immediately on compose success, stream the response to immutable `glues/<user_id>/jobs/<job_id>/response.pcm`, enforcing the 32 MiB cap. Record SHA-256 and exact byte count in `glue_jobs` before attempting decode or staging. This persisted response is the only authorization for automatic free recovery. Do not hold the only copy in Lambda memory.

Port the POC's measured behavior:

1. Decode the raw response as 44.1 kHz stereo PCM16, handling RIFF or the `pcm_44100` raw layout without assuming WAV headers. Validate actual geometry against A reference + generated chunk + B reference and allow the POC's 0.1 s response-length tolerance.
2. Locate the generated window by plan geometry: `[aRefSamples,min(total,aRefSamples+generationSamples)]`. Do not require sample-identical returned references; POC measurements showed re-rendering.
3. Measure 50 ms RMS-block Pearson envelope correlation for A's final 3 s against the returned preceding 3 s and B's first 3 s against the returned following 3 s. `>=0.90` for both is `ok`; lower values produce `check` and measured reasons while retaining the paid take. Record correlations and offsets. Correlation is QA, not proof of musical quality.
4. Take the generated window's tail for `round(requestedGlueSeconds*44100)` samples. Tolerate at most 0.1 s shortfall and retain the returned audio with a measured reason; reject greater shortfall. Reject generated audio whose RMS is below -50 dBFS. Record actual staged seconds.
5. Measure A's last 200 ms and B's first 200 ms mono RMS. Ramp per-sample gain in dB from A's exit to B's entry, clamp requested gain to ±18 dB, scale the complete take if peak exceeds 0.99, and apply 20 ms linear fades to the Glue only. Store all requested/applied gains, limiting, residuals, peaks, envelope results, shortfall, status, and reasons.
6. Write the final canonical WAV to immutable `glues/<user_id>/<descriptor_key>/take-<take_id>.wav`. Verify R2 size/checksum before DB selection.

In one transaction, insert/update the immutable ready take metadata, set `songset_items.glue_active_take_id` to it only if the Junction descriptor still matches the job snapshot and no cancellation/invalidation forbids selection, bump item and Songset `updated_at`, attach the resulting take to the job, and mark the job ready/terminal. If inputs changed after provider contact, preserve the completed take and raw response but do not select it into the changed Junction; report stale completion rather than pretending it is current.

Raw and staged R2 objects are immutable. Selecting another take cannot alter an in-progress render's bytes. Initial generation failure leaves ordinary playback usable; a failed Try another leaves the current selected ready take untouched.

### 2.3 JSON API contracts

All new browser routes require the owning authenticated session, accept/return JSON only, and return stable `code`, localized actionable `message`, `jobId` where available, and optional safe `providerStatus`/`detail`. Never expose stack traces, API keys, signed URLs, raw Lyrics, or unrestricted provider bodies.

#### `POST /api/songsets/[id]/glue-jobs`

Request:

```json
{
  "fromItemId": "item-a",
  "clientRequestId": "4c4f0a37-84a0-4f67-a6bb-e252bc1f85e1",
  "expectedFromUpdatedAt": "2026-11-22T10:00:00.000Z",
  "expectedToUpdatedAt": "2026-11-22T10:00:00.000Z",
  "takeMode": "initial"
}
```

`clientRequestId` is a browser-generated UUID, unique per user for one intended Generate/Try another action. Retrying the same HTTP request reuses it; a new paid intent uses a new UUID. `takeMode` is exactly `"initial"` or `"new-take"`.

The route performs this sequence:

1. Authenticate and verify Songset ownership. Look up `(user_id, clientRequestId)` before any free resolution; an existing record returns its original job/status without another reservation, selection, or dispatch. For a new id, verify outgoing Item ownership, that the next visible Item is the recorded successor, Glue is enabled, and both arrangements are saved and valid against current analysis. Validate incoming tempo, beat choice, both arranged sides, and the 120-second generation cap before spend.
2. Compare both persisted `updated_at` values to `expectedFromUpdatedAt` and `expectedToUpdatedAt`. A mismatch is `409 GLUE_STALE_SNAPSHOT`, before a reservation or provider call.
3. Compute the v2 descriptor from server-side saved data. Refuse client-supplied descriptors, WAVs, `song_id`s, seeds, costs, or cache keys.
4. Resolve the current take. For `initial`, if it is already selected and effective, return `200 {"status":"ready","takeId":"…","cached":true}` with no job. Otherwise atomically select the newest ready take for this user/current descriptor, bump item and Songset timestamps, and return the same free ready response. This path consumes no quota.
5. For `new-take`, always prepare a new paid take even if another take is ready. For `initial` with no existing current ready take, prepare the initial job. Check the missing server API key and other configuration now, before reservation.
6. In one serializable transaction: re-read the item snapshot and descriptor; atomically claim `(user_id, clientRequestId)` or return the original job if a racing request inserted it; lock the user's quota row, count consumed attempts in the rolling 24 hours, and reject at 5; insert the job and reservation, allocate and persist the new `pending_take_id`, next `take_ordinal`, and cryptographically random `seed`, and rely on the partial active index to reject concurrent jobs. If another active job exists, return `409 GLUE_ACTIVE_JOB` identifying its Junction and job id rather than queueing surprise spend.
7. Commit, then dispatch `{kind:"glue.generate",jobId,songsetId,userId}` to the dedicated Glue queue. Return `202 {"jobId":"…","status":"queued"}`.
8. If SQS dispatch fails after commit, transactionally mark `dispatch-failed`, set active false, release the paid reservation because no provider call occurred, increment the dispatch attempt/log the exception, and return retryable `503 GLUE_DISPATCH_FAILED`. Repeating the same `clientRequestId` creates a fresh dispatch record/attempt id for that original job without charge; it must not create another paid reservation or bypass the active-job index. A job already claimed by a worker is never reset by a late HTTP retry.

Validation errors include `GLUE_NOT_ENABLED`, `GLUE_NO_SUCCESSOR`, `GLUE_UNSAVED_DRAFT` where detectable, `GLUE_TEMPO_UNAVAILABLE`, `GLUE_ANALYSIS_UNAVAILABLE`, `GLUE_ARRANGEMENT_INVALID`, `GLUE_EXCERPT_TOO_SHORT`, `GLUE_DURATION_TOO_LONG`, `GLUE_DAILY_LIMIT`, and `GLUE_NOT_CONFIGURED`. No validation failure consumes quota.

#### `PATCH /api/songsets/[id]/items/[itemId]`

Extend the existing route with optional `{ "glueActiveTakeId": "take-uuid" }`, alongside existing Glue enable/beat fields. Selection is free and never creates a job. Accept only a ready take owned by the session user whose stored descriptor exactly matches the saved Junction descriptor. Reject cross-user, stale-descriptor, non-ready, and outgoing/incoming mismatches. Return the resolved editor payload and updated timestamps. Changing beats does not delete takes; it changes which descriptor can be selected.

#### Job discovery and status

Add an authenticated JSON discovery endpoint, `GET /api/songsets/[id]/glue-jobs?active=1`, returning the owner's active and latest relevant jobs keyed by outgoing Item. Editor and render pages call it on initial load, reconnect, and visibility resume. `GET /api/glue-jobs/[id]` returns the same safe status snapshot for direct recovery. Responses include phase, `createdAt`, `startedAt`, elapsed inputs, resulting/selected take ids, safe error/retry kind, quota state, and Junction ids; no percent field.

#### `GET /api/glue-jobs/[id]/events`

Mirror the existing render-job SSE route's authorization and polling pattern: query PostgreSQL every 1 second, emit the persisted job snapshot, close on terminal phase, and support reconnect/resume by job id. SSE is a view of the DB, never the job's source of truth. Navigation or a closed stream does not cancel work.

#### Cancellation

`POST /api/glue-jobs/[id]/cancel` is accepted only before `provider_contacted`. The route records cancellation atomically; the worker rechecks before every stage and before each provider request. After contact, return `409 GLUE_PAID_NOT_CANCELLABLE`; the UI states that the paid request cannot be cancelled or refunded. Worker checks between stages prevent later spend where possible, preserve returned bytes, and never report a refund.

### 2.4 Dispatch and durable worker execution

Generalize `src/lib/render/dispatcher.ts` and `src/lib/sqs/client.ts` without conflating queues or job stores:

- Production render messages remain on `SOW_SQS_QUEUE_URL`. Their existing `{jobId,songsetId,userId}` shape, with omitted `kind`, is interpreted as `render` for backward compatibility.
- Glue messages use `SOW_GLUE_SQS_QUEUE_URL` and `{kind:"glue.generate",jobId,songsetId,userId}`. Add a separate SQS client/configuration path; do not switch queue URLs based on an untrusted message.
- Deploy the same render-worker container image as a separate Glue Lambda and event-source mapping. Use batch size 1, reserved/maximum concurrency 2, Lambda timeout 1900 seconds, SQS visibility timeout 2100 seconds, and `maxReceiveCount` 3 with a DLQ. Visibility exceeds Lambda timeout. Render concurrency is not throttled by Glue work.
- Local REST mode commits the same durable job and returns `202` immediately. Dispatch to the local worker asynchronously after commit; do not reuse the existing blocking REST invocation as a browser wait. A local dispatch failure follows the same `dispatch-failed` and free redispatch path. REST concurrency is development-only and does not weaken DB idempotency.
- `lambda_handler.py` routes omitted/`render` kind to `execute_render_pipeline` and `glue.generate` to new `execute_glue_job`. Verify message `userId`/`songsetId` against the claimed DB job. Batch size remains 1; acknowledgement behavior must not convert an ambiguous paid phase into another provider call.

`execute_glue_job` atomically claims a queued/dispatchable job and attempt id. A duplicate delivery or restart reads phase, attempt, immutable `pending_take_id`/`take_ordinal`/`seed`, provider-contact, upload, and raw-response state before doing anything. Missing reserved identity fields on a paid job are a terminal invariant/configuration failure before provider contact, not permission to invent replacements:

1. Re-fetch the current Songset Items, ownership/order, saved arrangements, Recording hashes/tempo, and item timestamps. Recompute the descriptor and compare it plus current selection rules to the persisted snapshot before the first paid call. Stale/cancelled jobs finish without spend and do not change the selected take.
2. Resume free excerpt construction, cached upload lookup, raw-response verification, and staging from durable state.
3. Before any provider request, atomically record provider contact and consume the reservation. Before each subsequent provider request, recheck cancellation and persisted phase.
4. Persist every successful upload `song_id` before continuing. Never infer success from a log line or exception text.
5. On compose success, store the immutable raw response in R2 and record checksum/byte count before decode/staging. A later crash resumes only from verified raw bytes.
6. Heartbeat after every stage and blocking operation boundary. Terminal jobs leave the active index. Queued jobs with no dispatch/heartbeat remain recoverable by id; operators re-dispatch them without treating them as spent. Do not mark an abandoned queued job as a consumed provider attempt.

**Ambiguous redelivery rule:** if an SQS redelivery/restart finds an in-flight upload or compose phase whose request may have been sent and no complete durable success record exists, mark the job `failed` with unknown provider outcome. Do not call ElevenLabs again. Only an explicit user-confirmed retry creates a fresh paid attempt. Automatic recovery is permitted only for pre-contact work, persisted successful `song_id`s, or a fully persisted/checksummed raw response. A staged-response recovery never composes again.

### 2.5 Concrete limits and budgets

| Operation | Required limit |
|---|---|
| Free infrastructure retries | Initial operation plus at most 3 free retries; full-jitter delays of 1 s, 2 s, then 4 s. Log every retry. |
| R2 connect/read timeout | 30 s connect and 300 s read, separately configured. |
| ElevenLabs connect timeout | 30 s for upload and compose. |
| ElevenLabs upload read timeout | 900 s. |
| ElevenLabs compose read timeout | 1800 s, matching the measured POC budget. |
| Compose response | Streaming cap 32 MiB; abort before overflow and fail safely. This covers 24 s references plus the 120 s maximum generated chunk in 44.1 kHz stereo PCM16. |
| Generation length | Reject before spend if `max(3, beats × 60 / incomingTempo)` exceeds 120 s; ask for fewer beats. |
| Glue Lambda / SQS visibility | 1900 s / 2100 s. |
| Quota | 5 consumed paid jobs per user per rolling 24 h, transactionally shared by concurrent submissions. |
| User concurrency | One active Glue job per user via partial unique index; provider concurrency capped at 2 by the dedicated Lambda. |

If deployment inspection proves reserved concurrency cannot be configured separately, keep the dedicated queue and enforce a provider concurrency limit of 2 in the Glue dispatcher/worker; never solve this by reducing render throughput. v2 assumes no ElevenLabs server idempotency key. If one becomes available, a later specification must define its persistence and reuse before implementation relies on it.

## 3. Quota, caching, retries, and paid recovery

### 3.1 What consumes money or quota

A job consumes one of the 5 rolling-24-hour paid slots when it reaches **any** paid upload or compose, whether that operation later succeeds, fails, is rejected for copyright, or has unknown outcome. The reservation becomes consumed immediately before the first request. A subsequent second upload or compose in the same job does not consume another user quota slot, although provider credit estimates must include every new upload and generation second.

These actions are free and consume no paid slot:

- Validation failures and stale snapshots before provider contact.
- Missing API key/configuration detected before reservation.
- SQS/local dispatch failure before provider contact; its reservation is released.
- Selection/restoration of any ready take for the current descriptor.
- `takeMode:"initial"` resolution of the newest existing current ready take.
- Cached excerpt `song_id` reuse and cached copyright rejection.
- Staging-only recovery from a verified persisted raw response.
- Preview, Listen, render preflight, SSE polling, and job discovery.

A cache hit for the selected descriptor/take is free. Try another is always a new paid take and cannot be satisfied by deduplicating its compose response against an earlier take.

Before initial generation and every Try another, return/show:

- `uploadCredits = 15 × sum(actual seconds of only uncached sides)`;
- `generationCredits = 15 × max(3, beats × 60 / incomingTempo)`;
- `estimatedNewCredits = uploadCredits + generationCredits`;
- consumed/remaining paid-job count and the timestamp at which the oldest relevant rolling-window consumption expires.

The credit figure is an exact estimate from current cache state and POC's measured 15 credits/second rate, not a claim about final provider billing. Recheck quota in the same serializable transaction as job/reservation creation so concurrent submissions share one limit. Quota errors show the reset time and do not create active work.

### 3.2 Retry and recovery matrix

| Failure or action | Automatic retry? | User retry and charge |
|---|---|---|
| Transient PostgreSQL failure | Up to 3 attempts with specified full jitter; use transactions/atomic claims. | Free if no paid request was sent; otherwise resume only durable safe stages. |
| SQS dispatch failure | Bounded infrastructure retry in dispatcher; then `dispatch-failed`, release reservation. | Same `clientRequestId` redispatch is free and retryable; no new reservation. |
| R2 source download, excerpt upload-to-cache, raw/staged R2 object upload | Up to 3 attempts with specified full jitter and integrity validation. | Free; never repeat provider compose to repair storage. |
| Local FFmpeg/audio decode or local staging fault, where safe | Up to 3 bounded attempts; log each. | Free if no ambiguous paid request is repeated. |
| Cached excerpt | No upload. | Free; reuse persisted `song_id`. |
| Cached copyright rejection | No upload. | Non-retryable in place; identical audio cannot incur another half-fee upload. Changing actual excerpt identity is a new validation decision, not a bypass of user quota. |
| Provider entitlement/configuration or 401/402/403 | Never in place. | Non-retryable until operator/account correction; subsequent user action clearly requires confirmation if paid. |
| Provider invalid plan/422 | Never. | Non-retryable in place; fix plan/configuration. |
| Provider 429, 5xx, timeout, connection reset, or any ambiguity after request may have been sent | **Never automatically repeat upload or compose.** Fail with `retryKind:"paid"`. | Explicit confirmation that a new attempt may charge; creates a fresh paid attempt and consumes quota. |
| Compose succeeded; raw response stored/checksummed; decode/QA/staging/R2 metadata/DB finalization failed | Automatic recovery may resume only at safe staging steps. | **Retry processing — free**, reusing raw response. Never compose again. |
| Redelivery after in-flight provider phase without durable success | No provider request. Mark unknown outcome. | Explicit new paid retry only; do not claim success or refund. |
| Try another | No automatic musical reroll. | Paid confirmation, new id/ordinal/seed, new quota slot; preserve all prior takes. |

Log every infrastructure retry with attempt count and delay. Permit the initial operation plus at most three free infrastructure retries, with full jitter drawn from `[0,delay]` for successive retry delays 1 s, 2 s, and 4 s. This retry policy never authorizes repeating an ambiguous ElevenLabs request.

The provider client must distinguish “definitely not sent” from “may have been sent.” Only definitely-not-sent connection setup can use the bounded infrastructure policy without paid ambiguity; once bytes may have left, apply the paid row. Do not rely on HTTP status alone to prove no charge.

### 3.3 Take behavior and failure semantics

- Try another allocates a new take id, next per-user/descriptor ordinal, and seed. Its raw and staged objects use new immutable paths. It never overwrites the currently selected take.
- On success, the new ready take is selected atomically only if its descriptor remains current. On failure, the previous ready selection remains effective and auditionable.
- The selected-take menu lists all ready takes for the current descriptor with ordinal, creation time, actual seconds, and measured `ok`/`check` status. Restoring one is a free PATCH; no provider call and no quota.
- A take from another user, descriptor, or non-ready state is never accepted. Reverting arrangements/recordings/order/beats may make an earlier take current again; no destructive invalidation is needed.
- Only one explicitly submitted job runs per user. Additional Generate/Try another actions identify the active Junction and job and offer navigation to it; they do not enqueue future paid work.
- If no raw response or persisted upload `song_id` proves provider outcome, client copy says the outcome is unknown and another attempt may be charged. Never infer success from eventual account credits, and never auto-poll ElevenLabs to turn an ambiguous request into a new generation.

## 4. Errors, logging, and observability

### 4.1 Client error policy

Every error is attached inline to the affected **A → B** Junction and includes its job id when one exists. Toasts may supplement but never replace the inline error. Render errors continue through `sanitizeRenderErrorMessage`; Glue errors use the same allowlisting/sanitization approach and stable error-code mapping. Provider status and short safe detail may be shown only after removing URLs, paths, secrets, stack lines, unrestricted bodies, and personal/source content.

Minimum stable codes and copy intent:

| Code | Client meaning and next step |
|---|---|
| `GLUE_NOT_CONFIGURED` | Operator configuration is missing; no charge. Contact support/administrator. |
| `GLUE_STALE_SNAPSHOT` / `GLUE_INPUTS_CHANGED` | Songs or arrangements changed; save/review and generate again. No automatic paid retry. |
| `GLUE_ACTIVE_JOB` | Another Junction is already generating; open that job. |
| `GLUE_DAILY_LIMIT` | Limit reached; show reset time. |
| `GLUE_DURATION_TOO_LONG` / `GLUE_EXCERPT_TOO_SHORT` | Choose fewer beats / adjust the affected Recording or arrangement. |
| `GLUE_PROVIDER_ENTITLEMENT` / `GLUE_PROVIDER_PLAN` | Provider account/plan needs attention; no blind retry. |
| `GLUE_COPYRIGHT_REJECTED` | This excerpt was rejected and cached; no repeated identical upload. |
| `GLUE_PROVIDER_UNKNOWN` | Provider outcome unknown; a new attempt may be charged. Include job id. |
| `GLUE_RETRY_PAID` | Previous paid attempt failed; explicit new attempt may charge. |
| `GLUE_RETRY_PROCESSING_FREE` | Paid response is safe; Retry processing is free. |
| `GLUE_AUDIO_UNAVAILABLE` | This take could not be fetched/decoded; Retry audio for local playback. Render remains blocked. |
| `GLUE_NOT_READY` | Render blocked; list every unresolved Junction and remedy. |
| `GLUE_WORKER_FAILED` | Worker failed safely; include job id, phase, and applicable free/paid retry. |

### 4.2 Structured logs

Every web/worker exception log includes, where applicable:

```text
event, jobId, userId, songsetId, fromItemId, toItemId,
takeId, attemptId, descriptorKey, phase, providerStatus,
cacheHit, retryCount, durationMs, errorCode
```

Use stable event names such as `glue.job.created`, `glue.dispatch.failed`, `glue.provider.upload.succeeded`, `glue.provider.ambiguous`, `glue.response.persisted`, `glue.staging.recovered`, `glue.take.selected`, and `glue.render.preflight.failed`. Worker unexpected exceptions use `logger.exception` with stack traces. Web exceptions use the existing structured server logging convention. Never log API keys, signed URLs, raw Lyrics, unrestricted provider response bodies, or full prompt-associated user content.

Metrics and alerts must cover:

- queued age and full job duration, by terminal phase;
- failures by stable error code and phase;
- paid-attempt count, including unknown outcomes;
- excerpt/descriptor cache hits and cached copyright rejections;
- raw-response persist successes and free staging recoveries;
- SQS redeliveries, dispatch failures, old jobs without heartbeat, and DLQ arrivals.

Alert on any Glue DLQ arrival, provider ambiguous outcome, growing stale queue age, missing heartbeat beyond the operation budget, and raw-response recovery spikes. Metrics never contain titles, Lyrics, URLs, or keys.

## 5. Editor, Audition, Listen, and render preflight

### 5.1 Resolved payload as the single readiness source

Create one server-side resolver that returns, per outgoing Item:

```ts
{
  fromItemId: string;
  toItemId: string | null;
  glueEnabled: boolean;
  glueBeats: 2 | 4 | 8 | 16;
  descriptorKey: string | null;
  state: "disabled" | "needs-generation" | "queued" | "preparing" |
    "uploading" | "composing" | "storing-response" | "staging" |
    "ready" | "failed" | "stale";
  selectedTake: null | {
    id: string;
    ordinal: number;
    seconds: number;
    r2Key: string;
    descriptorKey: string;
    status: "ok" | "check";
    reasons: string[];
  };
  activeJob: null | { id: string; phase: string; createdAt: string; startedAt: string | null };
  unavailableReason: null | "unsaved-draft" | "no-next" | "no-tempo" |
    "no-analysis" | "invalid-arrangement" | "quota" | "active-job";
}
```

`getSongsetEditorData`, list summaries, `getSongset`, Listen/controller payloads, `getSongsetRenderMetrics`, and render page data all use this resolver or its shared resolved-take projection. Do not independently calculate readiness in each page. `toArrangementInput` and `arrangementItemColumns` in `src/lib/db/songsets.ts` pass the selected take id/key/effective seconds. Every successful generation, take selection, enable/beat change, arrangement save, or relevant Item update returns/refreshes this payload and timestamps.

An authenticated audio route resolves `(session user, selected take id)` and streams the immutable WAV, following the existing Recording audio proxy and private immutable caching conventions. The client never supplies an arbitrary R2 key. `sharedAudioBytesCache` uses `glue:<takeId>`; take id, not descriptor alone, prevents a new take from reusing old bytes.

### 5.2 Layout and duration invariants

Extend `ArrangementItemInput`, `TimelineItemSource`, and `ArrangementTimelineItem` in `src/lib/arrangements.ts` with effective selected-take identity, seconds, and computed Glue window. Keep `layoutSongsetItems` as the only web layout definition:

```text
hasGlue(i) = incoming item i > 0 AND predecessor has effective ready Glue
if hasGlue(i):
  gapBefore(i) = 0
  crossfade(i) = 0
  start(i) = previousEnd + glueSeconds(predecessor)
  glueWindow(i) = [previousEnd, start(i)]
else:
  use existing calculateGapSeconds/crossfadeSecondsFor/start formula
total duration = final item end, including every effective Glue
```

The Glue suppresses only its own Junction's gap/crossfade. It never modifies A/B Song Arrangement audio and shifts every subsequent song start, Lyric, Section Label, and chapter time by its actual staged seconds. Disabled, stale, missing, and locally unavailable Glue use ordinary layout. `getSongsetDurationSeconds`, Songset list durations, editor strips, Listen/controller headings, render metrics, and cap/admission checks all consume this same resolved input.

### 5.3 Preview rail controls and states

In `ArrangementEditorSheet.tsx`, place controls in the Preview rail above/next to transport:

- Enable switch labelled **AI transition to {next song}**, hidden for no successor. PATCH enable state with rollback on failure.
- Beat selector 2/4/8/16, default 4, with calculated target seconds. Changing beats immediately re-resolves older matching takes and never deletes audio.
- Generate/Try another button with exact new-credit estimate and quota/reset display. Require explicit paid confirmation. While another job is active, identify its Junction instead of enqueueing.
- Job id, actual phase, elapsed time, and Cancel only before provider contact. After contact, show **This paid request can’t be cancelled or refunded**. Do not display invented percentages.
- Selected-take menu with earlier ready takes, free restore, actual **Ready · {duration}**, and plain-language measured `check` warning. `check` means a measured reference-envelope issue needs listening, not that vocals/musical fit were automatically judged.
- A short explanation that AI transition replaces only this Junction's ordinary gap/crossfade.

Disable controls with concrete adjacent copy for unsaved draft, no successor, missing tempo/analysis, invalid/stale arrangement, quota exhaustion, over-120-second request, or another active job. Initial failure leaves Play usable with ordinary transition. Errors remain inline by the Junction with job id and the correct Retry processing—free or paid-confirmation action.

The editor strip gets a distinct Glue band only for effective ready windows. Its outgoing view includes enough of B to audition arrival. An unsaved A/B draft suppresses adjacent Glue audio but not Play. While generating, an existing selected take continues to play; otherwise the ordinary transition plays.

### 5.4 Audition and Listen behavior

Extend `src/lib/audio/songset-audio.ts` and `useSongsetAudio.ts` to schedule the selected take at its computed Glue window, using `sharedAudioBytesCache` key `glue:<takeId>`, neutral gain, and no extra Glue envelope. Support initial scheduling, newly decoded scheduling, seeking into the middle of Glue with correct buffer offset, full rebuild, and teardown on stop/session replacement. During generation, never schedule an unfinished take.

If a ready Glue fetch or decode fails:

1. Log the underlying exception with take id and Junction ids.
2. Show a persistent non-blocking warning naming **A → B** with **Retry audio** in both editor and Listen. A toast alone is insufficient.
3. Mark only that Glue unavailable and rebuild the local Audition timeline with the ordinary gap/crossfade. Recompute subsequent starts, lyrics, and chapter/controller times from the rebuilt layout.
4. Do **not** insert silence into the old Glue-sized window; that would preserve incorrect timing. Other effective Glues remain active.

This local playback fallback does not relax render preflight. Retrying audio re-fetches/re-decodes the same immutable take and rebuilds again only on success. Preview and Listen never generate or retry a provider request.

### 5.5 Render preflight and immutable snapshot

`getRenderPageData` and `getSongsetRenderMetrics` resolve all enabled Junctions server-side. Any enabled Glue without an effective ready take returns `409 GLUE_NOT_READY` with an ordered details array:

```json
{
  "code": "GLUE_NOT_READY",
  "glues": [{
    "fromItemId": "item-a",
    "toItemId": "item-b",
    "fromTitle": "詩歌 A",
    "toTitle": "詩歌 B",
    "reasonCode": "GLUE_PROVIDER_UNKNOWN",
    "jobId": "job-uuid",
    "jobPhase": "failed",
    "retryKind": "paid"
  }]
}
```

Titles are display-only and never enter descriptor/provider content. The render warning panel localizes each reason and shows actual job phase/status. Each row provides:

- **Retry**: free only for dispatch/pre-contact recovery or verified raw-response staging; otherwise opens the exact new-credit/quota confirmation. No batch generation.
- **Open editor**: deep-link to the outgoing Item/Junction.
- **Disable AI transitions and use ordinary transitions**: confirmation identifies affected Junctions, PATCHes only those Glues off, re-resolves duration, and then permits ordinary render. It never silently disables all Songset Glues.

`createRenderJob` repeats validation at creation and snapshots immutable entries:

```json
[{
  "to_item_id": "item-b",
  "take_id": "take-uuid",
  "descriptor_key": "64-lowercase-hex",
  "r2_key": "glues/user/descriptor/take-take-uuid.wav",
  "seconds": 2.786
}]
```

The snapshot also carries the expected from/to item identity and descriptor validation context needed to reject mismatches. Missing take, wrong owner, descriptor mismatch, seconds/R2-key mismatch, or non-ready state fails with `GLUE_NOT_READY` details before enqueue. The worker trusts only this server-created snapshot and rechecks immutable metadata. Later selection of another take cannot change this job.

### 5.6 Render-worker audio and downloads

In `AssetFetcher`, add `download_glue` using the existing atomic temp-file + rename approach. Stream with 30 s connect/300 s read timeouts and bounded infrastructure retries; validate expected content length and SHA-256/checksum metadata before atomic rename. A partial cache file is never a hit. Log failures with job/take ids. Missing object, checksum mismatch, decode failure, or exhausted download retry fails the render visibly. There is no ordinary-gap fallback inside the worker.

In `audio_engine.py`:

- Define `GlueClip(path,take_id,seconds,checksum)` and pass `dict[to_item_id,GlueClip]` through `calculate_total_duration` and `generate_songset_audio`.
- Audio order is exactly **A arranged + staged Glue + B arranged**. Set gap and crossfade to zero at a Glue Junction. Insert one full Glue audio segment at previous A end, then start B after actual staged Glue seconds.
- Apply no additional crossfade/fade to the staged Glue; its 20 ms fades and gain staging are already baked in. A's outgoing fade at that Junction is suppressed along with the ordinary crossfade.
- Keep one `segments` entry per Song, shifted by Glue duration. Export `glue_windows=(previousSongEnd,incomingSongStart)` for video. Total MP3 duration and all subsequent song/Lyric/chapter times include Glue seconds.
- Preserve existing loudness/export behavior elsewhere; do not introduce a second layout formula.

### 5.7 Video and chapters

Thread `glue_windows` through `video_engine.py` and `frame_renderer.py`/`_resolve_visual_state`. At any time inside a Glue window not covered by a Song segment:

- Show only the incoming Song's existing title, using the existing pre-roll title style and CJK font loading.
- Show no outgoing or incoming lyric lines.
- Add no extra title card and do not alter the Songset's configured title-card behavior elsewhere.
- Keep ordinary gap visuals unchanged at non-Glue Junctions.

The incoming Song's chapter starts when B audio starts, after Glue. Glue is uncovered time, not a separate chapter. All later chapter and lyric global timestamps include Glue duration. The MP4 midpoint of a Glue must show B's title and no Lyrics; B's first Lyric appears at its shifted Song-local timestamp. MP3, MP4, and `chapters.json` therefore describe the same selected-take timeline.

## 6. Traditional Chinese and internationalization acceptance

### 6.1 Language invariants

The overwhelmingly Traditional Chinese catalog is a primary acceptance target, not a future localization variant. The provider request always uses the fixed English instrumental prompt/styles and arranged audio excerpts. It never contains Song titles, Lyrics, worship themes, Chinese text, or a language classifier. English and Chinese Recordings follow exactly the same download, arrangement, excerpt, generation, staging, playback, and render path.

All en/zh-Hant controls, confirmations, state labels, job failures, Retry distinctions, preflight remedies, and audio warnings ship together with identical message keys. Follow `docs/translation-guidelines.md`; do not expose descriptor/take/jargon merely because it appears in logs. Preserve user Song titles and Lyrics byte-for-byte through API, timeline, and render data.

### 6.2 Mandatory Traditional Chinese fixture and human proof

Use a real two-Song Traditional Chinese catalog fixture with Chinese titles and Traditional Chinese timed Lyrics. Its saved Song Arrangements and tempo analysis must be valid. An English pair is secondary control coverage only and cannot replace the Chinese fixture.

Automated assertions must prove:

- Unicode titles and Lyrics pass through resolved payloads and worker artifacts unchanged.
- The outgoing/incoming Chinese titles are not present in any provider request body.
- Glue window frames contain only the incoming Chinese title, rendered with existing CJK font loading, and no lyric line or extra title card.
- B's Traditional Chinese Lyrics resume at exactly the shifted global timestamps; later chapter starts also include Glue seconds.

A human must audition the generated segment and verify **no audible vocals, words, choir, or spoken word**, then judge musical fit and seam quality. Metrics, negative styles, and tests cannot prove absence of vocals or musical suitability; do not claim they do. The reviewer also verifies that Chinese titles remain unchanged, the incoming title is legible throughout Glue pre-roll, no Lyrics appear during Glue, and B's Lyrics/chapter resume at the correct shifted time in the final MP4.

### 6.3 Deployment and configuration

Update the worker README and deployment workflow/configuration with:

- `SOW_ELEVENLABS_API_KEY` on the Glue Lambda/worker only, never exposed to browser code. Web validates configuration through a server-only preflight mechanism before reservation without revealing the key.
- Existing `SOW_DATABASE_URL`, R2 bucket/endpoint/credentials, AWS region/credentials, and `SOW_SQS_QUEUE_URL` for render.
- New `SOW_GLUE_SQS_QUEUE_URL`, separate Glue SQS queue and DLQ, event-source mapping batch size 1, `maxReceiveCount` 3, visibility 2100 s, Glue Lambda timeout 1900 s, and reserved/max concurrency 2.
- Existing REST settings for local mode, with asynchronous post-commit invocation and immediate browser `202`; the same DB job and SSE stream are used.
- Binary-wheel dependency verification and a successful AMD64 Docker schema2 image build/push before deploying the shared image to both Lambdas. Deploy Glue and render functions to the same image version so arrangement/audio behavior remains aligned.

Document provider account prerequisites: paid workspace with Music inpainting access, sufficient credits, supported model `music_v2_5`, and concurrency capacity 2. Missing server key fails before reservation/spend with an operator-facing configuration error.

### 6.4 Operator recovery by job id

Provide an operator runbook keyed by `glue_jobs.id`:

1. Inspect descriptor key, item pair, phase, attempt id, heartbeat, provider-contact flag, paid reservation, persisted upload `song_id`s, raw-response key/checksum, resulting take, retry kind, and structured logs. Do not print secrets or signed URLs.
2. For queued/no-heartbeat jobs with no provider contact, create a fresh dispatch using the same job/idempotency identity. Confirm active-index and attempt state before enqueue. This is free.
3. For failures after a verified raw response, requeue **staging recovery only**, keyed to the existing job/response. Verify checksum and byte count; never invoke compose. This is free and emits a raw-response recovery metric.
4. For a persisted successful upload without compose, resume only when durable state proves exactly which provider requests completed and cancellation/descriptor still permit the next request. Never repeat that upload automatically.
5. For any ambiguous in-flight paid upload/compose, never blindly requeue. Mark/retain unknown outcome and require the user's explicit new paid attempt. DLQ arrival does not authorize provider retry.
6. For staging success but final DB failure, reconcile immutable R2 object metadata and complete the take/selection transaction only if descriptor and cancellation state still match. Never create another take or mutate the object.
7. Keep terminal jobs outside the active index. A stale queued record must be recoverable by id rather than marked spent solely because its heartbeat is old.

## 7. Implementation sequence and verification

### 7.1 Fresh-implementer sequence

Implement in this order, with the named checks in §7.2 before moving to the next layer:

1. Add domain terms to `CONTEXT.md`, a v2 ADR superseding v1's synchronous/no-reroll decisions, schema/models/migration, and cross-language descriptor fixtures.
2. Add worker canonical excerpt/staging modules and binary dependencies; prove unit fixtures and Docker wheel build without provider calls.
3. Add transactional job/quota/idempotency services, JSON routes, dedicated dispatcher/queue config, worker claim/state machine, discovery, and SSE. Test all failure boundaries with fake provider/R2/SQS.
4. Add raw-response persistence and free staging recovery; then explicit paid retry and take selection. Verify ambiguous provider responses never auto-repeat.
5. Thread the resolved take through layout, duration readers, editor, Audition, Listen/controller, and preflight. Add local Glue-failure timeline rebuild.
6. Add immutable render snapshot, worker atomic downloads/audio insertion, video Glue state, and shifted chapters/Lyrics.
7. Complete en/zh-Hant, structured logs/metrics/alerts, deployment docs, and operator runbook.
8. Run the complete no-paid-call verification, then stop for explicit authorization before either live paid acceptance job.

### 7.2 Automated tests: no paid provider calls

Use fake provider transports and temporary/local R2/DB fixtures. Tests must fail on consumer-visible regressions, not merely wiring or source text.

**Descriptor/excerpts/staging**

- Cross-language descriptor determinism, exact 6-dp half-up rounding, field sensitivity, Step-order sensitivity, `null` vs full-range Steps, no item/title/Lyric fields.
- Tail/head extraction across canonical Steps, short sides using all audio, under-1-s rejection before spend, WAV format/PCM16 encoding, and no browser/multipart path.
- Geometry slicing, response-length tolerance, envelope `ok`/`check`, tail shortfall, RMS rejection, ±18 dB gain ramp/clamp, peak limiting, 20 ms fades, and actual staged seconds.

**API/job/paid safety**

- Authentication, ownership, successor/order, disabled Glue, missing tempo/analysis, invalid arrangement, unsaved/stale snapshot, and >120 s rejection all before spend.
- Duplicate POST with the same `clientRequestId` returns original job with no second reservation; a fresh UUID is a distinct explicit intent. Concurrent duplicate POSTs cannot double-spend.
- Quota races at the fifth/sixth job share one transactional result; cache hits and free restore do not consume quota; dispatch failure releases reservation and same-id redispatch is free.
- One-active-job partial unique index under concurrent submissions; response identifies the active Junction. Provider concurrency configuration is capped at 2 independently of render.
- Cached composition/current take, cached excerpts, and cached copyright rejection avoid the corresponding provider request. Per-user isolation denies every cross-user excerpt/take/job/audio access.
- Duplicate SQS delivery before contact claims once; redelivery during ambiguous paid upload/compose makes no second provider call and records unknown outcome. Worker restart before and after provider contact reuses the exact persisted pending take id, ordinal, and seed, and resumes only persisted safe stages.
- Paid timeout/429/5xx/connection ambiguity produces `retryKind:"paid"` and no automatic retry. Entitlement/invalid plan/copyright are non-retryable in place.
- Successful upload `song_id` persists before compose. Raw response R2 checksum/byte count persist before staging. Injected staging/QA/R2/DB failures offer free processing recovery and never compose again.
- Try another preserves old takes and selection on failure; success creates/selects a new ordinal/seed. Earlier current-descriptor take restore is free. Stale-before-spend and cancellation-before-contact do not consume quota.
- SSE emits real phase/elapsed data, closes terminal, and reconnect/discovery resumes after refresh/navigation. Local REST POST returns 202 without waiting for generation.

**Layout/UI/render**

- Glue suppresses only its Junction's gap/crossfade, shifts starts/duration, and disabled/stale/unavailable states use ordinary layout. All list/editor/Listen/render readers use the same selected take/seconds.
- Audition schedules/seeks/tears down take audio with `glue:<takeId>`; generation never interrupts current playback. Glue load failure produces persistent Retry audio and rebuilds ordinary timing rather than inserting silence.
- Render preflight lists localized reasons/job statuses and each Retry/Open editor/confirmed Disable action; no silent enabled-Glue fallback. Disable affects only confirmed Junctions.
- Render creation snapshots selected `{to_item_id,take_id,descriptor_key,r2_key,seconds}` and rejects mismatch/missing/non-ready/cross-user takes. Changing selection after snapshot does not alter it.
- Worker atomic download rejects partial/cache-corrupt files, retries bounded infrastructure failures, validates length/checksum, and fails render instead of falling back.
- Worker mix is A + Glue + B with no extra Glue fade, one shifted segment per Song, correct total duration, and no silent fallback on staging/download failure.
- Video Glue midpoint shows only B's existing title and no Lyrics/extra card. Ordinary gaps are unchanged. Chinese Unicode text, B Lyrics, and chapter starts occur at shifted times; Glue has no chapter.

Run the webapp targeted Vitest suites, then `pnpm test`, `pnpm lint`, and `pnpm build`; run worker targeted tests, then `cd delivery/render-worker && uv run --extra dev pytest tests/ -v`; prove the Lambda Docker build with binary-only dependencies. These are implementation-phase commands, not commands for this documentation task.

### 7.3 Manual proof and paid-call gate

**Phase A — no paid calls.** Use deterministic fake provider/raw-response fixtures or explicitly seeded immutable test takes for the complete journey:

1. Enable Glue, select each beat option, verify duration/new-credit/quota display, submit one fake job, and receive 202. Refresh, close/reopen, navigate away/back, and reconnect SSE; the same job/phase resumes.
2. Exercise initial failure with ordinary playback, a failed Try another preserving current take, free staging recovery, and unknown-provider-outcome copy. Verify errors are localized, inline, job-correlated, and sanitized.
3. Audition ready takes in Preview and Listen; seek into Glue; simulate audio failure and verify re-layout/Retry audio. Restore an earlier current-descriptor take for free and confirm both surfaces select it.
4. Exercise stale draft/save, Recording/arrangement/order changes, enable/disable, duplication, and descriptor reversion. Verify no destructive cache deletion.
5. Verify preflight Retry/Open editor/Disable, including free vs paid labels and no batch spend. Render the selected take and prove final MP3/MP4/chapter timing with fake/seeded audio, including the Traditional Chinese fixture and English secondary control.
6. Verify logs/metrics/DLQ/operator recovery using synthetic failures. No paid call is implied by spec approval or Phase A.

**Phase B — separately authorized paid acceptance.** Obtain explicit authorization immediately before exactly one initial paid job for the real Traditional Chinese pair and, separately, one paid Try another. Confirm displayed estimate/quota before each. Do not run any other live generation or automatic retry.

- Initial job: verify background refresh/resume, actual phase/elapsed, raw response persisted before staging, new ready take, Chinese title/Lyric preservation, and human no-audible-vocals/musical-fit review.
- Try another: confirm new paid cost/quota, new take identity/seed, prior take retained, failed/unknown behavior where safely observable without another paid request, and free restoration of the initial take.
- Render both selected-take states as authorized without further generation. Verify Preview/Listen parity, preflight remedies on a separate unavailable fixture, final MP3 duration/audio, MP4 Glue title/no-Lyrics/CJK legibility/shifted Traditional Chinese Lyrics, and chapters starting at B. Record job/take ids and human listening verdict; never infer it from metrics.

## 8. File map

### Webapp

- `delivery/webapp/src/db/schema.ts` and generated `drizzle/` migration: takes, jobs, paid attempts, excerpts, selected-take pointer, render snapshots.
- `delivery/webapp/src/lib/glue/descriptor.ts`, `resolve.ts`, `jobs.ts`, `quota.ts`, `client.ts`, `elevenlabs-config.ts`: canonical identity, shared resolution, transactions, browser JSON/SSE client, safe configuration preflight.
- `delivery/webapp/src/app/api/songsets/[id]/glue-jobs/route.ts`: JSON-only 202 job creation/free initial resolution.
- `delivery/webapp/src/app/api/glue-jobs/[id]/route.ts`, `events/route.ts`, `cancel/route.ts`: safe status, DB-polled SSE, pre-contact cancellation.
- `delivery/webapp/src/app/api/songsets/[id]/items/[itemId]/route.ts`: enable/beats/free selected-take PATCH.
- `delivery/webapp/src/app/api/glues/[takeId]/audio/route.ts`: owned immutable take streaming.
- `delivery/webapp/src/lib/render/dispatcher.ts`, `src/lib/sqs/client.ts`, `src/lib/render/rest-client.ts`: kind-aware separate render/Glue dispatch and asynchronous local invocation.
- `delivery/webapp/src/lib/db/songsets.ts`, `src/lib/arrangements.ts`, arrangement-listen/controller readers: selected-take resolution and one shared layout.
- `delivery/webapp/src/lib/audio/songset-audio.ts`, `audio-bytes-cache.ts`, `src/hooks/useSongsetAudio.ts`: `glue:<takeId>` schedule/seek/teardown and failure re-layout.
- `delivery/webapp/src/components/songset/ArrangementEditorSheet.tsx`, `SongList.tsx`, `src/app/songsets/[id]/SongsetEditorClient.tsx`: Preview rail/jobs/takes/inline errors.
- `delivery/webapp/src/components/audio/SongsetPlayerBar.tsx` and Listen/controller components: playback parity and persistent audio warning.
- `delivery/webapp/src/components/render/RenderForm.tsx`, `src/app/songsets/[id]/render/RenderPageClient.tsx`, `src/lib/render/job-manager.ts`, `src/app/api/render-jobs/route.ts`: preflight actions and immutable snapshots.
- `delivery/webapp/src/lib/render/error-message.ts` and i18n message bundles: safe/localized errors and all UI copy.
- Web tests beside the above modules plus shared cross-language descriptor fixtures.

### Python worker

- `delivery/render-worker/pyproject.toml`, `uv.lock`, `requirements.txt`, Dockerfiles: NumPy/SoundFile exact binary-compatible versions.
- `src/sow_render_worker/glue_descriptor.py`, `glue_excerpts.py`, `glue_provider.py`, `glue_staging.py`, `glue_jobs.py`: canonical algorithms and durable state machine; no POC import.
- `src/sow_render_worker/lambda_handler.py`: omitted-kind render compatibility and `glue.generate` → `execute_glue_job`.
- `src/sow_render_worker/db.py`, `asset_fetcher.py`: worker models/job claims and atomic validated Glue downloads.
- `src/sow_render_worker/pipeline.py`, `audio_engine.py`: snapshot validation/download, Glue insertion, durations and windows.
- `src/sow_render_worker/video_engine.py`, `frame_renderer.py`: Glue pre-roll/no-Lyrics state with existing CJK fonts.
- `delivery/render-worker/tests/`: descriptor/excerpt/staging/job/recovery/render/audio/video coverage.
- `delivery/render-worker/README.md`, deployment workflow/config: separate Glue queue/Lambda/DLQ, limits, metrics, alerts, and operator runbook.

### Documentation

- `CONTEXT.md`: Junction/Glue Excerpt/Descriptor/Take/Job terminology and selected-take invariant.
- New `docs/adr/` entry: durable dedicated worker execution, retained paid takes, English-only prompt/Chinese QA, raw-response recovery, and no ambiguous provider retry.
- This file supersedes v1; do not edit v1 or import POC code into production.

## 9. Explicit non-goals and unresolved provider limitation

No Android generation UI, cross-user cache sharing, automatic musical-quality/vocal judgment, batch candidates, silent render fallback, Glue chapters, storage pruning, or combined Glue-plus-ordinary-crossfade is added. Existing Android/Listen consumption of already rendered artifacts is not a generation surface.

ElevenLabs currently provides no assumed server-issued idempotency key for upload/compose. Therefore an ambiguous provider completion is potentially paid and is never automatically repeated. A future provider idempotency capability requires a separate specification and durable identity design before it changes this rule.

