2026-06-20: R2 backup throughput investigation closed. 32-worker concurrency bump
   (commit ea397e7) regressed throughput 7.3 → 5.0 MiB/s. Reverted DEFAULT_CONCURRENCY
   to 8; kept as_completed / size-sort / tracer / range-GET diagnostic / read_timeout=300.
   Account-level R2 cap at ~7 MiB/s confirmed via --diag-range-key (ratio=2.41).
   <10 min backup goal for 12.9 GB closed as not feasible within local Python CLI
   architecture; reopening requires Cloudflare Worker backup or bucket size reduction.
   See specs/admin-r2-backup-throughput-remediation-v2.md.
