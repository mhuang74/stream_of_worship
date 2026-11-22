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
| Webapp full | `pnpm test` | 206 files, 3526 passed, 3 skipped, 10 todo |
| Webapp lint | `pnpm lint` | 0 errors (13 pre-existing warnings) |
| Webapp typecheck | `pnpm typecheck` | clean |
| Webapp build | `SOW_DATABASE_URL=… pnpm build` | succeeded, 111 s |
| Worker full | `SKIP_DOCKER_TESTS=1 uv run --extra dev pytest tests/ -q` | 1122 passed, 8 skipped |
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
SSE reconnect after navigation, an actual 202 job with phase progression,
failed Try-another preserving the take, free staging recovery, unknown-outcome
copy, take audition in Preview/Listen, free restore of an earlier take, and
stale-draft revert. The automated journeys cover the same contracts with
injected transports; the browser surfaces need a fake-provider server mode
that does not exist in the deployment (and `test_deploy_glue.py` asserts the
deploy scripts never select one).

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

Each regression was proven by reverting its fix and watching the test fail.

## Phase B gate

Phase B needs explicit authorization immediately before exactly one initial paid
job for the real Traditional Chinese pair and, separately, one paid Try another.
Nothing paid has been started, enqueued, or queued for redispatch.
