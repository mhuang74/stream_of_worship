## 2026-06-20

- R2 backup throughput investigation closed. 32-worker concurrency bump (commit ea397e7) regressed throughput 7.3 → 5.0 MiB/s. Reverted DEFAULT_CONCURRENCY to 8; kept as_completed / size-sort / tracer / range-GET diagnostic / read_timeout=300. Account-level R2 cap at ~7 MiB/s confirmed via --diag-range-key (ratio=2.41). <10 min backup goal for 12.9 GB closed as not feasible within local Python CLI architecture; reopening requires Cloudflare Worker backup or bucket size reduction. See specs/admin-r2-backup-throughput-remediation-v2.md.


- Addressed PR #104 review feedback by hardening LRC language/script mismatch warnings against missing or non-string lyric payloads, preserving the legacy `lyrics.lrc` R2 alias for renderers, and adding regression coverage.
- Implemented `catalog-insert-youtube-v2` for the admin CLI.
- Added curated `sow-admin catalog insert`, `catalog edit`, `catalog quarantine`, `catalog restore`, and `catalog list --deleted` flows.
- Added reviewed YouTube metadata/transcript drafting plus shared song ID and lyrics normalization helpers.
- Refactored the YouTube audio import path so `catalog insert --youtube` reuses the `audio download` core behavior.

## 2026-06-16

- Fixed render-worker lyric fade colors to composite faded text over the active template background instead of scaling RGB toward black; added focused `test_frame_renderer.py` coverage and refreshed graphify output.

## 2026-06-17

- Addressed PR #108 second-round review feedback for admin maintenance: DB-first soft-delete purges with R2 failure reporting, stricter R2 prefix normalization, orphan limit-after-filtering, repair manifest guard, same-hash force-import refresh handling, failed render job timestamp formatting, focused tests, and refreshed graphify output.
