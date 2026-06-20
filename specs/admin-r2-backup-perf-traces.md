# Admin R2 Backup Performance Traces — Diagnose 25-Minute / 12 GiB Bottleneck

## Summary

The v3 progress bar (download-aware progress, worker count, streaming MD5) shipped in commit `08a844f` did **not** produce the expected multi-fold wall-clock speedup: backup of ~12 GiB of R2 objects still takes >25 minutes with `--concurrency 8`. The v3 spec only addressed *progress display* smoothness; it did not change the actual download/tar-write pipeline.

This spec adds **performance traces** to the `backup-r2` command so the next run yields actionable numbers identifying which of the following is the actual bottleneck:

| Suspect | Diagnostic question |
|---|---|
| **Head-of-line blocking** | How much time does the main thread spend blocked on `future.result(idx)` in submission order, versus time inside `current_tar.addfile`? v3 only fixed the *progress display* of this; it did not remove the head-of-line wait. |
| **Aggregate network saturation** | Does aggregate throughput scale with `concurrency`, or does it plateau? If 8 workers each get ~1.5 MB/s but aggregate is also ~12 MB/s, the R2 link (or local uplink) is saturated and adding workers won't help. |
| **Temp-file disk round-trip** | Each object is `download → temp file → re-read → tar write` (3 disk I/Os / object). Is `tar_write_ms` ≈ `download_ms` (round-trip bound) or << (network bound)? |
| **boto3 connection setup** | Is `get_object_stream` (handshake + SSL + headers) a significant fraction of per-object time, especially for small objects? |

Traces are gated behind a new `--debug-traces` CLI flag (default off) so production runs are unaffected.

## Decisions

| Topic | Decision |
|---|---|
| Trace destination | Existing stderr logger (`logger = logging.getLogger(__name__)` in `r2_backup.py`); set to DEBUG when flag is on; Rich progress bar continues on same stderr (occlusion handled by `_on_progress` throttling — see Verification). |
| Trace granularity | Per-object (download + tar-write rows) + per-phase (inventory, download, tar-write, manifest, spot-check, total). |
| Activation | New `--debug-traces` flag on `backup-r2` — when set, configure logger to DEBUG on stderr and construct a `BackupTracer` passed into `write_backup`. |
| Tracer lifetime | Constructed in `maintenance.py`; passed to `write_backup(tracer=...)` and `_download_object_to_tempfile(tracer=...)` as new optional param; default `None` → no behavior change and no overhead. |
| API surface | New public class `BackupTracer` in `r2_backup.py` (exported from module). |
| Manifest version | No change (stays at 4 — no archive format change). |
| Tests | New `TestBackupTracer` class; new `test_backup_r2_debug_traces_flag` in command tests. |

## Files to Modify

1. `src/stream_of_worship/admin/services/r2_backup.py` — new `BackupTracer` class; thread `tracer: Optional[BackupTracer]` param through `_download_object_to_tempfile` and `write_backup`; emit trace events at the points enumerated in Change A–D.
2. `src/stream_of_worship/admin/commands/maintenance.py` — add `--debug-traces` flag, configure logger, construct `BackupTracer`, wrap `_on_progress` callback to feed throughput samples.
3. `tests/admin/test_r2_backup.py` — new `TestBackupTracer` class covering trace emission and aggregation.
4. `tests/admin/test_r2_backup_commands.py` — assert `--debug-traces` flag passes tracer through to `write_backup`.

---

## Change A: New `BackupTracer` Class (`r2_backup.py`)

### A.1 Class definition

```python
import logging


class BackupTracer:
    """Collects and emits performance traces for backup operations.

    Thread-safe. Designed to be passed (as `tracer=`) into `write_backup`
    and `_download_object_to_tempfile` to emit per-object and per-phase
    timing logs at DEBUG level. All methods are no-ops if the tracer is
    disabled (logger level above DEBUG).

    Traces are written to the module logger (`stream_of_worship.admin.services.r2_backup`).
    Per-object events fire from worker threads; Rich `Progress` renders on the
    same stderr but the tracer is throttled by the existing `BackupProgress._maybe_report`
    path (which feeds `bytes_downloaded_sample` via the wrapped callback in maintenance.py).
    """

    # Aggregate throughput sample interval (seconds)
    THROUGHPUT_SAMPLE_INTERVAL = 5.0

    def __init__(self, logger: Optional[logging.Logger] = None):
        self._logger = logger or logging.getLogger(__name__)
        self._enabled = self._logger.isEnabledFor(logging.DEBUG)
        self._lock = threading.Lock()

        # Phase timing
        self._phase_starts: dict[str, float] = {}

        # Per-object accumulators (aggregate over all objects)
        self._object_count_downloaded = 0
        self._object_count_written = 0
        self._bytes_downloaded = 0
        self._bytes_written = 0
        self._retries_total = 0
        self._sum_download_ms = 0.0       # sum of worker download_ms across objects
        self._sum_conn_ms = 0.0            # sum of boto3 get_object_stream ms
        self._sum_wait_ms = 0.0            # sum of main-thread future.result() wait ms
        self._sum_tar_write_ms = 0.0       # sum of main-thread tar.addfile ms
        self._max_object_download_ms = 0.0
        self._max_object_download_key = ""

        # Throughput sampling (fed via bytes_downloaded_sample)
        self._throughput_samples: list[tuple[float, int, int]] = []  # (t, bytes, workers)
        self._last_throughput_log = 0.0
        self._peak_workers = 0

        # Run totals
        self._run_start = 0.0

    # ---- Phase-level tracing ----

    def phase_start(self, name: str) -> None:
        if not self._enabled:
            return
        with self._lock:
            self._phase_starts[name] = time.monotonic()
        if name == "total":
            self._run_start = time.monotonic()

    def phase_end(self, name: str, **extra) -> None:
        if not self._enabled:
            return
        with self._lock:
            start = self._phase_starts.pop(name, None)
        if start is None:
            return
        elapsed_ms = (time.monotonic() - start) * 1000.0
        fields = " ".join(f"{k}={v}" for k, v in extra.items())
        self._logger.debug(
            f"phase_end name={name} elapsed_ms={elapsed_ms:.1f} {fields}"
        )

    # ---- Per-object download tracing (worker thread) ----

    def object_download_trace(
        self,
        key: str,
        worker: str,
        attempt: int,
        conn_ms: float,
        stream_ms: float,
        bytes_read: int,
        retries: int,
    ) -> None:
        """Emit per-object download trace and update accumulators.

        Called from each download worker thread after a download attempt completes
        (success or final failure).
        """
        if not self._enabled:
            return
        self._logger.debug(
            f"object_download key={key} worker={worker} attempt={attempt} "
            f"conn_ms={conn_ms:.1f} stream_ms={stream_ms:.1f} "
            f"bytes={bytes_read} download_mbps={(bytes_read / max(stream_ms, 1.0)) * 1000 / (1024*1024):.2f} "
            f"retries={retries}"
        )
        with self._lock:
            self._object_count_downloaded += 1
            self._bytes_downloaded += bytes_read
            self._retries_total += retries
            self._sum_download_ms += stream_ms
            self._sum_conn_ms += conn_ms
            total_ms = stream_ms + conn_ms
            if total_ms > self._max_object_download_ms:
                self._max_object_download_ms = total_ms
                self._max_object_download_key = key

    # ---- Per-object tar-write tracing (main thread) ----

    def tar_write_trace(
        self,
        idx: int,
        key: str,
        wait_ms: float,
        tar_write_ms: float,
        bytes_written: int,
    ) -> None:
        """Emit per-object tar-write trace and update accumulators.

        Called from the main thread after writing one object to the chunk tar.

        `wait_ms` = wall time spent on `future.result(idx)` (head-of-line wait).
        `tar_write_ms` = wall time spent in `current_tar.addfile(...)` end-to-end
        (includes opening temp file, reading from disk, and writing to tar).
        """
        if not self._enabled:
            return
        self._logger.debug(
            f"tar_write idx={idx} key={key} "
            f"wait_ms={wait_ms:.1f} tar_write_ms={tar_write_ms:.1f} "
            f"bytes={bytes_written} "
            f"wait_is_bottleneck={'yes' if wait_ms > tar_write_ms else 'no'}"
        )
        with self._lock:
            self._object_count_written += 1
            self._bytes_written += bytes_written
            self._sum_wait_ms += wait_ms
            self._sum_tar_write_ms += tar_write_ms

    # ---- Throughput sampling (called from throttled on_progress callback) ----

    def bytes_downloaded_sample(self, bytes_downloaded: int, active_workers: int) -> None:
        """Record a throughput sample and emit periodic aggregate throughput log.

        Called ~10/sec from `BackupProgress._maybe_report` via the wrapped
        `_on_progress` callback in maintenance.py.
        """
        if not self._enabled:
            return
        now = time.monotonic()
        with self._lock:
            self._throughput_samples.append((now, bytes_downloaded, active_workers))
            if active_workers > self._peak_workers:
                self._peak_workers = active_workers
            if now - self._last_throughput_log < self.THROUGHPUT_SAMPLE_INTERVAL:
                return
            self._last_throughput_log = now
            if len(self._throughput_samples) >= 2:
                t0, b0, _ = self._throughput_samples[0]
                t1, b1, w1 = self._throughput_samples[-1]
                dt = max(t1 - t0, 1e-9)
                mbps = (b1 - b0) / dt / (1024 * 1024)
                elapsed_since_run_start = now - self._run_start if self._run_start else 0.0
                self._logger.debug(
                    f"throughput_sample t+{elapsed_since_run_start:.1f}s "
                    f"workers={w1} downloaded_mib={b1 / (1024*1024):.2f} "
                    f"aggregate_mbps={mbps:.2f} peak_workers={self._peak_workers}"
                )

    # ---- Final summary ----

    def finalize(self, total_objects: int, total_bytes: int) -> None:
        """Emit aggregate summary stats.

        Expected to be called from `write_backup()` after the ThreadPoolExecutor
        block completes (after downloads + tar writes, before spot-check).
        """
        if not self._enabled:
            return
        with self._lock:
            samples = list(self._throughput_samples)
            peak_workers = self._peak_workers
            sum_download_ms = self._sum_download_ms
            sum_conn_ms = self._sum_conn_ms
            sum_wait_ms = self._sum_wait_ms
            sum_tar_write_ms = self._sum_tar_write_ms
            bytes_downloaded = self._bytes_downloaded
            retries_total = self._retries_total
            max_object_download_ms = self._max_object_download_ms
            max_object_download_key = self._max_object_download_key

        # Aggregate throughput across whole run (wall-clock between first and last sample)
        if len(samples) >= 2:
            t0, b0, _ = samples[0]
            t1, b1, _ = samples[-1]
            dt = max(t1 - t0, 1e-9)
            aggregate_mbps = (b1 - b0) / dt / (1024 * 1024)
        else:
            aggregate_mbps = 0.0

        # Single-worker average throughput = bytes / per-object average download time.
        # If single-worker avg << aggregate, workers ARE scaling (network not saturated).
        # If single-worker avg ≈ aggregate, network is saturated (adding workers won't help).
        avg_per_object_download_ms = (
            sum_download_ms / max(total_objects, 1)
        )
        avg_object_bytes = bytes_downloaded / max(total_objects, 1)
        single_worker_avg_mbps = (
            avg_object_bytes / max(avg_per_object_download_ms, 1.0) * 1000 / (1024*1024)
        )

        # Head-of-line blocking ratio: wait_ms / (wait_ms + tar_write_ms).
        # If ratio is high, main thread is bottlenecked on slow head-of-queue downloads
        # rather than running tar writes.
        main_thread_total = sum_wait_ms + sum_tar_write_ms
        wait_ratio = (sum_wait_ms / max(main_thread_total, 1.0)) * 100.0
        tar_ratio = (sum_tar_write_ms / max(main_thread_total, 1.0)) * 100.0

        self._logger.debug(
            "summary total_objects=%d total_bytes=%d "
            "aggregate_mbps=%.2f single_worker_avg_mbps=%.2f "
            "peak_workers=%d retries_total=%d "
            "sum_download_ms=%.1f sum_conn_ms=%.1f sum_wait_ms=%.1f sum_tar_write_ms=%.1f "
            "wait_pct=%.1f tar_write_pct=%.1f "
            "max_object_download_ms=%.1f max_object_download_key=%s "
            "network_saturated=%s",
            total_objects, total_bytes,
            aggregate_mbps, single_worker_avg_mbps,
            peak_workers, retries_total,
            sum_download_ms, sum_conn_ms, sum_wait_ms, sum_tar_write_ms,
            wait_ratio, tar_ratio,
            max_object_download_ms, max_object_download_key,
            "yes" if single_worker_avg_mbps > 0 and aggregate_mbps > 0
                  and (aggregate_mbps / max(single_worker_avg_mbps * peak_workers, 1.0)) < 0.5
                  else "no (or inconclusive)",
        )
```

### A.2 Design notes

- All public methods are **no-ops** when `_enabled` is False (forced via `logger.isEnabledFor(logging.DEBUG)` cached once at construction). Zero overhead when `--debug-traces` not passed.
- The `_enabled` check is per-call, not lazy; if the logger level changes after construction the tracer stays in its initial state. Acceptable — `--debug-traces` is set once at CLI parse time before the tracer is constructed.
- `logger.debug(...)` itself also early-returns on level checks; the per-call `_enabled` short-circuit avoids the per-call `time.monotonic()` / `threading.Lock()` overhead for the disabled case.
- Throughput samples are emitted inside `bytes_downloaded_sample`, which is called from `BackupProgress._maybe_report` (already throttled to ~10/sec by `min_report_interval=0.1`). The tracer further throttles its own `_last_throughput_log` to one summary every 5 seconds to keep the log readable.

### A.3 Network-saturated heuristic

The summary's `network_saturated` field compares `aggregate_mbps` against `single_worker_avg_mbps * peak_workers`:

- If `aggregate_mbps ≈ single_worker_avg_mbps * peak_workers`, scaling is working → **network NOT saturated** (CPU/disk/GIL is the limit).
- If `aggregate_mbps << single_worker_avg_mbps * peak_workers`, total throughput plateaus despite more workers → **network saturated** (R2 link or local uplink is the cap; adding workers will not help).

The `< 0.5` threshold is intentionally conservative (i.e. only flags as saturated when scaling efficiency is below 50%).

---

## Change B: Wire `BackupTracer` into `_download_object_to_tempfile()` (`r2_backup.py`)

### B.1 New `tracer` parameter

```python
def _download_object_to_tempfile(
    r2_client: R2Client,
    inv_obj: InventoryObject,
    temp_dir: Path,
    progress: Optional[BackupProgress] = None,
    tracer: Optional[BackupTracer] = None,
    max_retries: int = 2,
) -> DownloadResult:
```

### B.2 Trace points inside the download function

```python
    import tempfile
    import threading as _threading

    last_error: Optional[str] = None
    worker_name = _threading.current_thread().name
    retries_taken = 0

    for attempt in range(max_retries + 1):
        temp_path: Optional[Path] = None
        try:
            if progress is not None:
                progress.worker_started()

            t_conn_start = time.monotonic() if tracer is not None else 0.0
            resp = r2_client.get_object_stream(inv_obj.key)
            conn_ms = (time.monotonic() - t_conn_start) * 1000.0 if tracer is not None else 0.0
            body = resp["body"]
            content_length = resp["content_length"]
            get_etag = resp["etag"]

            try:
                temp_dir.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=temp_dir, delete=False) as temp_file:
                    temp_path = Path(temp_file.name)

                    on_read = progress.add_bytes if progress is not None else None
                    hashing_reader = HashingReader(body, on_read=on_read)
                    t_stream_start = time.monotonic() if tracer is not None else 0.0
                    shutil.copyfileobj(hashing_reader, temp_file, length=COPY_BUFFER_SIZE)
                    stream_ms = (time.monotonic() - t_stream_start) * 1000.0 if tracer is not None else 0.0

                # ... existing validation (short read, size mismatch, ETag check) ...

                # ... MD5 check (unchanged) ...

                sha256 = hashing_reader.sha256_hex

                metadata = { ... unchanged ... }

                if tracer is not None:
                    tracer.object_download_trace(
                        key=inv_obj.key,
                        worker=worker_name,
                        attempt=attempt,
                        conn_ms=conn_ms,
                        stream_ms=stream_ms,
                        bytes_read=hashing_reader.bytes_read,
                        retries=retries_taken,
                    )

                return DownloadResult(
                    temp_path=temp_path,
                    sha256=sha256,
                    bytes_read=hashing_reader.bytes_read,
                    metadata=metadata,
                )
            finally:
                body.close()

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
            if tracer is not None:
                tracer.object_download_trace(
                    key=inv_obj.key,
                    worker=worker_name,
                    attempt=attempt,
                    conn_ms=conn_ms,
                    stream_ms=stream_ms,
                    bytes_read=0,
                    retries=retries_taken,
                )
            raise BackupError(
                f"Failed to backup {inv_obj.key} after retries: {last_error}"
            ) from e
        finally:
            if progress is not None:
                progress.worker_finished()
```

### B.3 Key changes

- `worker_name = threading.current_thread().name` captured once.
- `t_conn_start` / `t_stream_start` measured only when `tracer is not None` (avoids `time.monotonic()` syscall overhead in production).
- `retries_taken` accumulated (currently always 0 on success path; only nonzero when a retry in the `except` branch fires).
- `object_download_trace` called on success **and** on final-failure (after retries exhausted), so all per-object timings appear in the log even for failed objects.
- `bytes_read=0` is reported for failed objects; the `sum_download_ms` accumulator only counts successful stream times (failed attempts still log their per-object DEBUG line for diagnosis, with `bytes=0`).

### B.4 Connection setup vs. stream time split

- `conn_ms` = `get_object_stream` call duration = boto3 connection acquisition from the `max_pool_connections=32` pool + HTTP/2 over TLS handshake + first response header parse.
- `stream_ms` = `shutil.copyfileobj` call duration = network body read + temp-file disk write (chained, end-to-end).
- If `conn_ms ≫ stream_ms` for small objects, boto3 connection setup dominates (consider connection reuse / tuning).
- If `stream_ms ≫ conn_ms` for large objects, network bandwidth or disk write speed is the cap — disambiguated by the `tar_write_ms` ratio (see Change C).

### B.5 Disk round-trip measurement

`stream_ms` includes network read + temp-file write (chained).
`tar_write_ms` includes temp-file read + tar-file write (chained — see Change C).
If `stream_ms` per object ≈ `tar_write_ms` per object, temp-file round-trip doubles disk I/O and dominates — combine traces with the network-required download bytes vs. total wall time to confirm.

Network-only read time ≈ `stream_ms - temp_file_write_time`. We do **not** wrap `temp_file.write` to isolate this; the chained `stream_ms` plus the absence of CPU-bound work in `shutil.copyfileobj` (it releases the GIL on the underlying `read()`/`write()` syscalls) means `stream_ms - tar_write_ms/2` is a reasonable lower bound for network time when disk reads and writes have similar rates. This is a known approximation; future spec can add finer-grained disk-write tracing if needed.

---

## Change C: Wire `BackupTracer` into `write_backup()` (`r2_backup.py`)

### C.1 New `tracer` parameter

```python
def write_backup(
    r2_client: R2Client,
    output_dir: Path,
    inventory: Inventory,
    chunk_size_bytes: int = DEFAULT_CHUNK_SIZE_BYTES,
    concurrency: int = DEFAULT_CONCURRENCY,
    on_progress: Optional[Callable[[BackupProgress], None]] = None,
    tracer: Optional[BackupTracer] = None,
) -> BackupResult:
```

### C.2 Per-object tar-write trace point in the main loop

```python
    if tracer is not None:
        tracer.phase_start("total")
        tracer.phase_start("download_phase")

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {
            idx: executor.submit(
                _download_object_to_tempfile,
                r2_client, inv_obj, temp_dir, progress, tracer,
            )
            for idx, inv_obj in enumerate(inventory.objects)
        }

        try:
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
                    download_result = future.result()
                except BaseException:
                    for f in futures.values():
                        f.cancel()
                    raise
                wait_ms = (time.monotonic() - t_wait_start) * 1000.0 if tracer is not None else 0.0

                if progress is not None:
                    progress.mark_object_downloaded()

                _track_temp(download_result.temp_path)
                try:
                    tar_info = tarfile.TarInfo(name=member_name)
                    tar_info.size = download_result.bytes_read
                    tar_info.mtime = 0
                    tar_info.mode = 0o644
                    tar_info.type = tarfile.REGTYPE

                    t_tar_start = time.monotonic() if tracer is not None else 0.0
                    with open(download_result.temp_path, "rb") as f_in:
                        current_tar.addfile(tar_info, f_in)
                    tar_write_ms = (time.monotonic() - t_tar_start) * 1000.0 if tracer is not None else 0.0

                    if tracer is not None:
                        tracer.tar_write_trace(
                            idx=idx,
                            key=inv_obj.key,
                            wait_ms=wait_ms,
                            tar_write_ms=tar_write_ms,
                            bytes_written=inv_obj.size,
                        )

                    obj_entry = _build_manifest_object(
                        inv_obj, member_name, download_result.sha256, chunk_index,
                        download_result.metadata,
                    )
                    manifest_objects.append(obj_entry)
                    current_chunk_bytes += inv_obj.size

                    if progress is not None:
                        progress.object_written(inv_obj.size)
                finally:
                    _untrack_temp(download_result.temp_path)
                    try:
                        download_result.temp_path.unlink()
                    except OSError:
                        pass
        except BaseException:
            for f in futures.values():
                f.cancel()
            raise

    if tracer is not None:
        tracer.phase_end("download_phase",
                        objects=inventory.object_count,
                        total_bytes=inventory.total_bytes)
        tracer.finalize(total_objects=inventory.object_count,
                         total_bytes=inventory.total_bytes)
```

### C.3 Phase tracing around other phases

Add `phase_start`/`phase_end` calls:

- `phase_start("disk_check")` before `_check_disk_space(...)`; `phase_end("disk_check", required_bytes=required_space)`.
- `phase_start("setup")` between `_check_disk_space` and the `ThreadPoolExecutor` block; `phase_end("setup")` after `temp_dir = partial_dir / "tmp"`.
- `phase_start("download_phase")` immediately before `with ThreadPoolExecutor(...)`; `phase_end("download_phase")` immediately after the block (as shown above).
- `phase_start("tar_close")` before `current_tar.close()`; `phase_end("tar_close")` after.
- `phase_start("spot_check")` before `if manifest_objects and SPOT_CHECK_HEAD_RATIO > 0:`; `phase_end("spot_check", samples=sample_size)` after the loop.
- `phase_start("manifest_write")` before `with open(tmp_manifest, "w") as f:`; `phase_end("manifest_write")` after `tmp_manifest.rename(manifest_path)`.
- `phase_start("rename")` before `partial_dir.rename(output_dir)`; `phase_end("rename")` after.
- `phase_end("total")` at the very end of the success path (just before returning `BackupResult`).

All phase calls are gated with `if tracer is not None:`.

### C.4 Key changes

- `tracer` plumbed through to `_download_object_to_tempfile` as the 5th positional arg in `executor.submit(...)`.
- Each main-loop iteration measures `wait_ms` (time on `future.result()`) and `tar_write_ms` (time on `current_tar.addfile` end-to-end including temp-file open).
- `finalize(...)` called after the executor block to emit the aggregate summary line.
- Phase boundaries emit `phase_end name=X elapsed_ms=Y fields...` lines.

### C.5 Head-of-line blocking interpretation

- If `Σ wait_ms` over all objects is much larger than `Σ tar_write_ms`, the main thread spends most of its time blocked on slow downloads (workers are not feeding results in submission order). Implication: head-of-line blocking is real; v3's in-order `future.result()` loop is wasting wall-clock time.
  - Fix (out of scope for this spec): consume futures in completion order via `concurrent.futures.as_completed` and queue tar writes separately.
- If `Σ wait_ms ≈ 0` and `Σ tar_write_ms` dominates the main-thread time, the tar-write sequential phase is the cap (CPU or disk bound for `tarfile.addfile`).
  - Fix (out of scope): parallelize tar writes via thread-local chunk buffers, or stream downloads directly into tar members (no temp file).

### C.6 Network saturation interpretation

- `single_worker_avg_mbps` = average per-object `bytes / stream_ms`.
- `aggregate_mbps` = whole-run wall-clock throughput across all workers.
- `network_saturated=yes` if `aggregate_mbps / (single_worker_avg_mbps * peak_workers) < 0.5` — scaling efficiency below 50%.
  - If yes: R2 link or local uplink is the cap; add workers won't help. Tune TCP / use R2 egress priority / consider parallel multipart range GETs (`Range:` header).
  - If no: bottleneck is elsewhere (disk, tar-write main thread, GIL); address via above interpretations.

---

## Change D: `--debug-traces` flag and logger setup in `maintenance.py`

### D.1 New flag

```python
@app.command("backup-r2")
def backup_r2(
    output: Path = typer.Option(..., "--output", help="Output directory for backup"),
    chunk_size: str = typer.Option(
        "10GiB", "--chunk-size", help="Chunk size (e.g. 10GiB, 500MiB, raw bytes)"
    ),
    concurrency: int = typer.Option(
        8, "--concurrency", min=1, max=64,
        help="Number of concurrent download workers"
    ),
    debug_traces: bool = typer.Option(
        False, "--debug-traces",
        help="Emit per-object and per-phase performance traces to stderr at DEBUG level"
    ),
    format_: str = typer.Option("table", "--format", help="table|json"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Backup entire R2 bucket to a local directory with chunked tar archives."""
    _validate_choice(format_, BACKUP_FORMAT_VALUES, "--format")

    try:
        chunk_size_bytes = parse_size(chunk_size)
    except ValueError as e:
        console.print(f"[red]Invalid --chunk-size: {e}[/red]")
        raise typer.Exit(1)

    if chunk_size_bytes < MIN_CHUNK_SIZE_BYTES:
        console.print(
            f"[red]Chunk size {chunk_size_bytes} is below minimum "
            f"{MIN_CHUNK_SIZE_BYTES} (64MiB)[/red]"
        )
        raise typer.Exit(1)

    config, _ = _load_clients(config_path)
    r2_client = _load_r2(config)

    if format_ == "json":
        progress_console = Console(file=sys.stderr)
    else:
        progress_console = console

    tracer: Optional[BackupTracer] = None
    if debug_traces:
        _configure_r2_backup_debug_logging(progress_console)
        tracer = BackupTracer()

    progress_console.print("[cyan]Building R2 inventory...[/cyan]")
    if tracer is not None:
        tracer.phase_start("inventory")
    inventory = build_inventory(r2_client)
    if tracer is not None:
        tracer.phase_end(
            "inventory",
            objects=inventory.object_count,
            total_bytes=inventory.total_bytes,
        )
    progress_console.print(
        f"[green]Inventory complete: {inventory.object_count} objects, "
        f"{_bytes_to_mb(inventory.total_bytes)} MB[/green]"
    )

    try:
        with Progress(
            SpinnerColumn(),
            BarColumn(),
            TaskProgressColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            TimeElapsedColumn(),
            TextColumn("{task.fields[workers]} workers"),
            TextColumn("{task.fields[objects_done]}/{task.fields[object_count]} objects"),
            console=progress_console,
        ) as progress:
            task = progress.add_task(
                "Backing up...",
                total=inventory.total_bytes,
                workers=0,
                objects_done=0,
                object_count=inventory.object_count,
            )

            base_callback: Optional[Callable[[BackupProgress], None]] = None

            def _on_progress(prog: BackupProgress) -> None:
                progress.update(
                    task,
                    completed=prog.bytes_downloaded,
                    workers=prog.active_workers,
                    objects_done=prog.objects_downloaded,
                )
                if tracer is not None:
                    tracer.bytes_downloaded_sample(
                        prog.bytes_downloaded, prog.active_workers
                    )

            base_callback = _on_progress

            result = write_backup(
                r2_client=r2_client,
                output_dir=output,
                inventory=inventory,
                chunk_size_bytes=chunk_size_bytes,
                concurrency=concurrency,
                on_progress=base_callback,
                tracer=tracer,
            )
    except BackupError as e:
        console.print(f"[red]Backup failed: {e}[/red]")
        raise typer.Exit(1)

    if format_ == "json":
        _print_json_to_stdout(
            {
                "output_dir": str(result.output_dir),
                "object_count": result.object_count,
                "total_mb": _bytes_to_mb(result.total_bytes),
                "chunk_count": result.chunk_count,
            }
        )
    else:
        _print_backup_summary_table(result, output)
```

### D.2 New helper `_configure_r2_backup_debug_logging`

```python
import logging as _stdlogging


def _configure_r2_backup_debug_logging(console: Console) -> None:
    """Attach a DEBUG-level stderr handler to the r2_backup module logger.

    Idempotent: if a handler tagged with the marker attribute is already
    attached, does nothing.
    """
    target = _stdlogging.getLogger(
        "stream_of_worship.admin.services.r2_backup"
    )
    target.setLevel(_stdlogging.DEBUG)
    marker_attr = "_sow_r2_backup_debug_handler"
    for h in target.handlers:
        if getattr(h, marker_attr, False):
            return
    handler = _stdlogging.StreamHandler(console.file)
    handler.setLevel(_stdlogging.DEBUG)
    handler.setFormatter(
        _stdlogging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    setattr(handler, marker_attr, True)
    target.addHandler(handler)
```

### D.3 Imports to add

```python
from stream_of_worship.admin.services.r2_backup import (
    DEFAULT_CHUNK_SIZE_BYTES,
    MIN_CHUNK_SIZE_BYTES,
    BackupError,
    BackupProgress,
    BackupTracer,
    RestoreError,
    VerifyError,
    build_inventory,
    load_manifest,
    parse_size,
    plan_restore,
    restore_from_archive,
    verify_archive,
    write_backup,
)
```

### D.4 Progress-bar vs. trace log interleaving

The Rich `Progress` live-display and the new DEBUG log lines both go to stderr. Rich's live display writes to the terminal on a throttle (~10 Hz); DEBUG log lines write between refreshes. In practice this causes log lines to appear interleaved with progress bar redraws, which is acceptable for diagnostic use.

For cleaner capture, users can redirect stderr to a file (`--format json` mode already sends progress to stderr and JSON output to stdout):

```bash
uv run --extra admin sow-admin maintenance backup-r2 \
    --output /tmp/sow-backup --concurrency 8 --debug-traces \
    --format json -c .../config.toml 2>/tmp/sow-backup-trace.log
```

After the run, `grep -E '^(phase_end|object_download|tar_write|throughput_sample|summary)' /tmp/sow-backup-trace.log` gives a clean trace.

---

## Change E: Test Updates

### E.1 `test_r2_backup.py` — new `TestBackupTracer` class

```python
from stream_of_worship.admin.services.r2_backup import BackupTracer


class TestBackupTracer:
    def test_disabled_when_logger_below_debug(self):
        """Tracer is no-op when logger is not at DEBUG level."""
        import logging
        log = logging.getLogger("test_disabled_tracer")
        log.setLevel(logging.INFO)
        tracer = BackupTracer(logger=log)
        # All methods should be no-ops and not raise
        tracer.phase_start("x")
        tracer.phase_end("x")
        tracer.object_download_trace(key="k", worker="t1", attempt=0,
                                     conn_ms=1.0, stream_ms=2.0, bytes_read=10, retries=0)
        tracer.tar_write_trace(idx=0, key="k", wait_ms=1.0,
                                tar_write_ms=2.0, bytes_written=10)
        tracer.bytes_downloaded_sample(100, 1)
        tracer.finalize(total_objects=1, total_bytes=10)
        # No assert needed; absence of error proves no-op

    def test_phase_start_end_emits_debug(self, caplog):
        import logging
        with caplog.at_level(logging.DEBUG, logger="stream_of_worship.admin.services.r2_backup"):
            tracer = BackupTracer()
            tracer.phase_start("phase_x")
            tracer.phase_end("phase_x", foo="bar")
        assert any("phase_end name=phase_x" in r.message for r in caplog.records)
        assert any("foo=bar" in r.message for r in caplog.records)

    def test_object_download_trace_updates_accumulators(self, caplog):
        import logging
        with caplog.at_level(logging.DEBUG, logger="stream_of_worship.admin.services.r2_backup"):
            tracer = BackupTracer()
            tracer.object_download_trace(key="a", worker="t1", attempt=0,
                                         conn_ms=10.0, stream_ms=100.0,
                                         bytes_read=1024 * 1024, retries=0)
        assert any("object_download key=a" in r.message for r in caplog.records)
        assert tracer._bytes_downloaded == 1024 * 1024
        assert tracer._sum_download_ms == 100.0
        assert tracer._sum_conn_ms == 10.0
        assert tracer._max_object_download_ms == 110.0
        assert tracer._max_object_download_key == "a"

    def test_tar_write_trace_updates_accumulators(self, caplog):
        import logging
        with caplog.at_level(logging.DEBUG, logger="stream_of_worship.admin.services.r2_backup"):
            tracer = BackupTracer()
            tracer.tar_write_trace(idx=0, key="a", wait_ms=50.0,
                                   tar_write_ms=30.0, bytes_written=1024 * 1024)
            tracer.tar_write_trace(idx=1, key="b", wait_ms=10.0,
                                   tar_write_ms=20.0, bytes_written=1024 * 1024)
        assert tracer._sum_wait_ms == 60.0
        assert tracer._sum_tar_write_ms == 50.0
        assert tracer._bytes_written == 2 * 1024 * 1024
        assert tracer._object_count_written == 2
        assert any("wait_is_bottleneck=yes" in r.message for r in caplog.records)  # 50 > 30
        assert any("wait_is_bottleneck=no" in r.message for r in caplog.records)   # 10 < 20

    def test_bytes_downloaded_sample_throttles_logs(self, caplog):
        """Throughput log emitted at most once per THROUGHPUT_SAMPLE_INTERVAL."""
        import logging
        with caplog.at_level(logging.DEBUG, logger="stream_of_worship.admin.services.r2_backup"):
            tracer = BackupTracer()
            tracer._run_start = 0.0  # enable t+ logging
            for i in range(5):
                tracer.bytes_downloaded_sample(i * 1024 * 1024, 8)
            # All 5 calls recorded as samples
            assert len(tracer._throughput_samples) == 5
            assert tracer._peak_workers == 8
            # At most 1 throughput_sample log line within the 5s window
            sample_logs = [r for r in caplog.records if "throughput_sample" in r.message]
            assert len(sample_logs) <= 1

    def test_finalize_emits_summary_with_network_saturation_field(self, caplog):
        import logging
        with caplog.at_level(logging.DEBUG, logger="stream_of_worship.admin.services.r2_backup"):
            tracer = BackupTracer()
            tracer._run_start = 0.0
            tracer.bytes_downloaded_sample(0, 4)
            # Simulate wall-clock passage
            import time as _t
            new_t = _t.time() + 60  # 60s later in real time
            tracer._throughput_samples.append(
                (new_t, 100 * 1024 * 1024, 4)
            )
            tracer._last_throughput_log = 0  # allow next log
            tracer._bytes_downloaded = 100 * 1024 * 1024
            tracer._sum_download_ms = 60_000.0  # 1 worker × 60s
            tracer._peak_workers = 4
            tracer.finalize(total_objects=100, total_bytes=100 * 1024 * 1024)
        summary_logs = [r for r in caplog.records if r.message.startswith("summary")]
        assert len(summary_logs) == 1
        msg = summary_logs[0].message
        assert "aggregate_mbps" in msg
        assert "single_worker_avg_mbps" in msg
        assert "network_saturated" in msg
        assert "peak_workers=4" in msg

    def test_thread_safety(self):
        """Concurrent calls to BackupTracer methods do not corrupt accumulators."""
        import logging
        import threading
        log = logging.getLogger("test_tracer_thread_safety")
        log.setLevel(logging.DEBUG)
        tracer = BackupTracer(logger=log)
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()
            for i in range(100):
                tracer.object_download_trace(
                    key=f"k{i}", worker="w", attempt=0, conn_ms=1.0,
                    stream_ms=1.0, bytes_read=100, retries=0,
                )
                tracer.bytes_downloaded_sample(i * 100, 8)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert tracer._object_count_downloaded == 800
        assert tracer._bytes_downloaded == 80000


class TestWriteBackupTracerIntegration:
    """Verify write_backup plumbs tracer through and emits expected traces."""

    def test_debug_traces_emits_object_and_tar_traces(self, tmp_path, caplog):
        import logging
        objects = [
            {"key": "a/file1", "size": 100, "etag": "etag1", "data": b"x" * 100},
            {"key": "a/file2", "size": 200, "etag": "etag2", "data": b"y" * 200},
        ]
        r2 = _make_r2_mock(objects)
        inventory = build_inventory(r2)
        output = tmp_path / "backup"

        with caplog.at_level(logging.DEBUG, logger="stream_of_worship.admin.services.r2_backup"):
            tracer = BackupTracer()
            result = write_backup(
                r2, output, inventory, tracer=tracer,
            )

        assert result.object_count == 2

        # At least one object_download and one tar_write log per object
        download_logs = [r for r in caplog.records if "object_download" in r.message]
        tar_logs = [r for r in caplog.records if "tar_write" in r.message]
        phase_logs = [r for r in caplog.records if "phase_end" in r.message]
        summary_logs = [r for r in caplog.records if r.message.startswith("summary")]
        assert len(download_logs) >= 2
        assert len(tar_logs) >= 2
        assert any("phase_end name=download_phase" in r.message for r in phase_logs)
        assert any("phase_end name=total" in r.message for r in phase_logs)
        assert len(summary_logs) == 1

    def test_write_backup_without_tracer_no_logs(self, tmp_path, caplog):
        """Default (tracer=None) emits no backup DEBUG logs."""
        import logging
        objects = [
            {"key": "a/file1", "size": 100, "etag": "etag1", "data": b"x" * 100},
        ]
        r2 = _make_r2_mock(objects)
        inventory = build_inventory(r2)
        output = tmp_path / "backup"

        with caplog.at_level(logging.DEBUG, logger="stream_of_worship.admin.services.r2_backup"):
            result = write_backup(r2, output, inventory)
        assert result.object_count == 1
        assert not [r for r in caplog.records if "object_download" in r.message]
        assert not [r for r in caplog.records if "tar_write" in r.message]
        assert not [r for r in caplog.records if "summary" in r.message]
```

### E.2 `test_r2_backup_commands.py` — `--debug-traces` flag test

```python
def test_backup_r2_debug_traces_flag_passes_tracer(monkeypatch, tmp_path):
    """--debug-traces constructs a BackupTracer and passes it to write_backup."""
    captured = {}

    def _fake_write_backup(*, r2_client, output_dir, inventory,
                           chunk_size_bytes, concurrency, on_progress, tracer):
        captured["tracer"] = tracer
        captured["concurrency"] = concurrency
        from stream_of_worship.admin.services.r2_backup import BackupResult
        from pathlib import Path
        return BackupResult(
            output_dir=Path(str(output_dir)),
            object_count=0,
            total_bytes=0,
            chunk_count=0,
            manifest={"version": 4, "objects": []},
        )

    monkeypatch.setattr(
        "stream_of_worship.admin.commands.maintenance.write_backup",
        _fake_write_backup,
    )
    # Also mock build_inventory to avoid R2 calls
    monkeypatch.setattr(
        "stream_of_worship.admin.commands.maintenance.build_inventory",
        lambda r2_client: _make_minimal_inventory(),
    )
    # Mock config + R2 client load
    monkeypatch.setattr(
        "stream_of_worship.admin.commands.maintenance._load_clients",
        lambda config_path: (_make_fake_config(), None),
    )
    monkeypatch.setattr(
        "stream_of_worship.admin.commands.maintenance._load_r2",
        lambda config: _make_fake_r2(),
    )

    runner = CliRunner()
    result = runner.invoke(app, [
        "maintenance", "backup-r2",
        "--output", str(tmp_path / "out"),
        "--debug-traces",
    ])
    assert result.exit_code == 0, result.output
    assert captured["tracer"] is not None
    assert captured["tracer"].__class__.__name__ == "BackupTracer"


def test_backup_r2_default_no_tracer_passes_none(monkeypatch, tmp_path):
    """Without --debug-traces, tracer is None."""
    captured = {}
    # ... same setup, no --debug-traces ...
    assert captured["tracer"] is None
```

Existing tests in `test_r2_backup_commands.py` (e.g. `test_backup_concurrency_flag`) mock `write_backup` with `_fake_write_backup` that previously did not accept `tracer` kwarg. **Update those mocks** to accept `tracer=None` (or use `**kwargs`) so they still pass after `write_backup` is called with `tracer=tracer`.

### E.3 Existing tests unchanged

- `TestHashingReader`, `TestBackupProgress`, `TestConcurrentBackup` — unchanged. `BackupTracer` is an additive class; `write_backup` and `_download_object_to_tempfile` gain a new default-`None` parameter, so existing tests calling them without `tracer=` continue to work.
- `TestVerifyArchive`, `TestPlanRestore`, `TestRestoreFromArchive` — unchanged; no tracer plumbing touches verify/restore paths.

---

## Expected trace output

### After enabling `--debug-traces`

Sample stderr output (timestamps truncated):

```
00:00:01 DEBUG stream_of_worship.admin.services.r2_backup: phase_end name=inventory elapsed_ms=2400.5 objects=679 total_bytes=12884901888
00:00:01 DEBUG stream_of_worship.admin.services.r2_backup: phase_end name=disk_check elapsed_ms=12.3 required_bytes=27057364276
00:00:01 DEBUG stream_of_worship.admin.services.r2_backup: phase_end name=setup elapsed_ms=4.1
# First per-object download (worker thread):
00:00:02 DEBUG stream_of_worship.admin.services.r2_backup: object_download key=abc/audio.mp3 worker=ThreadPoolExecutor-0_0 attempt=0 conn_ms=145.2 stream_ms=2390.4 bytes=18874368 download_mbps=7.41 retries=0
00:00:02 DEBUG stream_of_worship.admin.services.r2_backup: object_download key=def/audio.mp3 worker=ThreadPoolExecutor-0_1 attempt=0 conn_ms=132.7 stream_ms=2210.1 bytes=17203200 download_mbps=6.88 retries=0
00:00:02 DEBUG stream_of_worship.admin.services.r2_backup: tar_write idx=0 key=abc/audio.mp3 wait_ms=2535.6 tar_write_ms=42.1 bytes=18874368 wait_is_bottleneck=yes
00:00:02 DEBUG stream_of_worship.admin.services.r2_backup: tar_write idx=1 key=def/audio.mp3 wait_ms=0.1 tar_write_ms=38.6 bytes=17203200 wait_is_bottleneck=no
# Throughput sample every 5s:
00:00:06 DEBUG stream_of_worship.admin.services.r2_backup: throughput_sample t+5.0s workers=8 downloaded_mib=120.50 aggregate_mbps=8.04 peak_workers=8
00:00:11 DEBUG stream_of_worship.admin.services.r2_backup: throughput_sample t+10.0s workers=8 downloaded_mib=240.80 aggregate_mbps=8.02 peak_workers=8
# ... (continues) ...
# Phase totals:
00:25:13 DEBUG stream_of_worship.admin.services.r2_backup: phase_end name=download_phase elapsed_ms=1512324.4 objects=679 total_bytes=12884901888
00:25:13 DEBUG stream_of_worship.admin.services.r2_backup: summary total_objects=679 total_bytes=12884901888 aggregate_mbps=8.31 single_worker_avg_mbps=1.04 peak_workers=8 retries_total=0 sum_download_ms=1510000.0 sum_conn_ms=98000.0 sum_wait_ms=1480000.0 sum_tar_write_ms=32000.0 wait_pct=97.9 tar_write_pct=2.1 max_object_download_ms=5120.0 max_object_download_key=qrs/audio.mp3 network_saturated=yes
```

### Interpreting the above (example numbers)

- `aggregate_mbps=8.31`, `single_worker_avg_mbps=1.04`, `peak_workers=8`: scaling efficiency = 8.31 / (1.04 × 8) = 0.997 → **near-linear scaling** → network is **NOT** saturated.
  - (If `aggregate_mbps` were 8.31 with `single_worker_avg_mbps` 1.04 and peak_workers 32, scaling efficiency = 8.31 / 33.3 = 0.25 → saturation at ~8 MB/s regardless of workers.)
- `wait_pct=97.9`, `tar_write_pct=2.1`: main thread spends almost all time blocked on `future.result()` → **head-of-line blocking is real**, but it's because downloads dominate, not because tar writes are the cap.
- `max_object_download_ms=5120.0`: one 5-second outlier. Not the bottleneck given aggregate throughput.
- `retries_total=0`: no retry storms.

So in this example, the diagnosis would be "per-worker throughput caps at ~1 MB/s, and aggregate maxes out at ~8 MB/s — adding workers to 16 should double throughput. If not, network is the cap."

---

## Verification

```bash
# Run R2 backup tests including new BackupTracer tests
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest \
  tests/admin/test_r2_backup.py tests/admin/test_r2_backup_commands.py -v

# Run all admin tests
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/admin/ -v

# Verify --debug-traces is wired in --help output
uv run --extra admin sow-admin maintenance backup-r2 --help | grep -- '--debug-traces'

# Manual smoke test (requires R2 credentials, real bucket)
uv run --extra admin sow-admin maintenance backup-r2 \
  --output /tmp/sow-r2-backup-test \
  --concurrency 8 \
  --debug-traces \
  --format json \
  -c ~/.config/stream-of-worship-admin/config.toml \
  2>/tmp/sow-r2-backup-trace.log

# Analyze the trace
grep -E '^(phase_end|object_download|tar_write|throughput_sample|summary)' \
  /tmp/sow-r2-backup-trace.log | less
```

---

## Assumptions and Out of Scope

**Assumptions:**

- `threading.current_thread().name` distinguishes workers (Python's `ThreadPoolExecutor` names them `ThreadPoolExecutor-N_M`).
- `time.monotonic()` has sufficient resolution (~1 µs on Linux/macOS) for the trace timings.
- `caplog` (pytest fixture) captures logger output regardless of whether handlers are attached, so the test logger need not have an explicit DEBUG handler set.
- Rich `Progress.update()` thread-safety (established in v3) continues to hold; the wrapped `_on_progress` callback now also feeds `tracer.bytes_downloaded_sample`, but that method only modifies tracer-local state (no Rich calls).
- The `--debug-traces` flag adds negligible runtime overhead (<1%) when disabled: all trace methods short-circuit on `_enabled` and all `time.monotonic()` calls are gated by `if tracer is not None:`.

**Out of scope:**

- **Fixing** the bottleneck — this spec only adds traces. The fix (e.g. `concurrent.futures.as_completed` to consume downloads in completion order, parallelize tar writes, or stream directly to tar members) will be the subject of a follow-up spec once the bottleneck is confirmed from real trace data.
- Wrapping `temp_file.write` to isolate network read time from disk write time (see B.5 limitation).
- Tracing `verify_archive` / `restore_from_archive` performance (not part of the slow operation).
- Tracing botocore / urllib3 internals (HTTP/2 frame timings, TLS handshake breakdowns). Per-object `conn_ms` is sufficient to flag if connection setup dominates, and further investigation can use `boto3.set_stream_logger('botocore')` directly.
- Tracing disk I/O at the syscall level (requires `iostat`/`perf` — out of band).
