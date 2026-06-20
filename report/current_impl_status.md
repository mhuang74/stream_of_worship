2026-06-20: R2 backup throughput investigation closed. 32-worker concurrency bump
   (commit ea397e7) regressed throughput 7.3 → 5.0 MiB/s. Reverted DEFAULT_CONCURRENCY
   to 8; kept as_completed / size-sort / tracer / range-GET diagnostic / read_timeout=300.
   Account-level R2 cap at ~7 MiB/s confirmed via --diag-range-key (ratio=2.41).
   <10 min backup goal for 12.9 GB closed as not feasible within local Python CLI
   architecture; reopening requires Cloudflare Worker backup or bucket size reduction.
   See specs/admin-r2-backup-throughput-remediation-v2.md.

2026-06-20: R2 backup default concurrency reverted 8 → 1. The v1 rclone benchmark
   (reports/admin-r2-backup-rclone-download-v1-results.md) confirmed a single
   connection now saturates the R2 account-level cap: boto3 single-conn
   7.85 MiB/s vs 4-range parallel 6.54 MiB/s (ratio=0.83, parallel HURTS;
   v2 trace had ratio=2.41). Tightened --concurrency max 64 → 5 and
   max_pool_connections 64 → 5 to match. Concurrency machinery
   (ThreadPoolExecutor, as_completed, size-sort, BackupTracer,
   range_get_throughput_diag) retained for experimentation; run
   --diag-range-key before raising --concurrency above 1.
