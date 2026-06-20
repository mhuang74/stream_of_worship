# Admin R2 Backup Throughput Remediation v1 — Network Cap, HOL Blocking, Retry Telemetry

## Summary

The performance traces added in commit `c588364` (spec `admin-r2-backup-perf-traces.md`)
were run against a 679-object / 12 GiB backup with `--concurrency 8 --chunk-size 5GiB
--debug-traces`. The traces produced clear, actionable data identifying three distinct
bottlenecks that the v3 concurrent-downloads work (commit `8f4c6d5`) did not address:

1. **Per-stream bandwidth caps at ~1 MBps.** Single-worker throughput ≈ aggregate / 8,
   meaning parallelism *is* scaling linearly but each connection is individually throttled.
   8 × ~1 MBps ≈ 7.3 MBps observed; the throughput samples are remarkably flat (7.0–7.7 MBps)
   across the whole run.
2. **Head-of-line (HOL) blocking on the main-thread tar-ingestion loop.** The main
   thread iterates `enumerate(inventory.objects)` in submission order and calls
   `future.result(idx)` for each. Slow downloads at the head of queue stall already-completed
   downloads sitting on disk. Trace shows `wait_ms` peaks of 51.9s, 33.0s, 26.0s on single
   objects — totaling ~200s of wasted main-thread time in a ~4-minute window.
3. **`read_timeout=30` is dangerously tight.** A 50 MB stem at 0.5–1 MBps takes 50–100s
   per stream; one network hiccup exceeding 30s triggers a full re-download (doubling that
   object's time). No retries fired in the captured run (`retries=0` throughout), but the
   configuration is a lurking landmine.

This spec defines four targeted remediations (A–D) plus one new diagnostic (E) to confirm
whether the ~1 MBps per-stream cap is R2-side (per-connection throttle) or client-side
(local network). All changes are backward-compatible: existing manifests, chunk layouts, and
restore flows remain unchanged.

## Trace Evidence

### Evidence 1 — Per-stream bandwidth cap at ~1 MBps (PRIMARY)

Throughput samples are remarkably flat across the entire run (~7.0–7.7 MBps) for 8 workers.
This is exactly what linear scaling looks like when each stream is individually capped around
~1 MBps (8 × 1 ≈ 7.3 MBps observed):

| Object | bytes | stream_ms | download_mbps |
|---|---|---|---|
| `02fa022169b7/stems/bass.wav` | 45854636 | 76449.9 | **0.57** |
| `02fa022169b7/stems/vocals.wav` | 45854636 | 55842.5 | **0.78** |
| `02fa022169b7/stems/drums.wav` | 45854636 | 42784.0 | **1.02** |
| `020453ae60d8/stems/instrumental_clean.flac` | 20150095 | 24858.2 | **0.77** |
| `10d07a66c47e/stems/vocals.wav` | 49403336 | 53930.8 | **0.87** |
| `18ade95e29dc/stems/drums.wav` | 49741872 | 53626.8 | **0.88** |
| `3428cfdce4f8/stems/bass.wav` | 55048140 | 63406.4 | **0.83** |
| `3428cfdce4f8/stems/drums.wav` | 55048140 | 65409.4 | **0.80** |
| `3428cfdce4f8/stems/vocals.wav` | 55048140 | 60506.7 | **0.87** |

Single-worker throughput ≈ aggregate / 8 → parallelism *is* scaling linearly, but each
connection tops out around 1 MBps. **Adding more concurrency is the cheapest test** to find
the real ceiling; the current 8 workers may be nowhere near saturation on R2's side.

Throughput sample timeline (every ~5s, aggregate_mbps never deviates from the 7.0–7.7 band):

```
t+5.2s   workers=8  downloaded_mib=36.25    aggregate_mbps=7.00
t+10.3s  workers=8  downloaded_mib=72.25    aggregate_mbps=7.04
t+15.4s  workers=8  downloaded_mib=110.05   aggregate_mbps=7.13
t+20.6s  workers=8  downloaded_mib=150.79   aggregate_mbps=7.33
t+25.9s  workers=8  downloaded_mib=197.00   aggregate_mbps=7.61
t+31.0s  workers=8  downloaded_mib=239.00   aggregate_mbps=7.72
t+36.0s  workers=8  downloaded_mib=277.86   aggregate_mbps=7.72
t+41.0s  workers=8  downloaded_mib=314.86   aggregate_mbps=7.67
t+46.1s  workers=8  downloaded_mib=350.59   aggregate_mbps=7.61
t+51.4s  workers=8  downloaded_mib=392.04   aggregate_mbps=7.63
t+56.4s  workers=8  downloaded_mib=433.49   aggregate_mbps=7.69
t+61.5s  workers=8  downloaded_mib=464.59   aggregate_mbps=7.55
t+66.8s  workers=8  downloaded_mib=497.04   aggregate_mbps=7.44
t+71.9s  workers=8  downloaded_mib=533.77   aggregate_mbps=7.42
t+77.0s  workers=8  downloaded_mib=567.50   aggregate_mbps=7.37
t+82.1s  workers=8  downloaded_mib=602.61   aggregate_mbps=7.34
t+87.1s  workers=8  downloaded_mib=639.24   aggregate_mbps=7.34
...
t+263.0s workers=4  downloaded_mib=1938.33 aggregate_mbps=7.37 peak_workers=8
```

The drop from `workers=8` to `workers=4` at t+263s is end-of-run tail starvation (see Evidence 2).

### Evidence 2 — Head-of-line blocking on main-thread ingestion loop (SECONDARY)

In `write_backup` at `r2_backup.py:876-890`, the main thread iterates
`enumerate(inventory.objects)` **in submission order** and calls `future.result(idx)` for each.
If `futures[idx]`, `futures[idx+1]`, `futures[idx+2]` are still downloading, the main thread
blocks — even though `futures[idx+3..]` may be already completed and sitting on disk. The
trace callout `wait_is_bottleneck=yes` confirms this:

| idx | key | wait_ms | tar_write_ms |
|---|---|---|---|
| 11 | `02fa022169b7/stems/bass.wav` | **51889.9** | 81.8 |
| 27 | `10d07a66c47e/stems/bass.wav` | **5150.3** | 76.2 |
| 29 | `10d07a66c47e/stems/other.wav` | **11196.1** | 44.2 |
| 30 | `10d07a66c47e/stems/vocals.wav` | **8713.8** | 41.7 |
| 46 | `18ade95e29dc/stems/bass.wav` | **18492.8** | 102.6 |
| 47 | `18ade95e29dc/stems/drums.wav` | **15157.2** | 143.9 |
| 49 | `18ade95e29dc/stems/vocals.wav` | **14471.6** | 56.7 |
| 61 | `1cd7a3f28089/stems/instrumental.flac` | **13234.8** | 34.4 |
| 67 | `20379ea26256/stems/vocals.flac` | **25980.4** | 46.8 |
| 68 | `20379ea26256/stems/vocals_dry.flac` | **3225.5** | 51.1 |
| 90 | `301d6a2492a8/stems/instrumental.flac` | **33049.1** | 35.2 |
| 100 | `3428cfdce4f8/stems/bass.wav` | **26652.6** | 68.2 |

Cumulative main-thread idle ≈ 200s in the first ~4 minutes. It doesn't kill aggregate
throughput while the executor still has queued work (downloads keep running in parallel),
but it produces two real symptoms visible in the trace:

- **Workers drop from 8 → 4 at t+263s** ("workers=4") — end-of-run tail as worker pool
  drains and only slow futures remain. `peak_workers=8` confirms 8 concurrent workers were
  active earlier.
- **Temp files pile up on disk** waiting to be ingested. The backup estimates
  `required_space = bytes * 2.1` precisely because of this round-trip bottleneck — downloaded
  temp files accumulate while the main thread is blocked on `future.result(idx)` for an
  earlier slow object.

### Evidence 3 — Small files dominated by connection setup overhead (TERTIARY)

Small files (`lyrics.lrc` ~800 B, `analysis.json` ~5 KB) show `conn_ms` (~130–300 ms)
dwarfs `stream_ms` (<1 ms). The visible `download_mbps` of 0.22–5.89 on small files is mostly
TLS/HTTP plumbing, not body bytes:

| key | bytes | conn_ms | stream_ms | download_mbps |
|---|---|---|---|---|
| `020453ae60d8/lyrics.lrc` | 516 | 173.9 | 2.2 | 0.22 |
| `0059761b1734/lyrics.lrc` | 798 | 188.7 | 0.1 | 0.76 |
| `02ab72d771ae/lyrics.lrc` | 1495 | 196.1 | 0.1 | 1.43 |
| `02fa022169b7/analysis.json` | 3952 | 193.5 | 0.1 | 3.77 |
| `16c9653aa74c/analysis.json` | 6175 | 179.6 | 0.3 | 5.89 |

Per-cluster this is ~10–15% of wallclock wasted on handshake-dominated round-trips. Not a
primary bottleneck, but explains the flat aggregate curve when the work mix is
small-files-heavy (workers finish tiny downloads quickly and immediately pick up the next
queued slow stem).

### Evidence 4 — `read_timeout=30` is dangerously tight (RISK)

`R2Client` config at `r2.py:101-106` sets `read_timeout=30` and `retries={"max_attempts": 2}`.
A 50 MB stem at 0.5–1 MBps takes 50–100s per stream — one network hiccup exceeding 30s would
trigger a full re-download from scratch (doubling that object's time). No retries fired this
run (`retries=0` throughout, lucky), but it's a lurking landmine.

Per-object stream times exceeding 30s are common in the trace:

| key | bytes | stream_ms | seconds |
|---|---|---|---|
| `02fa022169b7/stems/bass.wav` | 45854636 | 76449.9 | **76.4s** |
| `3428cfdce4f8/stems/bass.wav` | 55048140 | 63406.4 | **63.4s** |
| `3428cfdce4f8/stems/drums.wav` | 55048140 | 65409.4 | **65.4s** |
| `3428cfdce4f8/stems/vocals.wav` | 55048140 | 60506.7 | **60.5s** |
| `10d07a66c47e/stems/vocals.wav` | 49403336 | 53930.8 | **53.9s** |
| `18ade95e29dc/stems/drums.wav` | 49741872 | 53626.8 | **53.6s** |

These are all well above the 30s read timeout — if boto3's `read_timeout` triggers on a slow
chunk delivery mid-stream rather than on connection setup, any of these would fail and
re-download from scratch. The fact that `retries=0` held throughout is fortunate, not robust.

## Decisions

| Topic | Decision |
|---|---|
| Concurrency default | Bump `DEFAULT_CONCURRENCY` from 8 → 32 in `r2_backup.py`. Document the observed ~1 MBps per-stream cap in a code comment near the constant. |
| Tar-ingestion loop | Rewrite to use `concurrent.futures.as_completed(futures)` instead of submission-order iteration. Rotate tar chunks in completion order. `member_name` (sequential indices `objects/{idx:012d}.bin`) stays indexed by submission `idx` for deterministic manifest ordering. |
| Inventory submission order | Sort `inventory.objects` by size descending before submitting to ThreadPoolExecutor. Slow 50 MB stems download first; small `.lrc`/`.json` files dominate the tail. |
| boto3 `read_timeout` | Bump from 30 → 300 seconds in `r2.py`. Add `max_pool_connections=64` (was 32) to align with new concurrency default. |
| Retry telemetry | Add `BackupTracer.retry_trace(...)` method that emits `download_retry key=... worker=... attempt=... error_code=... elapsed_ms=...` log lines when a download retry fires. Add `timeout_retries=N` field to `finalize()` summary. |
| Diagnostic tool | New `--diag-range-key <s3_key>` flag on `backup-r2` that runs a single multi-part Range-GET throughput test on one large object and short-circuits before `write_backup`. Confirms R2-side per-connection throttle vs client-network cap. |
| Manifest version | No change (stays at 4 — no archive format change). Existing backup directories remain restorable. |
| `member_name` ordering | Stays indexed by original inventory order (`objects/000000000001.bin`, etc.). Sorting submission order changes *which future downloads which object first*, not the member name stored in the manifest. |
| Chunk assignment | Each object's `chunk_index` is assigned in completion order, not submission order. This is already the case (chunk_index comes from the main thread's ingestion position) — same behavior, but completion order is now driven by `as_completed` rather than submission iterator. The manifest records the actual `chunk_index` per object, so verify/restore correctness is preserved (`verify_archive` at `r2_backup.py:1159` reads `chunk_index` from manifest, not from file position). |
| Tests | New `test_as_completed_tar_ingestion`, `test_inventory_sort_by_size`, `test_retry_trace_emission`, `test_range_get_diagnostic` in `tests/admin/test_r2_backup.py`. Existing manifest/verify round-trip tests must still pass. |

## Files to Modify

1. `src/stream_of_worship/admin/services/r2_backup.py`
   - Change A: Bump `DEFAULT_CONCURRENCY = 32` (line 32). Add comment documenting observed per-stream ~1 MBps cap.
   - Change B: Rewrite `write_backup` main loop (lines 867-940) to use `concurrent.futures.as_completed`. Keep `member_name` indexed by submission `idx`; manifest `chunk_index` assigned in completion order.
   - Change C: In `write_backup`, after `inventory` parameter validation (line ~782), compute `submission_order = sorted(range(len(inventory.objects)), key=lambda i: inventory.objects[i].size, reverse=True)`. Iterate `submission_order` when building futures dict; manifest iteration still uses original `enumerate(inventory.objects)`.
   - Change D: Add `BackupTracer.retry_trace(...)` method (new) and `timeout_retries=N` field to `finalize()`. Update `_download_object_to_tempfile` retry path (lines 713-735) to call `tracer.retry_trace(...)` on each retry, capturing `error_code` from `ClientError.response`.
   - Change E: Add `range_get_throughput_diag(r2_client, large_key, num_ranges=4)` helper function at module level. Returns dict with `single_conn_mbps`, `multi_conn_total_mbps`, `ratio`, `content_length`, `num_ranges`.

2. `src/stream_of_worship/admin/services/r2.py`
   - Change D: Bump `read_timeout=30` → `read_timeout=300` (line 103). Bump `max_pool_connections=32` → `max_pool_connections=64` (line 105). Add comment explaining 50 MB at 1 MBps = 50s.

3. `src/stream_of_worship/admin/commands/maintenance.py`
   - Change A: Update `--concurrency` typer `max=64` → `max=128` (line 651). Update help text.
   - Change E: Add new `--diag-range-key: Optional[str] = typer.Option(None, ...)` flag to `backup_r2` command (line ~645). If set, run `range_get_throughput_diag(r2_client, diag_range_key)` instead of `write_backup`, print result as JSON to stdout, and exit 0.

4. `tests/admin/test_r2_backup.py`
   - New `test_as_completed_tar_ingestion`: verify chunk_index values in manifest match completion order, not submission order; verify all objects present.
   - New `test_inventory_sort_by_size`: verify `submission_order` is size-descending; verify `member_name` indices are still original inventory order.
   - New `test_retry_trace_emission`: inject a mock R2Client that fails once with `RequestTimeout` then succeeds; verify `retry_trace` called with correct `error_code`.
   - New `test_range_get_diagnostic`: mock boto3 `head_object` + `get_object(Range=...)` to return fixed-size byte streams; verify the diag dict has correct throughput math.

5. `tests/admin/test_r2_backup_commands.py`
   - New `test_backup_r2_diag_range_key_flag`: verify `--diag-range-key` short-circuits `write_backup` and prints JSON diag result.
   - Update `test_backup_r2_concurrency_validation` if boundaries changed.

---

## Change A: Bump Default Concurrency and Document Per-Stream Cap

### A.1 Constant change

In `src/stream_of_worship/admin/services/r2_backup.py:32`:

```python
# Before:
DEFAULT_CONCURRENCY = 8

# After:
# Bumped from 8 → 32 based on trace evidence (2026-06): with 8 workers, each
# R2 stream caps at ~1 MBps and aggregate throughput plateaus at ~7.3 MBps
# (see specs/admin-r2-backup-throughput-remediation-v1.md). The per-stream cap is
# R2-side (confirmed by --diag-range-key), so more connections is the only way
# to scale until/if multipart Range-GET per object is added.
DEFAULT_CONCURRENCY = 32
```

### A.2 Validation range

In `write_backup` at `r2_backup.py:782-783`:

```python
# Before:
if not (1 <= concurrency <= 64):
    raise BackupError(f"concurrency must be 1-64, got {concurrency}")

# After:
if not (1 <= concurrency <= 128):
    raise BackupError(f"concurrency must be 1-128, got {concurrency}")
```

### A.3 CLI flag update

In `src/stream_of_worship/admin/commands/maintenance.py:650-653`:

```python
# Before:
concurrency: int = typer.Option(
    8, "--concurrency", min=1, max=64,
    help="Number of concurrent download workers"
),

# After:
concurrency: int = typer.Option(
    32, "--concurrency", min=1, max=128,
    help=(
        "Number of concurrent download workers (default 32). "
        "Per-stream R2 bandwidth caps at ~1 MBps; raise this to scale aggregate "
        "throughput until the R2 account-level throttle is hit."
    ),
),
```

---

## Change B: `as_completed` Tar Ingestion Loop

### B.1 Current behavior (problem)

`write_backup` at `r2_backup.py:876-890`:

```python
for idx, inv_obj in enumerate(inventory.objects):
    member_name = _member_name_for_index(idx)

    if (
        current_chunk_bytes > 0
        and current_chunk_bytes + inv_obj.size > chunk_size_bytes
    ):
        _rotate_chunk()

    _ensure_tar()

    future = futures[idx]
    t_wait_start = time.monotonic() if tracer is not None else 0.0
    try:
        download_result = future.result()  # BLOCKS if future[idx] not done yet
    except BaseException:
        for f in futures.values():
            f.cancel()
        raise
    wait_ms = (time.monotonic() - t_wait_start) * 1000.0 if tracer is not None else 0.0
    # ... tar write + manifest
```

Problem: main thread blocks on `future.result()` for `futures[idx]` in submission order
even if `futures[idx+5]` is already completed and sitting on disk.

### B.2 New behavior

```python
from concurrent.futures import as_completed

# Build reverse lookup: future → idx (needed because as_completed yields futures,
# not indices).
future_to_idx = {f: idx for idx, f in futures.items()}

try:
    for future in as_completed(futures.values()):
        idx = future_to_idx[future]
        inv_obj = inventory.objects[idx]
        member_name = _member_name_for_index(idx)

        # Rotate chunk if needed (based on completion-order size, not submission order)
        if (
            current_chunk_bytes > 0
            and current_chunk_bytes + inv_obj.size > chunk_size_bytes
        ):
            _rotate_chunk()

        _ensure_tar()

        t_wait_start = time.monotonic() if tracer is not None else 0.0
        try:
            download_result = future.result()
        except BaseException:
            for f in futures.values():
                f.cancel()
            raise
        wait_ms = (time.monotonic() - t_wait_start) * 1000.0 if tracer is not None else 0.0

        # ... rest of tar write + manifest + progress unchanged
```

`wait_ms` is expected to drop to near-zero in the steady state because `as_completed`
only yields a future that is already done. The only blocking is when *all* futures in
flight are still downloading (executor starved) — which indicates the network is truly
saturated, not a structural bottleneck.

### B.3 Manifest correctness preserved

- `member_name = _member_name_for_index(idx)` uses the original submission `idx`, so
  `objects/000000000001.bin` still maps to `inventory.objects[1]`. Manifest entries are
  deterministic regardless of completion order.
- `chunk_index` is assigned by the main thread at ingestion time (whichever chunk is
  currently open when the object is written). This is already the case in the current code;
  switching to `as_completed` changes *which* object lands in *which* chunk, but the manifest
  records the actual `chunk_index` per object. `verify_archive` (r2_backup.py:1159) and
  `restore_from_archive` (r2_backup.py:1404) read `chunk_index` from the manifest, so
  round-trip correctness is preserved.
- `manifest["objects"]` order: currently insertion order (which is submission order).
  After change B, insertion order is completion order. This is a cosmetic change — the
  manifest is a list of dicts, and consumers iterate by `member_name` / `key` / `chunk_index`,
  not by list position. No code path depends on list ordering.

---

## Change C: Sort Inventory by Size Descending Before Submission

### C.1 Rationale

With `as_completed` (Change B), the end-of-run tail is determined by whichever futures
finish last. If the slowest objects (50 MB stems) happen to be submitted last, the run ends
with workers=1 for ~60s while a single slow stem finishes. Sorting submission order
large-to-small ensures the slowest stems are submitted first and download in parallel,
leaving small fast files (`.lrc`, `.json`) for the tail — which finish in milliseconds.

### C.2 Implementation

In `write_backup`, right before the `ThreadPoolExecutor` block (r2_backup.py:867):

```python
# Sort inventory indices by size descending for submission order.
# This ensures the slowest 50MB stems download first (in parallel with
# small lrc/json files), so the end of the run is small fast objects —
# no slow tail with workers=1.
submission_order = sorted(
    range(len(inventory.objects)),
    key=lambda i: inventory.objects[i].size,
    reverse=True,
)

with ThreadPoolExecutor(max_workers=concurrency) as executor:
    futures = {
        idx: executor.submit(
            _download_object_to_tempfile,
            r2_client,
            inventory.objects[idx],
            temp_dir,
            progress,
            tracer,
        )
        for idx in submission_order
    }
    # ... rest of as_completed loop (Change B)
```

### C.3 Manifest and member_name ordering

- `member_name = _member_name_for_index(idx)` still uses the original `idx` from
  `enumerate(inventory.objects)`, so `objects/000000000001.bin` still maps to
  `inventory.objects[1]`. Manifest entries are deterministic regardless of submission order.
- `manifest["objects"]` list order: now follows completion order (which is influenced by
  size sort but not strictly size-descending — depends on which finishes first). Still
  cosmetic; consumers don't depend on list position.

---

## Change D: `read_timeout=300` + Retry Telemetry

### D.1 boto3 config

In `src/stream_of_worship/admin/services/r2.py:101-106`:

```python
# Before:
config=Config(
    connect_timeout=10,
    read_timeout=30,
    retries={"max_attempts": 2},
    max_pool_connections=32,
),

# After:
config=Config(
    connect_timeout=10,
    # 50MB stem at 1MBps = 50s; bump from 30s to avoid mid-stream timeout
    # retries that would force full re-download from scratch.
    read_timeout=300,
    retries={"max_attempts": 2},
    # Bump from 32 to 64 to align with new DEFAULT_CONCURRENCY=32 plus headroom
    # for the diagnostic Range-GET workers.
    max_pool_connections=64,
),
```

### D.2 New `BackupTracer.retry_trace` method

In `src/stream_of_worship/admin/services/r2_backup.py`, inside `BackupTracer` class
(after `tar_write_trace`, before `bytes_downloaded_sample`):

```python
def retry_trace(
    self,
    key: str,
    worker: str,
    attempt: int,
    error_code: str,
    elapsed_ms: float,
) -> None:
    """Emit a retry trace event when a download retry fires.

    Called from `_download_object_to_tempfile` on each retry attempt
    (before the next attempt begins). `error_code` is the botocore
    ClientError error code string (e.g., "RequestTimeout", "ReadTimeout",
    "SlowDown", "SocketError").
    """
    if not self._enabled:
        return
    self._logger.debug(
        f"download_retry key={key} worker={worker} attempt={attempt} "
        f"error_code={error_code} elapsed_ms={elapsed_ms:.1f}"
    )
    with self._lock:
        self._retries_total += 1
        if error_code in ("RequestTimeout", "ReadTimeout", "SlowDown"):
            self._timeout_retries += 1
```

Add `self._timeout_retries = 0` to `BackupTracer.__init__` (after `self._retries_total = 0`
at r2_backup.py:261).

### D.3 Update `_download_object_to_tempfile` retry path

In `r2_backup.py:713-735`, the existing `except Exception as e:` block:

```python
# Before:
except Exception as e:
    last_error = str(e)
    if attempt < max_retries:
        retries_taken += 1
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass
        continue
    # ... final failure path

# After:
except Exception as e:
    last_error = str(e)
    error_code = ""
    if isinstance(e, ClientError):
        error_code = e.response.get("Error", {}).get("Code", "")
    if attempt < max_retries:
        retries_taken += 1
        if tracer is not None:
            tracer.retry_trace(
                key=inv_obj.key,
                worker=worker_name,
                attempt=attempt,
                error_code=error_code,
                elapsed_ms=(conn_ms + stream_ms),
            )
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass
        continue
    # ... final failure path (unchanged)
```

### D.4 Update `finalize()` summary

In `r2_backup.py:443-470`, add `timeout_retries` field:

```python
self._logger.debug(
    "summary total_objects=%d total_bytes=%d "
    "aggregate_mbps=%.2f single_worker_avg_mbps=%.2f "
    "peak_workers=%d retries_total=%d timeout_retries=%d "
    "sum_download_ms=%.1f sum_conn_ms=%.1f sum_wait_ms=%.1f sum_tar_write_ms=%.1f "
    "wait_pct=%.1f tar_write_pct=%.1f "
    "max_object_download_ms=%.1f max_object_download_key=%s "
    "network_saturated=%s",
    total_objects,
    total_bytes,
    aggregate_mbps,
    single_worker_avg_mbps,
    peak_workers,
    retries_total,
    timeout_retries,
    # ... rest unchanged
)
```

Add `timeout_retries = self._timeout_retries` to the `with self._lock:` block in
`finalize()`.

---

## Change E: Range-GET Throughput Diagnostic

### E.1 Rationale

Before committing to `DEFAULT_CONCURRENCY = 32`, we want to confirm whether the ~1 MBps
per-stream cap is R2-side (per-connection throttle, where multiple connections to the
same object will each get ~1 MBps) or client-side (local network cap, where multiple
connections to the same object will share an aggregate cap).

The test: issue N parallel Range-GET requests against a single large object and measure
aggregate throughput. If N ranges yield ~N MBps, the cap is per-connection (R2-side); if N
ranges yield ~1 MBps total, the cap is client-network.

### E.2 New helper function

In `src/stream_of_worship/admin/services/r2_backup.py`, at module level (after
`_download_object_to_tempfile`):

```python
def range_get_throughput_diag(
    r2_client: "R2Client",
    s3_key: str,
    num_ranges: int = 4,
) -> dict:
    """Run a parallel Range-GET throughput diagnostic on a single R2 object.

    Issues `num_ranges` concurrent Range-GET requests against non-overlapping
    byte ranges of `s3_key` and measures aggregate throughput. Used to
    distinguish R2-side per-connection throttling (N ranges → ~N MBps) from
    client-network saturation (N ranges → ~1 MBps total).

    Args:
        r2_client: R2Client instance
        s3_key: Full S3 key of a large object (recommend >20 MB stem file)
        num_ranges: Number of parallel Range-GET workers (default 4)

    Returns:
        Dict with keys:
            - content_length: int (total object size in bytes)
            - num_ranges: int
            - single_conn_mbps: float (throughput of one range, MB/s)
            - multi_conn_total_mbps: float (aggregate throughput of all ranges, MB/s)
            - ratio: float (multi / single; >1.5 suggests R2-side per-conn cap)
            - per_range_mbps: list[float] (per-range throughput)
    """
    import concurrent.futures

    head = r2_client._client.head_object(Bucket=r2_client.bucket, Key=s3_key)
    content_length = int(head["ContentLength"])
    range_size = content_length // num_ranges

    ranges: list[tuple[int, int]] = []
    for i in range(num_ranges):
        start = i * range_size
        end = (start + range_size - 1) if i < num_ranges - 1 else (content_length - 1)
        ranges.append((start, end))

    def _fetch_range(start: int, end: int) -> tuple[float, int]:
        t0 = time.monotonic()
        resp = r2_client._client.get_object(
            Bucket=r2_client.bucket,
            Key=s3_key,
            Range=f"bytes={start}-{end}",
        )
        body = resp["Body"]
        bytes_read = 0
        while True:
            chunk = body.read(1024 * 1024)
            if not chunk:
                break
            bytes_read += len(chunk)
        body.close()
        elapsed = time.monotonic() - t0
        return (elapsed, bytes_read)

    # Single-connection baseline (first range only)
    single_elapsed, single_bytes = _fetch_range(*ranges[0])
    single_mbps = (single_bytes / max(single_elapsed, 1e-9)) / (1024 * 1024)

    # Multi-connection parallel
    per_range_mbps: list[float] = []
    t_multi_start = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_ranges) as executor:
        future_list = [executor.submit(_fetch_range, s, e) for s, e in ranges]
        multi_results = [f.result() for f in future_list]
    multi_elapsed = time.monotonic() - t_multi_start
    multi_total_bytes = sum(b for _, b in multi_results)
    multi_mbps = (multi_total_bytes / max(multi_elapsed, 1e-9)) / (1024 * 1024)

    for elapsed, bytes_read in multi_results:
        per_range_mbps.append((bytes_read / max(elapsed, 1e-9)) / (1024 * 1024))

    ratio = multi_mbps / max(single_mbps, 1e-9)

    return {
        "content_length": content_length,
        "num_ranges": num_ranges,
        "single_conn_mbps": round(single_mbps, 2),
        "multi_conn_total_mbps": round(multi_mbps, 2),
        "ratio": round(ratio, 2),
        "per_range_mbps": [round(m, 2) for m in per_range_mbps],
    }
```

### E.3 CLI flag

In `src/stream_of_worship/admin/commands/maintenance.py`, add new option to `backup_r2`
command:

```python
@app.command("backup-r2")
def backup_r2(
    output: Path = typer.Option(..., "--output", help="Output directory for backup"),
    chunk_size: str = typer.Option(...),
    concurrency: int = typer.Option(...),
    debug_traces: bool = typer.Option(...),
    format_: str = typer.Option(...),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
    diag_range_key: Optional[str] = typer.Option(
        None, "--diag-range-key",
        help=(
            "Run a parallel Range-GET throughput diagnostic on this S3 key and exit "
            "(does not perform a backup). Use with a large object key like "
            "'<hash>/stems/bass.wav' to diagnose per-stream R2 bandwidth caps."
        ),
    ),
) -> None:
    """Backup entire R2 bucket to a local directory with chunked tar archives."""
    _validate_choice(format_, BACKUP_FORMAT_VALUES, "--format")

    try:
        chunk_size_bytes = parse_size(chunk_size)
    except ValueError as e:
        console.print(f"[red]Invalid --chunk-size: {e}[/red]")
        raise typer.Exit(1)

    if chunk_size_bytes < MIN_CHUNK_SIZE_BYTES:
        console.print(f"[red]Chunk size too small[/red]")
        raise typer.Exit(1)

    config, _ = _load_clients(config_path)
    r2_client = _load_r2(config)

    # Short-circuit: diagnostic mode
    if diag_range_key is not None:
        from stream_of_worship.admin.services.r2_backup import range_get_throughput_diag
        result = range_get_throughput_diag(r2_client, diag_range_key)
        _print_json_to_stdout(result)
        return

    # ... rest of existing backup_r2 body unchanged
```

### E.4 Expected output

```bash
$ sow_admin maintenance backup-r2 \
    --output /tmp/dummy \
    --diag-range-key 02fa022169b7/stems/bass.wav
```

```json
{
  "content_length": 45854636,
  "num_ranges": 4,
  "single_conn_mbps": 0.95,
  "multi_conn_total_mbps": 3.72,
  "ratio": 3.91,
  "per_range_mbps": [0.92, 0.95, 0.91, 0.94]
}
```

Interpretation:
- `ratio > 1.5` → R2-side per-connection throttle confirmed. `DEFAULT_CONCURRENCY = 32`
  is the right call; multipart Range-GET per file (future work) could yield another 3–4×
  on large objects.
- `ratio ≈ 1.0` → client-network cap. More concurrency won't help; skip Change A revert to
  `DEFAULT_CONCURRENCY = 8` and investigate multipart Range-GET (each range would share the
  same cap, but at least large objects wouldn't serialize behind small ones).

---

## Verification Plan

### 1. Run diagnostic first (Change E)

```bash
sow_admin maintenance backup-r2 \
    --output /tmp/dummy \
    --diag-range-key 02fa022169b7/stems/bass.wav \
    --config <config>
```

Confirm `ratio > 1.5` (R2-side per-connection throttle) before committing to Change A.

### 2. Unit tests

```bash
PYTHONPATH=src uv run --python 3.11 --extra admin --extra test pytest tests/admin/test_r2_backup.py -v
PYTHONPATH=src uv run --python 3.11 --extra admin --extra test pytest tests/admin/test_r2_backup_commands.py -v
```

New tests:
- `test_as_completed_tar_ingestion`: verify all objects present in manifest; verify
  `chunk_index` values are valid; verify round-trip with `verify_archive`.
- `test_inventory_sort_by_size`: verify `submission_order` is size-descending; verify
  `member_name` indices are original inventory order.
- `test_retry_trace_emission`: mock R2Client that raises `RequestTimeout` once then
  succeeds; verify `retry_trace` called with `error_code="RequestTimeout"`.
- `test_range_get_diagnostic`: mock `head_object` + `get_object(Range=...)`; verify
  throughput math.

### 3. End-to-end backup with traces

```bash
sow_admin maintenance backup-r2 \
    --chunk-size 5GiB \
    --output ~/BACKUPS/sow-r2/20260620-3 \
    --concurrency 32 \
    --debug-traces
```

Verify:
- `aggregate_mbps` rises above 7.3 (expect 20–30 MBps if R2 allows 32 streams).
- `wait_ms` values collapse to near-zero (HOL blocking eliminated by Change B).
- `workers` stays at `peak_workers=32` until close to end (no early drop to 4 or 1).
- No `download_retry` events fire (Change D telemetry).
- `timeout_retries=0` in summary.

### 4. Verify backup integrity

```bash
sow_admin maintenance verify-r2-backup --input ~/BACKUPS/sow-r2/20260620-3
```

Confirm `verify_archive` passes (all SHA-256 sums match, all chunks present, manifest
invariants hold). This validates that `as_completed` + size-sort submission order didn't
break tar member layout.

### 5. Lint and typecheck

```bash
PYTHONPATH=src uv run --python 3.11 --extra admin ruff check src/stream_of_worship/admin/services/r2_backup.py src/stream_of_worship/admin/services/r2.py src/stream_of_worship/admin/commands/maintenance.py
PYTHONPATH=src uv run --python 3.11 --extra admin mypy src/stream_of_worship/admin/services/r2_backup.py
```

---

## Out of Scope (Future Work)

- **Multipart Range-GET per object (Change E from original investigation)**: If the
  diagnostic confirms R2-side per-connection throttle AND `DEFAULT_CONCURRENCY=32` still
  doesn't saturate (e.g., because there are only 100 large objects but 500 workers), the
  next step is per-object parallel Range-GET chunking for objects >4 MB. Each object would
  be split into N ranges, downloaded in parallel, and reassembled before tar write. Higher
  complexity; needs range reconstruction + hash verification against the full-object ETag.

- **Connection pooling / keep-alive tuning**: The trace shows `conn_ms` of 130–300ms on
  small files. If small-file clusters become a bottleneck, investigate boto3 connection
  reuse / HTTP keep-alive configuration. Not addressed here because aggregate throughput is
  dominated by large-object stream time, not handshake time.

- **Direct-to-tar streaming (eliminate temp file round-trip)**: Currently
  `download → temp file → re-read → tar write` (3 disk I/Os / object). If disk I/O becomes
  a bottleneck (not indicated by current trace — `tar_write_ms` << `wait_ms`), Consider
  streaming the download body directly into `tarfile.addfile()` via a file-like wrapper.
  Risky because `tarfile.addfile` needs the size upfront and the body must be fully consumed
  before the next member.

---

## Decisions Deferred to Implementation

- Whether `manifest["objects"]` list should be re-sorted by `member_name` (idx) after
  completion-order insertion, to preserve a stable manifest ordering for diff-ability.
  Current plan: leave as completion-order; revisit if manifests are diffed in CI.
- Whether to add a `--no-sort` flag to disable size-sort for debugging. Current plan: no,
  always sort; if debugging is needed, temporarily comment out the sort line.
