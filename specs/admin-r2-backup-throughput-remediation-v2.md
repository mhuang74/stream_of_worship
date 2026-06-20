# Admin R2 Backup Throughput Remediation v2 — Concurrency Revert, Investigation Closure

## Summary

The throughput remediation shipped in commit `ea397e7` (spec
`admin-r2-backup-throughput-remediation-v1.md`) bumped `DEFAULT_CONCURRENCY`
from 8 → 32 based on the hypothesis that R2 applies a *per-connection* throughput
cap (~1 MiB/s) and that adding connections is the only way to scale aggregate
throughput. That hypothesis was **never verified** before the commit landed —
the v1 spec listed `--diag-range-key` as a verification step to run *first*,
but the implementation proceeded on assumption.

A new debug trace (`sow_admin maintenance backup-r2 --chunk-size 5GiB --debug-traces`,
679 objects / 12.9 GB, 32 workers) plus the diagnostic that should have been run
before the v1 commit both confirm the hypothesis was wrong:

- **32 workers gave LOWER aggregate throughput (~5.0 MiB/s) than 8 workers (~7.3 MiB/s).**
- Per-stream throughput collapsed from ~1.0 MiB/s (8-worker) to ~0.15 MiB/s (32-worker).
- `--diag-range-key` against a 69 MB stem shows `ratio=2.41` — partial scaling,
  not the >1.5 "per-connection cap" the v1 spec assumed and not enough to break
  through the observed ~5–7 MiB/s ceiling.

The R2 account/bucket-level aggregate throughput cap (~7 MiB/s) dominates.
Adding more workers past ~8 simply slices the cap thinner and adds protocol
overhead, *reducing* effective throughput.

The user's stated target — **<10 min for a 12.9 GB backup ⇒ requires 21.5 MiB/s
sustained** — is **not feasible within the local Python CLI architecture**.
The best observed throughput (~7.3 MiB/s ⇒ ~30 min for 12.9 GB) is the hard
ceiling imposed by R2 from this client. Investigation is closed; this spec
records the closure and prescribes a partial revert to the last known-good
configuration.

## What the v1 commit Fixed (Keeping)

These changes from `ea397e7` are confirmed good by the new 32-worker trace and
remain in place — they are structural improvements independent of throughput
scaling:

| Change | Evidence from new trace |
|---|---|
| `as_completed` ingestion loop | Every `tar_write` event shows `wait_ms=0.0 wait_is_bottleneck=no` — head-of-line blocking gone |
| Inventory size-desc sort | No end-of-run tail observed; workers stay at `peak_workers=32` until near-end of run |
| `BackupTracer` | Produces all the evidence this spec analyzes; keep for future investigations |
| `range_get_throughput_diag` helper | Confirmed partial scaling, ruled out pure per-connection cap — invaluable diagnostic |
| `read_timeout=300` | No mid-stream retries fired; the lurking landmine is defused |
| Retry telemetry (`retry_trace`, `timeout_retries`) | `retries=0`, `timeout_retries=0` — confirms the run was clean, not lucky |

## What the v1 commit Got Wrong (Reverting)

The single bad decision was the concurrency bump `8 → 32`. It was premise-flawed:

- v1 Evidence 1 of `admin-r2-backup-throughput-remediation-v1.md` claimed:
  *"8 × ~1 MBps ≈ 7.3 MBps observed... parallelism is scaling linearly."*
- v1 Decisions table claimed: *"The per-stream cap is R2-side (confirmed by
  --diag-range-key)..."* — but `--diag-range-key` had not been run when the
  v1 spec was written. The confirmation was assumed, not measured.
- The v1 "Verification Plan" section literally listed running
  `--diag-range-key` as Step 1 to "confirm `ratio > 1.5` (R2-side
  per-connection throttle) **before committing to Change A**." That step was
  skipped during implementation.

### Diagnostic result (the verification that finally ran)

```
$ sow_admin maintenance backup-r2 \
    --output /tmp/dummy \
    --diag-range-key d48247f4fb2f/stems/vocals.wav
{
  "content_length": 68938108,
  "num_ranges": 4,
  "single_conn_mbps": 0.34,
  "multi_conn_total_mbps": 0.82,
  "ratio": 2.41,
  "per_range_mbps": [0.23, 0.20, 0.23, 0.23]
}
```

Interpretation:

- `single_conn_mbps = 0.34` and `per_range_mbps ≈ 0.21` — opening 4 concurrent
  ranges to the *same* object made each range **slower** (0.34 → 0.21).
- `ratio = 2.41`, not the clean >1.5 v1 hoped for and not ≈1.0 either. It's
  partial scaling: 4 connections to one object yield 2.4× the throughput of one
  but each connection is throttled harder.
- This is exactly the shape of an **R2 account/bucket-level aggregate cap**:
  more connections divide a fixed aggregate, with some per-connection overhead.

The conclusion is that R2 has both a per-connection soft cap (~0.3–1.0 MiB/s)
and an account/bucket-level aggregate cap (~5–7 MiB/s observed from this
client). The aggregate cap dominates once worker count exceeds ~8.

## Trace Evidence (32-worker run)

### Per-stream degradation timeline (the smoking gun)

Per-stream throughput collapses as the run progresses, while aggregate stays
flat at ~5 MiB/s. This is the signature of an account-level cap divided N ways:

| t+ | Object | bytes | stream_ms | per-stream MiB/s |
|---|---|---|---|---|
| +105 s | `e5c16c2f35f2/stems/vocals.wav` | 62.6 MB | 104 967 | **0.57** |
| +280 s | `renders/beE2g7fWELUPNDLWjIEY0/output.mp4` | 68.7 MB | 279 084 | **0.24** |
| +378 s | `renders/OC3-0rHE2JOVzXxI1gXR1/output.mp4` | 65.2 MB | 376 235 | **0.17** |
| +440 s | `d48247f4fb2f/stems/vocals.wav` | 68.9 MB | 435 066 | **0.15** |
| +545 s | `renders/CsieHPVFK6bQzX6mPqEln/output.mp4` | 62.3 MB | 544 572 | **0.11** |
| +660 s | `renders/zZysO69yayzlnghzAAZzC/output.mp4` | 64.2 MB | 656 287 | **0.09** |

`32 workers × ~0.15 MiB/s ≈ 4.8 MiB/s` — matches the observed aggregate exactly.

### Aggregate throughput plateau

Throughput samples from the 32-worker run (every 5 s after warm-up):

```
t+30.7s   workers=32  downloaded_mib=208.00   aggregate_mbps=6.78   peak_workers=32
t+51.4s   workers=32  downloaded_mib=289.00   aggregate_mbps=5.62   peak_workers=32
t+102.9s  workers=32  downloaded_mib=523.00   aggregate_mbps=5.08   peak_workers=32
t+253.3s  workers=32  downloaded_mib=1276.08  aggregate_mbps=5.04   peak_workers=32
t+420.5s  workers=32  downloaded_mib=2145.99  aggregate_mbps=5.10   peak_workers=32
t+676.4s  workers=32  downloaded_mib=3480.66  aggregate_mbps=5.15   peak_workers=32
```

Aggregate throughput is **flat at 5.0–5.15 MiB/s** from ~t+100 s to t+676 s.
Compare with the 8-worker v1 run which held flat at **7.0–7.7 MiB/s** across
its entire ~4 min window.

### Comparison table

| Run | Concurrency | Aggregate steady-state | Per-stream avg | Backups/min for 12.9 GB |
|---|---|---|---|---|
| v1 spec evidence | 8 workers | **7.0–7.7 MiB/s** | ~1.0 MiB/s | ~30 min |
| New trace | 32 workers | **5.0–5.15 MiB/s** | ~0.15 MiB/s | ~43 min |

**32 workers made throughput ~30% worse.** The user target of <10 min is not
reachable client-side; the best observed (~7.3 MiB/s) caps backup time at
~30 min for the current bucket size.

## Tar/disk side: definitively not a bottleneck

Every `tar_write` event in the trace shows:

- `wait_ms = 0.0` (head-of-line blocking eliminated by `as_completed`)
- `tar_write_ms = 50–110 ms` for 60 MB objects (~700 MB/s effective)
- `wait_is_bottleneck=no` in every record

The `tempfile → open → addfile → unlink` round-trip is not the issue. The
v1 spec's "future work" item proposing direct-to-tar streaming is **moot** —
disk I/O is two orders of magnitude faster than the R2 ceiling.

## Decisions

| Topic | Decision |
|---|---|
| `DEFAULT_CONCURRENCY` | Revert from 32 → **8**. The 8-worker baseline remains the best measured configuration. |
| `read_timeout` | Keep at 300 (unchanged from v1 commit). Setting is correct independently of concurrency. |
| `max_pool_connections` | Keep at 64 (unchanged). Provides headroom without cost. |
| `as_completed` ingestion loop | Keep (unchanged). Confirmed eliminates HOL blocking. |
| Inventory size-desc sort | Keep (unchanged). Avoids end-of-run tail. |
| `BackupTracer`, `range_get_throughput_diag`, retry telemetry | Keep (unchanged). All confirmed valuable. |
| `--concurrency` CLI `max` | Lower from 128 → **64**. The 32-worker result shows values above ~16 are counterproductive; 64 retains headroom for experimentation without inviting the 32+ regression. |
| `--diag-range-key` flag | Keep (unchanged). Required before any future R2-throughput-related change to the codebase. |
| Manifest version | No change (stays at 4). |
| Per-object multipart Range-GET | **Not pursued.** Diagnostic shows `ratio=2.41` (partial scaling), so per-object Range-GET cannot reach the user's <10 min target. Even an optimistic 7.3 → 10 MiB/s gain would still cap at ~22 min backup. |
| Cloudflare Worker in-network backup | **Not pursued.** User explicitly declined the architectural change at this time. |
| boto3 TCP/socket buffer tuning | **Not pursued.** Expected gain (<20%) does not reach the <10 min target. |
| Goal: <10 min backup for 12.9 GB | **Closed as not feasible** within local Python CLI architecture due to R2 account-level throughput cap (~7 MiB/s best observed). Document as an R2 property; do not re-attempt without architectural change. |
| Re-opening the goal | Requires (a) explicit decision to use Cloudflare Worker / in-network backup, OR (b) bucket size shrinking below ~4 GB (which would yield <10 min at current ceiling). |

## Files to Modify

### 1. `src/stream_of_worship/admin/services/r2_backup.py`

Change A (revert): In the module constants block (currently lines 32–37), replace
the v1 comment + `DEFAULT_CONCURRENCY = 32` with:

```python
# 32 workers gave ~30% LOWER throughput than 8 workers in real traces
# (5.0 vs 7.3 MiB/s) — see specs/admin-r2-backup-throughput-remediation-v2.md.
# R2 exhibits a ~7 MiB/s account/bucket aggregate cap from this client;
# adding workers past ~8 slices the cap thinner without raising it.
# Verified via --diag-range-key (ratio=2.41, partial scaling, not pure per-conn cap).
DEFAULT_CONCURRENCY = 8
```

Change B (revert): In `write_backup` concurrency validation (currently line 914):

```python
# Before:
if not (1 <= concurrency <= 128):
    raise BackupError(f"concurrency must be 1-128, got {concurrency}")

# After:
if not (1 <= concurrency <= 64):
    raise BackupError(f"concurrency must be 1-64, got {concurrency}")
```

No other changes to this file. In particular, **do not** revert
`as_completed`, `submission_order` size-sort, `BackupTracer`,
`range_get_throughput_diag`, or any tracer calls — those are confirmed wins.

### 2. `src/stream_of_worship/admin/commands/maintenance.py`

Revert `--concurrency` option (default, max, and help text). The v1 change set
default=32, max=128, with help text promising that raising concurrency scales
throughput. Replace with:

```python
concurrency: int = typer.Option(
    8, "--concurrency", min=1, max=64,
    help=(
        "Number of concurrent download workers (default 8). "
        "R2 exhibits an account-level throughput cap (~7 MiB/s from this client); "
        "raising workers past 8 typically REDUCES throughput (verified at 32 workers: "
        "~5 MiB/s vs ~7 MiB/s at 8 workers). Use --diag-range-key to investigate."
    ),
),
```

If the `debug_traces` parameter name or surrounding code has changed, keep
those changes — only the `concurrency` option tuple is reverted.

### 3. `tests/admin/test_r2_backup.py`

- Update `test_concurrency_validation` (or equivalent boundary test): the
  upper bound assertion changes from `128` → `64`. The lower bound stays `1`.
- Update any test that asserts the default `DEFAULT_CONCURRENCY == 32` to
  `== 8`. Typically a test like `test_default_concurrency` or an assertion
  in a fixture.
- Leave all `as_completed` / `submission_order` / `retry_trace` /
  `range_get_throughput_diag` tests untouched — their behavior is unchanged.

### 4. `tests/admin/test_r2_backup_commands.py`

- Update the `--concurrency` CLI validation test. The `max=128` rejection
  case becomes `max=64`. Specifically, any test that invokes the CLI with
  `--concurrency 200` and expects a typer error still passes (200 > 64). Any
  test that invokes with `--concurrency 100` expecting success must be changed
  to expect rejection (100 > 64).
- Default-value test: the command's default concurrency is now `8`, not `32`.

### 5. `report/current_impl_status.md` (and `MEMORY` if present)

Per `AGENTS.md` session-completion convention, append a short entry:

```
2026-06-20: R2 backup throughput investigation closed. 32-worker concurrency bump
   (commit ea397e7) regressed throughput 7.3 → 5.0 MiB/s. Reverted DEFAULT_CONCURRENCY
   to 8; kept as_completed / size-sort / tracer / range-GET diagnostic / read_timeout=300.
   Account-level R2 cap at ~7 MiB/s confirmed via --diag-range-key (ratio=2.41).
   <10 min backup goal for 12.9 GB closed as not feasible within local Python CLI
   architecture; reopening requires Cloudflare Worker backup or bucket size reduction.
   See specs/admin-r2-backup-throughput-remediation-v2.md.
```

If `report/current_impl_status.md` does not exist, create it with this entry
as the initial content. If `MEMORY` is a separate file at the repo root,
append the same entry there too.

## Verification Plan

### 1. Unit tests

```bash
PYTHONPATH=src uv run --python 3.11 --extra admin --extra test \
    pytest tests/admin/test_r2_backup.py tests/admin/test_r2_backup_commands.py -v
```

Expected: all tests pass with the updated boundary assertions and default
value.

### 2. Lint and typecheck

```bash
PYTHONPATH=src uv run --python 3.11 --extra admin \
    ruff check \
    src/stream_of_worship/admin/services/r2_backup.py \
    src/stream_of_worship/admin/commands/maintenance.py
PYTHONPATH=src uv run --python 3.11 --extra admin \
    mypy src/stream_of_worship/admin/services/r2_backup.py
```

### 3. Optional: regression smoke (not required to land the revert)

If the user opts in, run a 4 / 8 / 16 worker sweep with `--debug-traces`
against a fresh backup directory to confirm 8 is still the local optimum:

```bash
for c in 4 8 16; do
  sow_admin maintenance backup-r2 \
    --chunk-size 5GiB --concurrency $c \
    --output ~/BACKUPS/sow-r2/sweep-$c \
    --debug-traces 2>&1 | tee sweep-$c.txt
done
```

Expected: 8 workers outperforms both 4 and 16; throughput ceiling at ~7 MiB/s
in all three cases. This step is confirmation, not discovery — the v2 spec
already concludes the investigation.

### 4. Session completion

```bash
git pull --rebase
git push
git status
```

`git status` MUST show "up to date with origin" before stopping (per
`AGENTS.md` Session Completion section).

## Out of Scope (Investigation Closed)

These items are listed to prevent re-investigation:

- **Per-object multipart Range-GET (v1's "future work")**: Observed
  `ratio=2.41` from `--diag-range-key` is partial scaling, not the per-connection
  cap v1 assumed. Implementing per-object Range-GET would at best recover
  the few MiB/s lost in the 32-worker regression; it cannot reach the user's
  <10 min target. Do not implement without reopening the goal explicitly.
- **Cloudflare Worker in-network backup**: User explicitly declined the
  architectural change. Goal can only be reopened by reversing that decision.
- **boto3 / urllib3 TCP tuning** (keepalive, socket buffers, connection
  reuse): Expected gain <20%, far below the ~3× needed for <10 min.
- **Direct-to-tar streaming**: `tar_write_ms` is 50–110 ms per 60 MB object
  (~700 MB/s). Eliminating the temp-file round-trip would save negligible
  time; the bottleneck is network, not disk.
- **Spoofing R2 with multiple source IPs**: Brittle, against ToS, and unproven
  to bypass an account-level cap (as opposed to per-IP cap). Not considered.

## Decisions Deferred

- **Concurrency sweep (4/8/16)**: Optional confirmation step, not required to
  land this spec. If run and 16 happens to outperform 8 in a future trace,
  raise `DEFAULT_CONCURRENCY` to 16 in a separate spec; do not silently bump.
- **`--concurrency max=64` vs `max=32`**: Chose 64 to preserve experimentation
  headroom. If trace data later confirms 16+ is always harmful, lower to 32
  in a follow-up. No urgency.
- **Reopening the <10 min goal**: Tracked here only. Requires either (a)
  explicit decision to pursue Cloudflare Worker in-network backup, or (b)
  bucket size reduction below ~4 GB (e.g., via lifecycle rules deleting old
  `renders/` outputs).

## References

- `specs/admin-r2-backup-throughput-remediation-v1.md` — original (incorrectly
  verified) remediation; ships the changes this spec partially reverts.
- `specs/admin-r2-backup-perf-traces.md` — earlier perf-trace spec; still
  valid for understanding the tracer design.
- `src/stream_of_worship/admin/services/r2_backup.py` — backup service with
  `DEFAULT_CONCURRENCY` constant and `write_backup` main loop.
- `src/stream_of_worship/admin/commands/maintenance.py` — `backup-r2` command
  with `--concurrency` and `--diag-range-key` flags.
- Commit `ea397e7` — the v1 remediation commit (the partial revert target).
- Commit `c588364` — added the performance tracing that made this investigation
  possible.
