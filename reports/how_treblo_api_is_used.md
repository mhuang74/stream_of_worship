# How the Treblo API is used by the junction-Glue POC

Reference for `lab/poc-scripts/gen_glue_treblo.py`. Every claim below is settled by that script, by
the paid-but-refused `run2` artifacts under `lab/poc-scripts/output/treblo-glue/`, or by the vendor
reference where attributed. Line numbers are hints (`:NNN` = definition line at the time of
writing); re-read the symbol before quoting it.

## 1. Scope and endpoints

Exactly one driver calls the API: `lab/poc-scripts/gen_glue_treblo.py`, against base
`API = "https://api.treblo.com/v1"` (`:88`) with the paid endpoint
`ENDPOINT = "POST /generations/v2/inpaint"` (`:89`). There is no second generation route in the
script and none available as a fallback (§7).

| call | function | auth | purpose | failure semantics |
|---|---|---|---|---|
| `POST /uploads` (multipart, field `file`) | `treblo_post_upload` `:733` | Bearer | turn a montage into an `audio_upload_id` | free; re-encode to FLAC once on 413/415; retry connection errors and `RETRYABLE_UPLOAD_STATUSES` (408/429/500/502/503/504/520–524) up to 3 times at 30/60/120 s, then abort; 401/402/422 fatal and unretried |
| `POST /generations/v2/inpaint` (JSON) | `treblo_submit` `:786` | Bearer + `Content-Type: application/json` | buy one section replacement | 100 credits; 422 uncharged; the id is accepted as `task_id`, `id` or `job_id` |
| `GET /generations/status/{id}` | `treblo_status` `:820` | Bearer | poll, 5 s interval, 900 s cap (`POLL_INTERVAL_SECONDS`, `POLL_TIMEOUT_SECONDS`) | body is a JSON *string*; empty or unparseable body = still running, never a failed take |
| `GET /generations/{id}` | `treblo_task` `:846` | Bearer | terminal task payload | non-object or non-JSON body is fatal, not retried |
| `GET song_paths[0]` on `cdn.treblo.com` | `treblo_download` `:898` | none | fetch the returned section bytes | the CDN link carries no `Authorization` header |
| `GET /v1/credits/balance` | not called | — | referenced only in the HTTP 402 hint text (`:640`) | manual check only |

```mermaid
sequenceDiagram
  participant S as gen_glue_treblo.py
  participant API as api.treblo.com/v1
  participant CDN as cdn.treblo.com
  S->>API: POST /uploads (multipart, file=montage.wav)
  API-->>S: audio_upload_id 2ae7ff90-ce42-4ad2-b921-0ee2bcf162c5
  S->>API: POST /generations/v2/inpaint (JSON, 100 credits)
  API-->>S: task_id c80da03a-b1ef-4c85-80da-d7bf257b0655
  loop every 5 s, capped at 900 s
    S->>API: GET /generations/status/{task_id}
    API-->>S: "TRANSCRIBE_STAGE_1" … "SUCCESS"
  end
  S->>API: GET /generations/{task_id}
  API-->>S: song_paths[0], model_version v2.2
  S->>CDN: GET song_paths[0]
  CDN-->>S: flac section bytes
```

`TREBLO_API_KEY` is read from the environment, or filled in by `load_env_file` from
`ENV_FILE = Path("/opt/sow/.env")` (`:90`) when unset. Every authenticated call sends
`Authorization: Bearer <key>` (`_api_headers`, `:655`).

Rotating proxy: an `SOW_YOUTUBE_PROXY` in the **environment** — the port-range notation
`http://user:pass@host:10001-10010`, parsed by
`stream_of_worship.admin.services.proxy.parse_proxy_pool` via `load_proxy_pool` (`:655`) — routes
upload, submit, status and task calls through one randomly chosen port per request
(`request_proxies`, `:667`); the CDN download stays direct. `SOW_YOUTUBE_PROXY` is deliberately
absent from `ENV_KEYS` (`:145`), so a value that exists only in `/opt/sow/.env` is ignored and the
run is direct — the script never prints the raw URL, only `redact_proxy_url(...)`.

## 2. Input given

The input is **one montage of two songs, not two songs**. `build_montage` (`:398`) concatenates

```
[ A arranged tail 12.0 s ][ digital-zero hole = beats × 60 / BPM ][ B arranged head 12.0 s ]
```

and enforces three invariants before the bytes are uploaded: each side must be byte-identical to its
arranged source (`np.array_equal` on both slices), the hole must be all-zero (`np.any` on the hole is
false), and `wav_bytes ≤ MAX_UPLOAD_BYTES` (40 MB, `:104`). The format is fixed: 44.1 kHz stereo
PCM16 (`SR`, `CHANNELS`, `SAMPLE_WIDTH` at `:97-99`), written content-addressed into
`<out-root>/uploads/<sha16>.wav`.

Where the audio comes from:

- `fetch_songset` (`:538`) reads `songset_items` joined to `songs` and `recordings` from
  `SOW_DATABASE_URL`; the tempo used for the hole is `recordings.tempo_bpm`.
- `ensure_source_audio` (`:604`) downloads the R2 mp3 once into `<out-root>/cache/` and decodes it
  once, so both cache files are shared by every run under that root.
- `arranged_audio` (`:521`) concatenates every `arrangement_steps` entry **in array (playback)
  order**, not sorted by `startSeconds`. Treblo therefore hears exactly what the render plays at the
  seam, not a raw time window of the recording.

Hole geometry comes from `glue_specs` (`:304`): `beats × 60 / tempo`, whole beats of the **incoming**
song's tempo, refused below `MIN_SECTION_SECONDS = 0.1` (`:103`). Default `--beats 4`. There is no
generation floor to pad to and no tail to trim — the requested section *is* the glue length.

The `run2` montage as recorded in `run2/index.json` `.junctions[0].montage`:

| field | value |
|---|---|
| total length | 27.43839 s (12.0 s sides + 3.43839 s hole) |
| target section | 3.43839 s = 4 beats at 69.8 BPM = 151,633 frames |
| WAV size | 4,840,176 bytes |
| sha256 | `2162bad6dcb2be0b9b8aa22ba161691caedf671a858f24ac8dae3843e5dc63ac` |
| `audio_upload_id` | `2ae7ff90-ce42-4ad2-b921-0ee2bcf162c5` |

Upload caching: `treblo_upload` (`:735`) keys `uploads.json` by the **full** sha256 of the montage
PCM, and `_upload_is_fresh` (`:721`) treats an entry older than `UPLOAD_TTL_SECONDS = 20 h` (`:108`)
— or one whose timestamp is missing/unparseable — as a cache miss and re-uploads. Uploads cost no
credits; the margin exists because ids expire "about 24 hours" after upload.

The content guard: `POST /uploads` answers HTTP 422
`{"detail":"This upload matches existing audio and cannot be used."}` for audio Treblo will not
accept as a source. That refusal carries no `audio_upload_id`, and `/v1/uploads` exposes exactly one
operation (POST — no list, no GET by id), so the montage cannot be submitted by any route: the
junction is recorded as `blocked` in `index.json` (`junctions[].status`, `blocked_reason`) with a
`blocked:` note in `report.html`, the rest of the run continues, and no credits are spent. The
failure is raised as `UploadBlocked` (`:159`) and handled per junction in `_execute_run` (`:2389`);
the full-set build is skipped with that cause named (`:2604`), because a blocked junction has no
Glue to assemble. Never resubmit the refused audio through another input (`audio_base64`,
`audio_url`) or transform it: that is the same audio aimed past a content refusal.

Classification boundary: only the upload 422 whose body contains `matches existing audio` is a
per-junction block. Any *other* upload 422 stays fatal (`_fatal_from_response`, `:690`), because that
status also covers request-shape errors — a silently absorbed shape error would mark every junction
blocked and exit 0 with zero takes, hiding a code bug instead of reporting it. A second content 422
shape (e.g. a copyright flag) has not been observed: every refusal on record carries the
`matches existing audio` detail above, and the raw body is printed verbatim in `blocked_reason` /
the fatal message, so a new shape is identifiable the first time it appears and can then be
classified per junction.

Byte-identity dedupe was settled by a free two-upload probe (2026-11-24, 3 s 220 Hz stereo tone,
`/tmp/treblo-probe-tone.wav`): the **same bytes were accepted twice**, with two distinct
`audio_upload_id`s (`fd49ba4b-…`, `3b7b45c2-…`). The guard therefore matches **content, never our own
upload history**, so a 422 can never mean "the earlier 524 actually landed" — and the 6-of-7 refusal
rate below, on first uploads, is content matching, exactly as recorded.

Retrying a refusal is not a workaround, and is therefore not implemented (`UploadBlocked` is raised
on the first 422). Measured 2026-11-24 on the refused montage of `oQLbFBCtkoeWRMewz5cja` j2
(27.62 s, sha `2072e1274dc76680…`): five consecutive `POST /uploads` of the identical bytes, each
through a different proxy exit IP (`…:10009`, `:10004`, `:10004`, `:10006`, `:10005`), returned the
same 422 `{"detail":"This upload matches existing audio and cannot be used."}` every time. The
verdict is stable within a run, so a refusal ends that junction immediately; it can still differ
between runs (this songset's j1 was refused in the probe table above and accepted on 2026-11-24 with
`audio_upload_id dfdf4bfd-…`), which is why the run keeps going and a later run is worth retrying.
Free upload probes over the arranged songsets in the library, each sending the exact montage the paid
run would send:

| songset | j1 | j2 |
|---|---|---|
| `hbWfCccGxN_JLQGBEqmdu` Small Group Morning Fortress | ✗ 422 | — |
| `i3-Ps8YpofpBmJ8_rNLzN` Test Arrangement | ✗ 422 | ✗ 422 |
| `2ZV5SHjAzw7ceJE-nREs2` Friday Worship - Arranged | ✗ 422 | **✓ 200** |
| `oQLbFBCtkoeWRMewz5cja` Monday Worship - Arranged | ✗ 422 | ✗ 422 |

Seven junctions, one accepted. Passing is incidental to the exact audio sent, not a property of a
recording, so the montage for `2ZV5SHjAzw7ceJE-nREs2` j2 is the only one this POC ever bought.

## 3. Request body

The literal paid body, `run2/junctions/j2/plans/beats-4.json`, written by the take loop before the
submit and byte-for-byte the JSON posted:

```json
{
  "audio_upload_id": "2ae7ff90-ce42-4ad2-b921-0ee2bcf162c5",
  "tags": [
    "worship",
    "praise & worship"
  ],
  "sections": [
    [
      12.0,
      15.43839
    ]
  ],
  "selection_crop": true,
  "num_songs": 1,
  "seed": 0,
  "prompt_strength": 2.0,
  "balance_strength": 0.7,
  "output_format": "flac",
  "instrumental": true
}
```

| field | run2 value | source | constraint |
|---|---|---|---|
| `audio_upload_id` | `2ae7ff90-…` | `treblo_upload` | names this run's montage; `audio_url`/`audio_base64` are deliberately never sent (`inpaint_body` `:435`) so the API cannot regenerate a different window |
| `tags` | `["worship","praise & worship"]` | `--tags` | closed v2 vocabulary, `data/treblo-v2-tags.txt` (4,160 entries; the last line has no trailing newline), `validate_tags` `:364`, at most `MAX_TAGS` 64; an off-list tag is a 422 and is not charged |
| `sections` | `[[12.0, 15.43839]]` | `build_montage` hole bounds | absolute seconds, one window; the window *is* the glue length, floored at `MIN_SECTION_SECONDS` 0.1 |
| `selection_crop` | `true` | constant | return only the replaced section, not the whole montage |
| `num_songs` | `1` | constant | fixed at one |
| `seed` | `0` | `--seed-base` + take index | part of the response-cache key, so two takes never share cached audio |
| `prompt_strength` | `2.0` | `--prompt-strength` | CLI-validated 1.4–3.1 |
| `balance_strength` | `0.7` | `--balance-strength` | CLI-validated 0.0–1.0; vendor reference: vocal naturalness vs. instrumental sharpness — *not* a reference/hole weighting |
| `instrumental` | `true` | `--instrumental` (default) | mutually exclusive with `lyrics` |
| `lyrics` | absent | `--lyrics` | required and non-empty when `--no-instrumental`; `inpaint_body` raises otherwise |
| `output_format` | `"flac"` | `--output-format` | stored as `.bin`, then written as `.flac` |

Never sent: `prompt` (v2 rejects it outright), `bpm`, `key`, `negative_tags`. The v2 inpaint body has
no field for tempo or key, which is why a glue cannot be requested in the incoming song's key — the
joint and residual metrics are the evidence, not a key match.

## 4. Response handling

Three status body shapes reach `treblo_status` (`:820`), and all three are handled:

| body | decoded as | result |
|---|---|---|
| `"TRANSCRIBE_STAGE_1"` (JSON string) | `resp.text` → `json.loads` → `str` | the status word, verbatim |
| `{"status": "SUCCESS"}` (object, `include_alignment`) | dict | `payload["status"]` |
| empty, or text that is not JSON | — | `PENDING` (or the first 40 chars), so the poll continues |

An empty or undecodable body is *never* a failed take. The bug recorded in
`reports/treblo_glue_poc_impl_summary.md` §6 was double-parsing this body (`resp.json()` then
`json.loads()` on the resulting string), which raised on every poll of an already-charged task.

The terminal payload from `treblo_task` (`:846`) carries `song_paths[0]` (the CDN FLAC),
`model_version "v2.2"`, `error_message`, and the server's own echo of the accepted parameters. From
the cached `.meta.json` for this take: `inpaint_params` = `{sections: [[12.0, 15.43839]],
audio_url: "upload", lyrics: null, selection_crop: true}`; `v2_params` = `{balance_strength: 0.7,
seed: 0, bpm: null}`; `extend_params`, `reference_params`, `v3_params` and `prompt` all `null`;
`prompt_strength 2.0`; `style_scale 1.0`.

`resolve_section` (`:981`) then puts the bytes where the hole was. `selection_crop` is **not**
sample-exact: `run2` returned 147,456 frames = 3.3437 s for a 3.43839 s request (Δ −94.717 ms), which
is zero-padded at the tail and recorded as `padded_ms 94.717`, `trimmed_ms 0.0`,
`section_delta_ms` −94.717. Two geometries are accepted:

- `crop` — returned length within `SECTION_TOLERANCE_SECONDS` 0.1 (`:105`) of the request; padded or
  trimmed at the tail.
- `montage` — returned length within 0.5 s of the whole montage, i.e. `selection_crop` was ignored;
  the hole is sliced out as `[hole_start, hole_end]`.

Anything else raises and fails the take rather than splicing the wrong audio.

Post-API handling is local, not the vendor's: `stage_edges` (`:1041`) ramps gain in dB between A's
exit RMS and B's entry RMS with an 18 dB clamp each side; `apply_fades` (`:1075`) applies 20 ms
linear fades to the glue only; `splice` (`:1086`) concatenates A + glue + B and asserts A and B stay
sample-identical; `evaluate_status` (`:1278`) turns the measurements into `ok`/`check`/`failed` on
five rules — glue level below −50 dBFS, section delta over 100 ms, either residual over 6 dB, either
joint step over 3 dB, either jump ratio over 2.0.

## 5. Cost and idempotency

`CREDITS_PER_REQUEST = 100` (`:107`); uploads are free, and a task that ends `FAILURE` is not
charged. The estimate printed before any submit is junctions × beats × takes × 100.

A paid response is never bought twice. `response_key` (`:915`) is the sha256 of
`{"endpoint", "body", "take"}` — for this take,
`36524da4f8a006aae3cb6ad4da3b052785892a105f383c5e0e414e6f2b6a7727`. `treblo_generate_cached`
(`:925`) writes `<key>.request.json` (the task id) immediately after a successful submit, then
`<key>.bin` (the downloaded section) and `<key>.meta.json` (the task payload). A key with a request
but no `.bin` is an interrupted run: the saved task id is **polled**, never resubmitted.
`--refresh-responses` is the only path that buys again.

That is what preserved this paid take: the first attempt died on the status-parse bug after the
submit had already been charged, and the rerun resumed task `c80da03a-…` instead of spending a second
100 credits.

## 6. What the paid run measured

`run2`, songset `2ZV5SHjAzw7ceJE-nREs2` junction j2 (祢的救恩 → 當祢走進我們當中), 4 beats, one take,
100 credits. Task `c80da03a-b1ef-4c85-80da-d7bf257b0655`, `model_version v2.2`, balance 3000 → 2900.

Every figure below is the recorded one, from `run2/index.json`, the raw `.bin` in
`cache/treblo-responses/`, and `reports/treblo_glue_poc_impl_summary.md` §4 — no new measurements.

| measured | value |
|---|---|
| returned section | 147,456 frames = 3.3437 s (requested 3.43839 s, Δ −94.717 ms) |
| peak | 0.00152588 = 50 / 32768 |
| RMS, returned section overall | −88.52 dBFS |
| RMS, first 0.17 s | −75.71 dBFS |
| RMS, remainder | −103.50 dBFS |
| `glue_rms_db` (padded raw section, pre-staging) | −88.628 dBFS |
| staged + faded glue | −82.767 dBFS |
| staging requested / applied | +59.686 / +78.390 dB requested, clamped to 18 / 18 dB, residuals 41.686 / 60.390 dB |
| joints | A step −66.032 dB (jump ratio 2.229); B step +208.632 dB |
| status | `failed` — `glue level -88.628 dBFS is below -50 dBFS` |

Stated plainly: **the model wrote no music into the masked region.** Its own bytes are near-digital
silence with a faint onset transient — not an echo of the upload, whose hole held exactly 0.0.
`glue_rms_db` is measured after the pad and before `stage_edges`, which is why the gate reads a
property of the vendor's audio rather than of the staging.

Two rival explanations remain **untested**; neither is asserted here, and each is another 100-credit
probe:

1. *Silent-hole content.* v2 may treat the masked region's digital zeros as content to keep. The
   discriminating probe is one request over a section holding real audio from the same cached upload.
2. *`instrumental: true`.* Every historically working v2 inpaint configuration known uses `tags` plus
   `lyrics` with `instrumental: false`; no working instrumental-mode inpaint has been observed.

## 7. Documented vs. observed API behaviour

- `balance_strength` is **vocal naturalness vs. instrumental sharpness** per the vendor reference,
  not a reference/hole weighting. The CLI help and README now say so; an earlier draft treated it as
  a plausible fix for the silence, which it is not.
- v2 inpaint **rejects `prompt`** and wants `tags` plus either `lyrics` or `instrumental: true`. The
  body never sent a prompt, so this was documentation risk, not a defect.
- Section replacement is **v2 only**. v3 exposes `operation: replace|insert` and
  `prefix_context_seconds`/`suffix_context_seconds` in the OpenAPI schema, but the vendor reference
  states replace for v3 is "coming soon", so there is no fallback endpoint to move to today.

## 8. Source anchors

| anchor | line | what it settles |
|---|---|---|
| `build_montage` | `:398` | sides byte-identical, hole all-zero, 40 MB cap |
| `inpaint_body` | `:435` | the exact body key set; `audio_url`/`audio_base64` never sent |
| `treblo_upload` | `:735` | sha256-keyed `uploads.json`, TTL, content-addressed WAV path |
| `treblo_submit` | `:786` | the charged POST and the `task_id`/`id`/`job_id` handle |
| `treblo_status` | `:820` | the JSON-string body and the still-running fallback |
| `treblo_generate_cached` | `:925` | the three cache files and resume-without-resubmit |
| `resolve_section` | `:981` | `crop` vs `montage` geometry, pad/trim, the reject path |
| `stage_edges` | `:1041` | edge-anchored dB ramp and the 18 dB clamp |
| `evaluate_status` | `:1278` | the five `ok`/`check`/`failed` rules |
| `run2/junctions/j2/plans/beats-4.json` | — | the literal paid request body in §3 |
| `cache/treblo-responses/*.meta.json` | — | the server's echo quoted in §4 |
