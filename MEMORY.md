## 2026-06-25

- Completed documentation for the native Android delivery app. Added
  `delivery/android/README.md` covering prerequisites, API base URL Gradle
  properties, emulator and physical-device networking to the local webapp,
  build/test commands, release build notes, Better Auth cookie and trusted-origin
  troubleshooting, signed URL playback, and offline downloads. Root README now
  lists Android as a delivery component, and current implementation status
  records the Android app as a standalone Kotlin/Jetpack Compose client of the
  existing webapp JSON APIs.

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
