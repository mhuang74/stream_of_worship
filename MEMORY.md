## 2026-06-30

- Fixed Cast discovery initialization for the web playback controller by loading
  the Google Cast sender SDK with `loadCastFramework=1` and resolving the
  Default Media Receiver from `chrome.cast.media.DEFAULT_MEDIA_RECEIVER_APP_ID`.
  Updated the Cast SDK ambient types and hook/loader tests to match Chrome's
  real namespace, refreshed graphify output, and verified `pnpm --filter
  sow-webapp test`, `lint`, and `build` pass.

## 2026-06-29

- Updated Worship Playback lyrics pullup title behavior: song title clicks now
  expand that song's lyrics instead of seeking, while lyric line clicks remain
  the only jump action. The expanded song follows the current playback song
  until the user explicitly browses another song during the open pullup session;
  current-song and current-line highlighting stay tied to actual playback.
  Removed the unused `onJumpToChapter` path from `ControllerPlayer`, updated
  iOS helper copy, refreshed focused `LyricJumpList` / `ControllerPlayer`
  coverage, and refreshed graphify output. Focused tests and lint pass; the
  full webapp test command is still blocked by missing `@upstash/ratelimit` in
  `src/lib/rate-limit.ts`.

## 2026-06-25

- Shipped Consolidated Chromecast Projection v3 (`.dex/plan.md`, tasks 1-12).
  Cast Web Sender SDK (Default Media Receiver only) drives TV playback from
  Android Chrome; a dev-only W3C Presentation API fallback remains for
  browser-to-browser projection. The logged-in phone mints a 4-hour R2
  presigned MP4 URL (`/api/signed-url?cast=true` or `/api/share/[token]`,
  `CAST_PLAYBACK_EXPIRES_IN_SECONDS=14400`) and hands it to the TV receiver,
  which only hits R2. Render worker now appends `-movflags +faststart` so the
  `moov` atom precedes `mdat` (Cast-compatible progressive playback), guarded
  by an `ffprobe` pipeline test. `useCastTransport` implements latest-wins
  seek debounce, extrapolated disconnect-resume with a stale (>60s) prompt,
  buffering tracking, and best-effort telemetry to `POST /api/log-client-error`
  (Upstash token-bucket rate-limited, PII-redacted, `client_error_log`
  table). `ControllerPlayer` surfaces a buffering chip, a no-Cast diagnostic
  bottom sheet, tap-to-resume on `play()` rejection, and an iPhone AirPlay
  hint. `PrePlayCard` no longer owns any Cast/Presentation detection. Docs
  (README.md, DEPLOY-VERCEL.md, `.env.production.example`,
  `docs/deployment-plan-webapp*.md`) cover Default Media Receiver, iPhone
  fallback, long-URL policy, pre-service network test, Presentation API
  dev-only label, faststart, and a 10-point Live-Service Go/No-Go Checklist.
- Completed documentation for the native Android delivery app. Added
  `delivery/android/README.md` covering prerequisites, API base URL Gradle
  properties, emulator and physical-device networking to the local webapp,
  build/test commands, release build notes, Better Auth cookie and trusted-origin
  troubleshooting, signed URL playback, and offline downloads. Root README now
  lists Android as a delivery component, and current implementation status
  records the Android app as a standalone Kotlin/Jetpack Compose client of the
  existing webapp JSON APIs.
- Addressed PR #116 follow-up review feedback for Android: replacement render
  confirmation now reruns validation before enqueueing, failed songset deletes
  restore the prior server total for paginated lists, and blank descriptions send
  an explicit clearing value. Added focused ViewModel regressions and refreshed
  graphify output.

## 2026-06-20

- Completed the ops/delivery/lab repo reorganization. New layout: `ops/admin-cli`
  owns `stream_of_worship.admin` and shared `stream_of_worship.db` helpers
  (including former `stream_of_worship.app.db` as `stream_of_worship.db.app`);
  `ops/analysis-service` owns `sow_analysis`; `delivery/webapp` owns
  `sow-webapp`; `delivery/render-worker` owns `sow_render_worker`; lab code
  lives in `lab/sow-app`, `lab/legacy-cli-tui`, and `lab/poc-scripts`.
  Root Python `pyproject.toml` and `uv.lock` were removed in favor of
  per-subproject locks. Canonical commands now use `uv --project`, e.g.
  `uv run --project ops/admin-cli --extra admin sow-admin --help`,
  `uv run --project lab/sow-app sow-app --help`, and
  `uv run --project lab/legacy-cli-tui stream-of-worship --help`.

- R2 backup throughput investigation closed. 32-worker concurrency bump (commit ea397e7) regressed throughput 7.3 → 5.0 MiB/s. Reverted DEFAULT_CONCURRENCY to 8; kept as_completed / size-sort / tracer / range-GET diagnostic / read_timeout=300. Account-level R2 cap at ~7 MiB/s confirmed via --diag-range-key (ratio=2.41). <10 min backup goal for 12.9 GB closed as not feasible within local Python CLI architecture; reopening requires Cloudflare Worker backup or bucket size reduction. See specs/admin-r2-backup-throughput-remediation-v2.md.

- **R2 backup rclone path benchmarked and REJECTED.** Per specs/admin-r2-backup-rclone-download-v1.md Step 1c, ran mandatory pre-implementation benchmarks: boto3 single conn = 7.85 MiB/s, rclone single file = 4.07 MiB/s, rclone multi-file (8 transfers) = 5.68 MiB/s. rclone achieves only 0.52×–0.72× the boto3 baseline, well below the 1.2× proceed threshold. The cap is confirmed R2-account-level, not boto3-specific. Fixes 1-7 from the spec are NOT implemented. Full results recorded in reports/admin-r2-backup-rclone-download-v1-results.md.

- R2 backup default concurrency reverted 8 → 1. The v1 rclone benchmark
  (reports/admin-r2-backup-rclone-download-v1-results.md) confirmed a single
  connection now saturates the R2 account-level cap: boto3 single-conn
  7.85 MiB/s vs 4-range parallel 6.54 MiB/s (ratio=0.83, parallel HURTS;
  v2 trace had ratio=2.41). Tightened --concurrency max 64 → 5 and
  max_pool_connections 64 → 5 to match. Concurrency machinery
  (ThreadPoolExecutor, as_completed, size-sort, BackupTracer,
  range_get_throughput_diag) retained for experimentation; run
  --diag-range-key before raising --concurrency above 1.


- Addressed PR #104 review feedback by hardening LRC language/script mismatch warnings against missing or non-string lyric payloads, preserving the legacy `lyrics.lrc` R2 alias for renderers, and adding regression coverage.
- Implemented `catalog-insert-youtube-v2` for the admin CLI.
- Added curated `sow-admin catalog insert`, `catalog edit`, `catalog quarantine`, `catalog restore`, and `catalog list --deleted` flows.
- Added reviewed YouTube metadata/transcript drafting plus shared song ID and lyrics normalization helpers.
- Refactored the YouTube audio import path so `catalog insert --youtube` reuses the `audio download` core behavior.

## 2026-06-16

- Fixed render-worker lyric fade colors to composite faded text over the active template background instead of scaling RGB toward black; added focused `test_frame_renderer.py` coverage and refreshed graphify output.

## 2026-06-17

- Addressed PR #108 second-round review feedback for admin maintenance: DB-first soft-delete purges with R2 failure reporting, stricter R2 prefix normalization, orphan limit-after-filtering, repair manifest guard, same-hash force-import refresh handling, failed render job timestamp formatting, focused tests, and refreshed graphify output.
