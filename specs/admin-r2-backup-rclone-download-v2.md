# Admin R2 Backup rclone Download Path v2 — Default Concurrency Revert to Single-Threaded

**Service:** Admin CLI (`src/stream_of_worship/admin/`)
**Status:** Plan only — no implementation
**Created:** 2026-06-20
**Predecessor specs:**
- `specs/admin-r2-backup-rclone-download-v1.md` (rclone benchmark — abandoned)
- `specs/admin-r2-backup-throughput-remediation-v2.md` (closure of v1 throughput investigation)
**CLI command:** `sow-admin maintenance backup-r2`

## Summary

The v1 rclone benchmark (`reports/admin-r2-backup-rclone-download-v1-results.md`)
returned STOP: rclone does not outperform boto3, and the ~7 MiB/s ceiling is
confirmed to be R2-account-level. Critically, the benchmark produced a **new
data point** that supersedes the v2 remediation spec's `ratio=2.41` finding:

- `--diag-range-key` now reports `ratio=0.83` (down from 2.41 in the v2 trace).
- A single boto3 connection achieved **7.85 MiB/s**, while 4 parallel
  Range-GETs achieved only **6.54 MiB/s** aggregate. Parallel connections
  now *hurt* throughput — the network path is saturated by a single connection.
- Best observed across all backends: **single-connection boto3 at 7.85 MiB/s**.

This spec reverts `DEFAULT_CONCURRENCY` from `8 → 1` and tightens the
`--concurrency` upper bound from `64 → 5` (matching `max_pool_connections` on
the boto3 client). The ThreadPoolExecutor / `as_completed` / size-desc
submission sort / `BackupTracer` / `range_get_throughput_diag` machinery
remains intact for experimentation; the default simply no longer pays
concurrency overhead that the data shows buys nothing.

**Success metric:** default backup run uses 1 worker; throughput matches or
exceeds the 8-worker baseline; all existing tests pass with updated boundary
assertions.

## Background

### Why the v2 remediation spec set DEFAULT_CONCURRENCY=8

The v2 remediation spec (`specs/admin-r2-backup-throughput-remediation-v2.md`)
reverted the v1 bump of `8 → 32` based on a 32-worker trace showing 5.0 MiB/s
vs the 8-worker baseline's 7.3 MiB/s. At the time, the `--diag-range-key`
diagnostic returned `ratio=2.41` — interpreted as "partial scaling, not pure
per-connection cap" — so 8 workers was retained as a hedge: if there was any
per-connection cap component, 8 workers might still overlap downloads with
tar writes usefully.

### What the v1 rclone benchmark changed

The v1 rclone benchmark re-ran `--diag-range-key` against the same object
(`d48247f4fb2f/stems/vocals.wav`, 65.745 MiB) from the same client/network
path. The result:

```json
{
  "content_length": 68938108,
  "num_ranges": 4,
  "single_conn_mbps": 7.85,
  "multi_conn_total_mbps": 6.54,
  "ratio": 0.83,
  "per_range_mbps": [1.77, 3.81, 1.71, 1.64]
}
```

**Interpretation** (per `reports/admin-r2-backup-rclone-download-v1-results.md`):

- `ratio = 0.83` (< 1.0) means parallel connections now *reduce* aggregate
  throughput. This is the opposite shape of the v2 trace's `ratio=2.41`.
- Network conditions have shifted: the path is now saturated by a single
  connection, leaving no headroom for parallel ranges to add throughput.
- The single-connection baseline (7.85 MiB/s) matches the 8-worker multi-file
  steady-state (7.0–7.7 MiB/s) from the v2 trace — confirming concurrency
  buys ~0 aggregate throughput.

### What concurrency was buying (and no longer is)

The only theoretical benefit of `concurrency > 1` is overlapping the download
of object N+1 with the tar-write of object N. The v2 trace's `tar_write`
events show `tar_write_ms = 50–110 ms` per 60 MB object (~700 MB/s effective
disk I/O), vs ~10 s per object download. Overlap saves <1% of wall time.
Under the new `ratio=0.83` regime, concurrency *costs* ~17% of aggregate
download throughput — far more than the <1% overlap saves.

### Comparative data (from v1 rclone benchmark report)

| Backend | Throughput (MiB/s) | vs boto3 single conn |
|---|---|---|
| boto3 single conn (diag) | **7.85** | 1.00× (baseline, best observed) |
| boto3 multi conn (4 Range-GETs) | **6.54** | 0.83× |
| boto3 8-worker multi-file (v2 trace) | 7.0–7.7 | ~0.92× |
| boto3 32-worker multi-file (v2 trace) | 5.0 | 0.64× |
| rclone single file | 4.07 | 0.52× |
| rclone multi-thread streams (8) | 2.96 | 0.38× |
| rclone multi-file (8 transfers) | 5.68 | 0.72× |

## Decision & Rationale

**Approach:** Revert `DEFAULT_CONCURRENCY` to 1. Keep the concurrency
machinery intact for `--concurrency N>1` experimentation; tighten the upper
bound to 5 across the board (CLI flag, boto3 client pool, `write_backup`
validation).

### Why default=1 instead of keeping default=8

1. **Data-justified.** Single-conn (7.85 MiB/s) ≥ 8-worker multi-file
   (7.0–7.7 MiB/s) in the v2 trace, and the new `ratio=0.83` diagnostic
   shows parallelism now *hurts*. There is no measurement in favor of 8.
2. **Lower overhead.** No `ThreadPoolExecutor` spin-up, no lock contention
   on `BackupProgress`, no per-worker connection setup. Single-threaded
   mode is simpler and uses fewer resources.
3. **Consistency with `ratio=0.83`.** The diagnostic is the canonical
   measurement the v2 spec designated as the gate for concurrency decisions.
   When it returned 2.41, we kept 8. Now that it returns 0.83, the
   symmetric action is to drop to 1.
4. **Reversibility preserved.** The `--concurrency` flag remains available;
   if a future trace shows `ratio > 1.5` again (network conditions change),
   the default can be bumped back in a follow-up spec.

### Why max=5 (and not max=1 or max=8)

- **Not max=1:** Hardcoding `concurrency=1` would prevent anyone from
  re-running the `--diag-range-key` benchmark (which defaults to
  `num_ranges=4`) or experimenting with small N if network conditions
  change. Preserves the diagnostic that produced all the evidence this
  spec relies on.
- **Not max=8:** The v2 remediation spec kept `max=64` "to preserve
  experimentation headroom," but the v1 rclone benchmark shows values
  above 8 are *consistently* counterproductive (32 workers → 5.0 MiB/s,
  0.64× baseline). Tightening to 5 documents the empirical ceiling while
  leaving 1–5 available for diagnostic use.
- **max=5 specifically:** Fits the `--diag-range-key` default of
  `num_ranges=4` plus 1 spare connection headroom. Keeps the boto3
  `max_pool_connections` and the CLI `--concurrency max` aligned — no
  silent queuing when the user experiments.

## Implementation Plan

### Change 1: `DEFAULT_CONCURRENCY` revert (P0)

**File:** `src/stream_of_worship/admin/services/r2_backup.py`

**Location:** Module constants block, currently lines 32–37.

**Before (current):**

```python
# 32 workers gave ~30% LOWER throughput than 8 workers in real traces
# (5.0 vs 7.3 MiB/s) — see specs/admin-r2-backup-throughput-remediation-v2.md.
# R2 exhibits a ~7 MiB/s account/bucket aggregate cap from this client;
# adding workers past ~8 slices the cap thinner without raising it.
# Verified via --diag-range-key (ratio=2.41, partial scaling, not pure per-conn cap).
DEFAULT_CONCURRENCY = 8
```

**After:**

```python
# Concurrency past 1 buys ~0 aggregate throughput. R2 exhibits an
# account/bucket-level cap (~7 MiB/s from this client); a single connection
# now saturates it. The v1 rclone benchmark confirmed this:
#   boto3 single-conn  7.85 MiB/s (best observed)
#   boto3 4-range     6.54 MiB/s  (ratio=0.83, parallel HURTS)
#   boto3 32-worker   5.0  MiB/s  (v2 trace, 30% regression)
#   rclone 8-transfer 5.68 MiB/s  (slower than single boto3 conn)
# See reports/admin-r2-backup-rclone-download-v1-results.md and
# specs/admin-r2-backup-throughput-remediation-v2.md.
# --concurrency N>1 remains available for experimentation but is not expected
# to help; run --diag-range-key first.
DEFAULT_CONCURRENCY = 1
```

**Rationale:** The comment cites the v1 rclone benchmark (the new canonical
evidence) and the v2 remediation spec (for the 32-worker regression data
point). Future readers will see *why* 1 was chosen and *where* to look if
they want to re-investigate.

---

### Change 2: `write_backup` validation upper bound (P0)

**File:** `src/stream_of_worship/admin/services/r2_backup.py`

**Location:** `write_backup` function, currently line 914–915.

**Before:**

```python
if not (1 <= concurrency <= 64):
    raise BackupError(f"concurrency must be 1-64, got {concurrency}")
```

**After:**

```python
if not (1 <= concurrency <= 5):
    raise BackupError(f"concurrency must be 1-5, got {concurrency}")
```

**Rationale:** Brings the programmatic API in line with the CLI `max=5`
(Change 3) and the boto3 pool size (Change 4). Keeps the lower bound at 1
to preserve the existing `test_concurrency_1_works` test.

---

### Change 3: `--concurrency` CLI option (P0)

**File:** `src/stream_of_worship/admin/commands/maintenance.py`

**Location:** `backup_r2` Typer command, currently lines 648–656.

**Before (current):**

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

**After:**

```python
concurrency: int = typer.Option(
    1, "--concurrency", min=1, max=5,
    help=(
        "Number of concurrent download workers (default 1). "
        "R2 exhibits an account-level throughput cap (~7 MiB/s from this client); "
        "a single connection already saturates it. The v1 rclone benchmark showed "
        "boto3 single-conn 7.85 MiB/s vs 4-range parallel 6.54 MiB/s (ratio=0.83, "
        "parallel HURTS). Use --diag-range-key to investigate before raising."
    ),
),
```

**Rationale:** Default lowered to match `DEFAULT_CONCURRENCY`; max tightened
to 5; help text rewritten to cite the v1 rclone benchmark (the newest
evidence) rather than the v2 trace (still mentioned in the module comment
but not the user-facing help).

---

### Change 4: `max_pool_connections` on boto3 client (P0)

**File:** `src/stream_of_worship/admin/services/r2.py`

**Location:** `R2Client.__init__`, currently lines 107–109.

**Before (current):**

```python
# Bump from 32 to 64 to align with new DEFAULT_CONCURRENCY=32 plus headroom
# for the diagnostic Range-GET workers.
max_pool_connections=64,
```

**After:**

```python
# Align with DEFAULT_CONCURRENCY=1 plus headroom for the
# --diag-range-key diagnostic's 4 parallel Range-GET workers.
# Higher parallelism is counterproductive under R2's account-level cap.
max_pool_connections=5,
```

**Rationale:** The old comment referenced the now-reverted
`DEFAULT_CONCURRENCY=32` and was never updated when the v2 remediation spec
reverted to 8. Lowering to 5 matches the new `--concurrency max=5` ceiling,
eliminating the inconsistency where the CLI accepted `--concurrency 64` but
the boto3 client only had 64 pool slots (silent queuing above 64). With
pool=5 and `--concurrency max=5`, every CLI-permitted value gets a real
boto3 connection slot.

**Compatibility note:** The `range_get_throughput_diag` function
(`r2_backup.py:788-870`) defaults to `num_ranges=4`. With
`max_pool_connections=5`, the diagnostic's 4 parallel workers fit with 1
spare slot. No change to the diagnostic's default.

---

### Change 5: Boundary test assertions (P0)

**File:** `tests/admin/test_r2_backup.py`

**Location:** `TestConcurrentBackup` class, lines 907–923.

**Before (current):**

```python
def test_concurrency_validation_rejects_zero(self, tmp_path):
    """write_backup raises BackupError for concurrency=0."""
    r2 = _make_r2_mock([])
    inventory = build_inventory(r2)
    output = tmp_path / "backup"

    with pytest.raises(BackupError, match="concurrency must be 1-64"):
        write_backup(r2, output, inventory, concurrency=0)

def test_concurrency_validation_rejects_65(self, tmp_path):
    """write_backup raises BackupError for concurrency=65."""
    r2 = _make_r2_mock([])
    inventory = build_inventory(r2)
    output = tmp_path / "backup"

    with pytest.raises(BackupError, match="concurrency must be 1-64"):
        write_backup(r2, output, inventory, concurrency=65)
```

**After:**

```python
def test_concurrency_validation_rejects_zero(self, tmp_path):
    """write_backup raises BackupError for concurrency=0."""
    r2 = _make_r2_mock([])
    inventory = build_inventory(r2)
    output = tmp_path / "backup"

    with pytest.raises(BackupError, match="concurrency must be 1-5"):
        write_backup(r2, output, inventory, concurrency=0)

def test_concurrency_validation_rejects_6(self, tmp_path):
    """write_backup raises BackupError for concurrency=6."""
    r2 = _make_r2_mock([])
    inventory = build_inventory(r2)
    output = tmp_path / "backup"

    with pytest.raises(BackupError, match="concurrency must be 1-5"):
        write_backup(r2, output, inventory, concurrency=6)
```

**Rationale:** Match strings updated from `1-64` → `1-5`; the upper-bound
rejection test renamed from `rejects_65` → `rejects_6` and its input value
changed from `65` → `6`. The lower-bound (`0`) test is unchanged in
structure, only the match string changes.

**Unchanged tests (verified still valid):**

- `test_concurrent_backup_produces_valid_archive` (line 684): calls
  `write_backup(...)` with no `concurrency` arg — now exercises
  `concurrency=1`. Produces a valid archive regardless; no value
  assertion. ✓
- `test_concurrency_1_works` (line 714): explicitly passes
  `concurrency=1`, still within `1 <= concurrency <= 5`. ✓
- `test_concurrent_backup_preserves_object_order` (line 729): passes
  `concurrency=4`, still ≤5. ✓
- Test at line 1690: passes `concurrency=4`, still ≤5. ✓

---

### Change 6: CLI test (P0, verify no assertion change needed)

**File:** `tests/admin/test_r2_backup_commands.py`

**Location:** `TestBackupR2Command.test_backup_concurrency_flag`, lines 214–236.

This test invokes the CLI with `--concurrency 4` and asserts
`mock_write.call_args.kwargs["concurrency"] == 4`. The value `4` is still
within the new `max=5` range. **No edit required — test continues to pass
as-is.**

**Search confirmed** (via grep for `concurrency.*=.*\d+` in
`tests/admin/`): no other test asserts the CLI default concurrency value.
The only test that exercises the default is
`test_concurrent_backup_produces_valid_archive` in
`tests/admin/test_r2_backup.py`, which does not assert the specific value.

---

### Change 7: Status report and MEMORY append (P1)

**File:** `report/current_impl_status.md` — append entry.

**File:** `MEMORY.md` — append entry (line 3 currently has the v2 remediation
entry; this is the natural continuation).

**Entry text (same for both files):**

```
- R2 backup default concurrency reverted 8 → 1. The v1 rclone benchmark
  (reports/admin-r2-backup-rclone-download-v1-results.md) confirmed a single
  connection now saturates the R2 account-level cap: boto3 single-conn
  7.85 MiB/s vs 4-range parallel 6.54 MiB/s (ratio=0.83, parallel HURTS;
  v2 trace had ratio=2.41). Tightened --concurrency max 64 → 5 and
  max_pool_connections 64 → 5 to match. Concurrency machinery
  (ThreadPoolExecutor, as_completed, size-sort, BackupTracer,
  range_get_throughput_diag) retained for experimentation; run
  --diag-range-key before raising --concurrency above 1.
```

**Rationale:** Per `AGENTS.md` session-completion convention, status/MEMORY
must reflect completed work. The entry cites the v1 rclone benchmark report
explicitly so future readers can trace the decision back to its evidence.

## File Change Summary

| File | Change | Priority |
|---|---|---|
| `src/stream_of_worship/admin/services/r2_backup.py` (lines 32–37) | `DEFAULT_CONCURRENCY = 8 → 1`; rewrite comment citing v1 rclone benchmark | P0 |
| `src/stream_of_worship/admin/services/r2_backup.py` (lines 914–915) | `write_backup` validation bound `64 → 5`; match string `1-64 → 1-5` | P0 |
| `src/stream_of_worship/admin/commands/maintenance.py` (lines 648–656) | `--concurrency` default `8 → 1`, max `64 → 5`; rewrite help text | P0 |
| `src/stream_of_worship/admin/services/r2.py` (lines 107–109) | `max_pool_connections = 64 → 5`; rewrite stale comment | P0 |
| `tests/admin/test_r2_backup.py` (lines 913, 916–923) | Boundary match strings `1-64 → 1-5`; rename `rejects_65 → rejects_6`, input `65 → 6` | P0 |
| `tests/admin/test_r2_backup_commands.py` (lines 214–236) | No edit — `--concurrency 4` still within new `max=5` range | n/a |
| `report/current_impl_status.md` | Append status entry per AGENTS.md | P1 |
| `MEMORY.md` | Append status entry per AGENTS.md | P1 |

**Total:** 4 source/test files edited, 2 docs appended. 1 test file
explicitly verified as needing no change.

## Files Explicitly NOT Modified

- **`specs/admin-r2-backup-*.md`** — historical specs are immutable records;
  this v2 spec documents the new decision without rewriting predecessors.
- **`poc/`, `scripts/`** — out of scope.
- **`as_completed` ingestion loop** in `write_backup` — kept. Still correct
  for `concurrency=1` (the `ThreadPoolExecutor(max_workers=1)` degenerates
  to sequential submission, and `as_completed` still yields futures in
  completion order). No behavior change.
- **Inventory size-desc submission sort** — kept. Under `concurrency=1` it
  means large objects download first, which is fine (no end-of-run tail
  either way when there's no parallelism).
- **`BackupTracer`** — kept. Still emits per-object and per-phase traces
  at DEBUG level; the `network_saturated` summary field's heuristic still
  works (its threshold is `aggregate / (single_worker_avg * peak_workers)
  < 0.5`; with `peak_workers=1` this becomes `aggregate / single_worker_avg
  < 0.5`, correctly reporting "no (or inconclusive)" for a single-threaded
  run).
- **`range_get_throughput_diag`** — kept. The diagnostic that produced all
  the evidence for this spec; must remain available for future
  re-investigation. Its default `num_ranges=4` fits within `max_pool_connections=5`.
- **Retry telemetry** (`retry_trace`, `timeout_retries`) — kept. Confirmed
  valuable in v2 trace; orthogonal to concurrency level.
- **`read_timeout=300`** — kept. Correct independently of concurrency; a
  single 50 MB stem at ~1 MiB/s still takes ~50 s, so 300 s headroom is
  still appropriate.
- **Manifest version** — no change (stays at 4).
- **`examples/sow-admin-config.toml`** — no change. The `[r2]` section does
  not document `--concurrency`; users don't need a config-file-side update.

## Verification Plan

### Step 1: Unit tests

Per `AGENTS.md` command pattern:

```bash
PYTHONPATH=src uv run --python 3.11 --extra admin --extra test \
  pytest tests/admin/test_r2_backup.py tests/admin/test_r2_backup_commands.py -v
```

**Expected:** all tests pass with the updated boundary assertions
(`1-5` match strings, `rejects_6` test name, `concurrency=6` input). The
default-concurrency test (`test_concurrent_backup_produces_valid_archive`)
produces a valid archive at `concurrency=1`; no value assertion to update.

### Step 2: Lint

```bash
PYTHONPATH=src uv run --python 3.11 --extra admin \
  ruff check \
  src/stream_of_worship/admin/services/r2_backup.py \
  src/stream_of_worship/admin/commands/maintenance.py \
  src/stream_of_worship/admin/services/r2.py
```

**Expected:** clean. No new imports, no line-length violations (all
changed lines fit within the 100-char limit per `AGENTS.md` formatting
rules).

### Step 3: Typecheck

```bash
PYTHONPATH=src uv run --python 3.11 --extra admin \
  mypy src/stream_of_worship/admin/services/r2_backup.py
```

**Expected:** clean. No type signatures changed — only integer literals
and comment strings.

### Step 4: Optional regression smoke (not required to land the revert)

If the user opts in, confirm `concurrency=1` matches `concurrency=8` on a
real backup run:

```bash
for c in 1 8; do
  sow-admin maintenance backup-r2 \
    --chunk-size 5GiB --concurrency $c \
    --output ~/BACKUPS/sow-r2/smoke-$c \
    --debug-traces 2>&1 | tee smoke-$c.txt
done
```

**Expected:** both runs produce ~7 MiB/s aggregate; the `concurrency=1`
run may even be marginally faster (no lock contention). This is
confirmation, not discovery — the v1 rclone benchmark already established
the ceiling.

If `concurrency=1` is *slower* than `concurrency=8` by more than 10%,
**stop** and investigate — the v1 benchmark may not generalize (e.g.,
per-object HEAD overhead becoming significant without overlap).

### Step 5: Session completion (per AGENTS.md)

```bash
git pull --rebase
git push
git status
```

`git status` MUST show "up to date with origin" before stopping.

## Out of Scope

- **Removing the `--concurrency` flag entirely.** User chose to keep the
  flag with `max=5` for experimentation headroom. A follow-up spec may
  remove it if the `ratio < 1.0` finding holds across multiple future
  traces.
- **Removing the `ThreadPoolExecutor` code path from `write_backup`.** The
  machinery is retained; `concurrency=1` degenerates gracefully to a
  sequential loop without code changes. Removing the executor would
  delete tested code (`as_completed`, `submission_order`) that the v2
  remediation spec explicitly kept.
- **Reopening the `<10 min for 12.9 GB` goal.** Still closed per the v2
  remediation spec; the v1 rclone benchmark confirmed the ceiling is
  account-level, not client-level. Requires Cloudflare Worker in-network
  backup or bucket size reduction.
- **Re-investigating rclone as a backend.** The v1 rclone benchmark
  returned STOP per its Step 1c decision gate. Reopening requires new
  evidence (e.g., a different R2 plan tier lifting the account-level cap,
  or a different network path).
- **boto3 `TransferConfig` tuning (use_threads, multipart_chunksize).**
  Out of scope per v1 rclone benchmark's "Out of Scope" section.
  Per-object multipart Range-GET is moot now that `ratio=0.83` shows
  parallel ranges hurt throughput.
- **Cloudflare Worker in-network backup.** Out of scope per v2 remediation
  spec; user has not reversed the architectural-change decision.
- **AIobotocore async rewrite.** Out of scope per v1 rclone benchmark's
  "Out of Scope" section.
- **Updating `specs/admin-r2-backup-throughput-remediation-v1.md` or
  `...-v2.md`.** Historical specs are immutable records; this v2 rclone
  spec documents the new decision without rewriting predecessors.

## Decisions Deferred

- **Default backend (`boto3` vs `rclone`):** boto3 remains the sole
  backend. The v1 rclone benchmark confirmed rclone is slower across all
  tested configurations; promoting rclone to default is not considered.
- **`--concurrency max=5` vs `max=3`:** Chose 5 to fit the
  `--diag-range-key` default (`num_ranges=4`) plus 1 spare. If trace data
  later confirms `concurrency >= 2` is always harmful, lower to 1 in a
  follow-up spec and remove the flag entirely.
- **`max_pool_connections=5` vs `=10`:** Chose 5 to match the CLI `max=5`
  exactly, avoiding silent queuing. If a future diagnostic needs more
  than 4 parallel ranges, bump both in lockstep.
- **Reopening the goal if `ratio` changes:** If a future `--diag-range-key`
  returns `ratio > 1.5` (network conditions improve, R2 plan changes),
  re-evaluate `DEFAULT_CONCURRENCY` in a new spec. Do not silently bump.
- **Making `concurrency=1` skip the `ThreadPoolExecutor` entirely:** The
  current code uses `ThreadPoolExecutor(max_workers=1)`, which
  degenerates to sequential submission with minor overhead. A
  micro-optimization to skip the executor when `concurrency=1` is
  possible but out of scope for this revert; measure first.

## References

- `reports/admin-r2-backup-rclone-download-v1-results.md` — the v1 rclone
  benchmark results; canonical evidence for this spec (the `ratio=0.83`
  finding).
- `specs/admin-r2-backup-rclone-download-v1.md` — the v1 rclone spec that
  prescribed the benchmark; STOP decision per its Step 1c.
- `specs/admin-r2-backup-throughput-remediation-v2.md` — closure of the
  32-worker investigation; established `DEFAULT_CONCURRENCY=8` baseline
  that this spec reverts.
- `specs/admin-r2-backup-throughput-remediation-v1.md` — original
  32-worker bump (commit `ea397e7`); the regression this spec's lineage
  addresses.
- `specs/admin-r2-backup-perf-traces.md` — `BackupTracer` and
  `range_get_throughput_diag` design; the tools that produced all the
  evidence.
- `src/stream_of_worship/admin/services/r2_backup.py:32-37` —
  `DEFAULT_CONCURRENCY` constant (Change 1 target).
- `src/stream_of_worship/admin/services/r2_backup.py:914-915` —
  `write_backup` validation (Change 2 target).
- `src/stream_of_worship/admin/commands/maintenance.py:648-656` —
  `--concurrency` Typer option (Change 3 target).
- `src/stream_of_worship/admin/services/r2.py:107-109` —
  `max_pool_connections` boto3 Config (Change 4 target).
- `tests/admin/test_r2_backup.py:907-923` — boundary tests (Change 5
  target).
- `tests/admin/test_r2_backup_commands.py:214-236` —
  `test_backup_concurrency_flag` (verified unchanged under Change 6).
- `MEMORY.md:3` — existing v2 remediation entry; this spec's entry
  appends after it.
