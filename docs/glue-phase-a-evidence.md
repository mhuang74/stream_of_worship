# Phase A evidence: no-paid-call Glue journey (issue #323)

Executed 2026-11-22 against the local dev server (`https://localhost:8080`, commit
`c39a9585` + the Phase A fixes in `3a732dd5`), dev database behind
`SOW_DATABASE_URL`, no `SOW_ELEVENLABS_API_KEY` set anywhere. Spec:
`specs/inpaint-transition-elevenlabs-v2.md` §7.2, §7.3 Phase A.

Paid Phase B was **not** run. Nothing in this document spends credits.

## Automated proofs (fakes only)

| Suite | Command | Result |
|---|---|---|
| Web journey (TC fixture) | `pnpm test src/test/journey/glue-tc-journey.test.ts` | 7 passed |
| Worker journey (TC fixture) | `uv run --extra dev pytest tests/test_glue_journey.py -q` | 3 passed |
| Webapp full | `pnpm test` | 208 files, 3534 passed, 3 skipped, 10 todo |
| Webapp lint | `pnpm lint` | 0 errors (13 pre-existing warnings) |
| Webapp typecheck | `pnpm typecheck` | clean |
| Webapp build | `SOW_DATABASE_URL=… pnpm build` | succeeded before (111 s) and after (128 s) the Phase A fixes |
| Worker full | `SKIP_DOCKER_TESTS=1 uv run --extra dev pytest tests/ -q` | 1123 passed, 8 skipped |
| AMD64 image | `docker build --platform linux/amd64` | succeeded; imports `numpy`, `soundfile`, `PIL`, `psycopg2`, `boto3`, `sow_render_worker.pipeline`; static `ffmpeg 7.0.2` |

The worker journey proves the no-paid-call rule structurally: guards raise if
`ElevenLabsHttpTransport` or `urllib3.PoolManager` is constructed,
`create_provider_from_env` is called, or `SOW_ELEVENLABS_API_KEY` is read.

## Executed Phase A items (browser, real dev server)

Songset `2ZV5SHjAzw7ceJE-nREs2` ("Friday Worship - Arranged"), Junction
「我揚聲敬拜」→「祢的救恩」. Measured with `getBoundingClientRect`/DOM reads, not
eyeballed.

| §7.3 item | What was done | Observed |
|---|---|---|
| 1 enable + every beat option + quote/quota | Toggled the rail switch; clicked 2, 4, 8, 16 beats | Switch `aria-checked=true`; seconds 3.0 / 3.6 / 7.2 / 14.5 s; credits 405 / 414 / 469 / 577; "5 of 5 paid AI transitions left today"; each PATCH persisted (`glue_beats` 2→8→16→4) |
| 1 refresh / reopen / navigate away-back | Full reload, reopened the sheet after each beat change | Saved switch read on, saved beats selected, quote matched the saved beats |
| 1 submit with no provider key | Clicked Create → confirmation → confirm | Inline "AI transitions are not available on this server." (`GLUE_NOT_CONFIGURED`); `glue_jobs`=0, `glue_takes`=0, `glue_paid_attempts`=0 |
| 5 preflight remedies | Opened `/songsets/2ZV5SHjAzw7ceJE-nREs2/render` | "AI transitions need attention" with A→B, reason, and **Try another (paid)** / **Open editor** / **Disable AI transitions and use ordinary transitions**; Start Render disabled |
| 5 free vs paid labels | Read the remedy row | Retry is labelled paid; Disable is not a spend |
| 5 "Open editor" | Clicked it | Landed in the sheet for the outgoing item, rail showing the same junction and 405-credit quote |
| 5 "Disable" | Clicked, then confirmed "Disable and use ordinary transitions" | Dialog names the pair ("我揚聲敬拜 → 祢的救恩 … will use the ordinary transition instead"); after confirming, the preflight section disappeared, Start Render enabled, and only that junction changed (`glue_enabled` position 0 true→false, positions 1–2 unchanged) |
| 5 confirm copy | Read the confirm body | "This uses 405 new credits. 5 of 5 paid AI transitions left today." |

Not executed in the browser (fakes required, covered by tests instead):
SSE reconnect after navigation, a provider-backed 202 job with phase
progression, failed Try-another preserving the take, free staging recovery,
unknown-outcome copy, free restore of an earlier take, and stale-draft revert.
The automated journeys cover the same contracts with injected transports; the
browser surfaces need a fake-provider server mode that does not exist in the
deployment (and `test_deploy_glue.py` asserts the deploy scripts never select
one).

## Seeded-take audition, snapshot render, and artifacts

Executed 2026-11-22 after the run above, on the same junction, using an
**explicitly seeded immutable test take** (§7.3 Phase A) — a 3.61991 s
1000 Hz stereo/44.1 kHz WAV at
`glues/15/88fdcf7b…/take-11111111-2222-4333-8444-555555555555.wav`, take id
`11111111-2222-4333-8444-555555555555`. No provider was contacted.

The descriptor for the take was built with the worker's
`sow_render_worker.glue_descriptor.build_descriptor` from the live item rows and
produced key `88fdcf7beaf6de82c2ad323806d63b88481b066bcda2cb8899f1c8bbc956eef3`,
byte-identical to the key the webapp quote route computed for the same rows —
a live cross-language determinism check, not a fixture.

| §7.3 item | What was done | Observed |
|---|---|---|
| 3 audition in Preview | Opened the sheet, pressed Preview | Fetched A, B and `/api/glues/11111111-…/audio`; playhead advanced to 0:06; range request returned `206`, `RIFF`, `cache-control: private, max-age=3600` |
| 3 take audio proxy | `GET /api/glues/<takeId>/audio` with `Range: bytes=0-63` | `206`, 64 bytes, `RIFF` header |
| 5 deep link | `/songsets/2ZV5SHjAzw7ceJE-nREs2?glueItem=VwEji7gQxQoIFGQrO15xG` | Sheet opened for the junction, then the param was stripped and a reload did not reopen it |
| 5 preflight with a ready take | `/render` | No "needs attention"; Start Render enabled, no blocked junction |
| 5 render from the selected take | Started the render from the browser | Job `MmX2CWqUge8Mr_zIxhRh5`; REST dispatch to the local worker image; phases `preparing → mixing_audio → rendering_frames → encoding_video → uploading → completed`, 0 → 100 % |
| snapshot frozen with the take | Read `render_jobs.glue_transitions` | One entry: take id, descriptor key `88fdcf7b…`, staged key, `seconds 3.61991`, `sha256 1a7f9c1e…` |
| 5 MP3 / MP4 / chapters | ffprobe on the uploaded artifacts | MP3 560.300408 s (AAC/MP3 frame padding), MP4 560.269002 s vs `total_duration_seconds` 560.2689 s (≈0.1 ms), `chapters.json` 3 chapters |
| 5 embedded chapters | `ffprobe -show_chapters` on the MP4 | 3 chapters: 我揚聲敬拜 0.0000→241.1790, 祢的救恩 241.1790→450.6280, 當祢走進我們當中 450.6280→560.2680 |
| 5 Glue midpoint frame | Frame at 239.37 s (Glue window 237.560→241.180) | Incoming title 「祢的救恩」 only, CJK glyphs rendered, no lyric lines, no extra title card |
| 5 lyrics resume after Glue | Frame at 242.5 s | 「急難之中」 line at its shifted global time 241.17991, CJK rendered |
| 6 duration integration | `GET /api/songsets/<id>` with the take selected, then cleared | 560.2691077077365 s selected / 558.4591524588676 s cleared; Δ = glue 3.61991 − the ordinary 2-beat gap 1.809955, matching `getSongsetDurationSeconds` |
| 6 rendered artifact in the player | `/play/controller` | Player total 9:20, requests the signed MP4 and `chapters.json` for job `MmX2CWqUge8Mr_zIxhRh5` |

The MP4 chapter track cannot store a gap: `chapters.json` ends 祢的救恩 at
448.910 and starts 當祢走進我們當中 at 450.628 (the ordinary gap minus crossfade
region belongs to neither Song), while the MP4 text track is contiguous, so
ffmpeg extends the outgoing chapter to the next chapter's start. Reproduced in
isolation (a 10 s clip plus chapter metadata with START/END 0–3000, 3000–6000,
7000–10000): ffprobe reports `Two 3.000 → 7.000`. The two artifacts are not
computed differently — `_segment_to_chapter_info` and
`build_chapters_from_segments` agree; MP4 simply has no way to express the gap.
Whether `chapters.json` should close the gap instead is a product decision, not
addressed here.

Reads after a write: the editor's songset GET came back one mutation stale
(clear a take with a product `PATCH`, and the next `GET` still reported it
selected). The API route is not cached, the DB read is not stale, and
`?cb=<random>` was always correct — the service worker was answering from
`sow-api-songs`, whose matcher `url.pathname.startsWith("/api/songs")` also
matches `/api/songsets…`, so the stale-while-revalidate catalog route owned
every songset read and the network-first songset route below it never ran
(workbox matches in registration order). Fixed in `public/sw.js` by excluding
`/api/songsets` from the catalog matcher; the route also now answers
`Cache-Control: no-store` so no layer may store a per-mutation body. Verified
live with the corrected worker: every `PATCH` was readable on the next plain
`GET`; with the worker bypassed (`Network.setBypassServiceWorker`) the same
`PATCH`/`GET` sequence was current beforehand, which pinned the layer.

The seeded take, the one failed probe job, and both probe renders were deleted
after this recording (rows, R2 objects, and the item's selected take), so the
ids above are historical and the dev songset carries no synthetic Glue audio.

## Defects found and fixed while executing Phase A

1. `songsets/[id]/page.tsx` mapped items without `glueEnabled`/`glueBeats`/`glue`:
   a saved AI transition read as off after every reload and the rail could not
   resolve its junction. Regression: `src/test/app/songset-editor-page.test.tsx`.
2. The quote GET had a stable URL while its body changes with the saved beat
   count, so the browser replayed the previous count's credits after a beat
   PATCH. Fixed by `cache: "no-store"` on the quote fetch and the shared Glue
   client, plus `force-dynamic` on the quote route. Regression: "requests the
   quote without a browser cache…".
3. A read replica answered a quote issued right after the PATCH with the
   pre-write row — credits for a different descriptor. The rail now requires the
   answer's `descriptorKey` to match the saved junction, retries briefly, and
   otherwise shows the quote error. Regressions: "never shows credits computed
   for a different descriptor", "surfaces a quote error instead of numbers…".
4. A duplicate render delivery that lost the claim still deleted the **winning**
   invocation's working directory. `execute_render_pipeline` called
   `asset_fetcher.get_job_temp_dir(job_id)` before the claim, so the losing
   invocation owned `<temp>/<jobId>` too, and the `finally` block removed it
   while the winner was mid-encode. Observed live: FFmpeg finished the frames
   and failed the trailer with `Error writing trailer: No such file or
   directory` at 79.8 %. The temp dir is now taken only after the claim wins.
   Regressions: `test_duplicate_delivery_keeps_winner_working_files` and the
   updated `test_pipeline_uses_reclaim_likely_dead_job`.
5. The service worker served every `/api/songsets…` read from the catalog
   route's stale-while-revalidate cache: `workbox.routing` matches in
   registration order and `url.pathname.startsWith("/api/songs")` is also true
   for `/api/songsets…`, so the network-first songset route was unreachable
   dead code. A take cleared by a `PATCH` still read as selected on the next
   `GET`, and the GUI could show a saved transition that no longer existed.
   Fixed by excluding `/api/songsets` from the catalog matcher, with the
   songset GET now sending `Cache-Control: no-store` as a second layer.
   Regressions: `src/test/lib/offline/api-route-matching.test.ts` (routes are
   registered against a stubbed workbox and matched the way workbox does) and
   the `cache-control` assertion in `src/test/api/songsets/[id].test.ts`.

Each regression was proven by reverting its fix and watching the test fail.

## Phase B gate

Phase B needs explicit authorization immediately before exactly one initial paid
job for the real Traditional Chinese pair and, separately, one paid Try another.
Nothing paid has been started, enqueued, or queued for redispatch.
