# Admin R2 Backup: Progress Indicator v3 — Download-Aware Progress, Worker Count, Streaming MD5

## Summary

Fix two issues with the `backup-r2` progress bar after the concurrent-downloads v2 implementation:

1. **No worker count**: The progress bar doesn't show how many download threads are active.
2. **Progress doesn't reflect parallelism**: The progress bar's `completed` metric tracks bytes written to tar (sequential, main-thread), not bytes downloaded (parallel, worker-threads). This causes the bar to appear stuck during slow head-of-queue downloads, wild ETA fluctuations, and understated throughput.

Additionally fix a latent bottleneck discovered during analysis:

3. **MD5 body check re-reads entire temp file**: After downloading to a temp file, `_download_object_to_tempfile()` re-reads the entire file to compute MD5. This doubles disk I/O per object and can limit throughput on disk-bound systems.

### Root Cause Analysis

**Progress stuck / ETA fluctuation:**

In `write_backup()` (`r2_backup.py:465-519`), all download futures are submitted upfront, but the main thread processes results **in submission order**:

```python
for idx, inv_obj in enumerate(inventory.objects):
    future = futures[idx]
    download_result = future.result()  # blocks until this specific future completes
    # ... write to tar ...
    on_progress(idx + 1, bytes_completed)  # only called after tar write
```

If object 0 is slow (60s) but objects 1-7 finish fast (5s each), the progress bar stays at 0% for 60 seconds, then rapidly jumps. Rich's `TransferSpeedColumn` and `TimeRemainingColumn` compute from these bursty tar-write events, producing wild ETA and understated throughput.

**MD5 re-read:**

In `_download_object_to_tempfile()` (`r2_backup.py:307-320`), after streaming the download to a temp file (computing SHA-256 along the way), the code opens and re-reads the entire temp file to compute MD5 for single-part ETag verification. For a 40 MB audio file, this is an extra 40 MB of disk reads per object.

## Decisions

| Topic | Decision |
|---|---|
| Progress metric | Bytes downloaded across all workers (not bytes written to tar) |
| Worker count display | Inline text field: `{workers} workers` in the progress bar |
| Elapsed time | `TimeElapsedColumn` added to the progress bar |
| MD5 computation | Streaming during download via `HashingReader` (alongside SHA-256) |
| Callback threading | `on_progress` called from worker threads (Rich `Progress.update()` is thread-safe) |
| Callback throttling | Max ~10 calls/second via time-based throttle in `BackupProgress` |
| `on_progress` signature | Changed from `Callable[[int, int], None]` to `Callable[[BackupProgress], None]` |
| MANIFEST_VERSION | No change (stays at 4 — no archive format change) |

## Files to Modify

1. `src/stream_of_worship/admin/services/r2_backup.py` — `HashingReader`, new `BackupProgress` class, `_download_object_to_tempfile()`, `write_backup()`
2. `src/stream_of_worship/admin/commands/maintenance.py` — Progress bar columns, callback signature
3. `tests/admin/test_r2_backup.py` — Update `HashingReader` tests, `on_progress` test, add `BackupProgress` tests
4. `tests/admin/test_r2_backup_commands.py` — No changes needed (mocks `write_backup`)

---

## Change A: Streaming MD5 in `HashingReader` (`r2_backup.py`)

### A.1 Add MD5 hasher and `on_read` callback

```python
class HashingReader:
    """A wrapper around a readable stream that computes SHA-256 and MD5 as data is read.

    Also tracks total bytes read for short-read detection, and optionally
    invokes a callback after each read for progress reporting.
    """

    def __init__(self, source, on_read: Optional[Callable[[int], None]] = None):
        self._source = source
        self._sha256_hasher = hashlib.sha256()
        self._md5_hasher = hashlib.md5()
        self.bytes_read = 0
        self._on_read = on_read

    def read(self, size: int = -1) -> bytes:
        data = self._source.read(size)
        if data:
            self._sha256_hasher.update(data)
            self._md5_hasher.update(data)
            self.bytes_read += len(data)
            if self._on_read is not None:
                self._on_read(len(data))
        return data

    @property
    def sha256_hex(self) -> str:
        return self._sha256_hasher.hexdigest()

    @property
    def md5_hex(self) -> str:
        return self._md5_hasher.hexdigest()

    def close(self) -> None:
        if hasattr(self._source, "close"):
            self._source.close()
```

**Key changes:**
- `_hasher` (SHA-256 only) → `_sha256_hasher` + `_md5_hasher`
- New `on_read` callback invoked after each `read()` with the chunk byte count
- New `md5_hex` property

### A.2 Use streaming MD5 in `_download_object_to_tempfile()`

Replace the re-read MD5 block (`r2_backup.py:307-320`):

**Before (re-reads entire file):**
```python
if "-" not in get_etag:
    md5_hasher = hashlib.md5()
    with open(temp_path, "rb") as md5_file:
        while True:
            md5_chunk = md5_file.read(65536)
            if not md5_chunk:
                break
            md5_hasher.update(md5_chunk)
    md5_hex = md5_hasher.hexdigest()
    if md5_hex != get_etag:
        raise BackupError(...)
```

**After (uses streaming MD5 from HashingReader):**
```python
if "-" not in get_etag:
    if hashing_reader.md5_hex != get_etag:
        raise BackupError(
            f"Object {inv_obj.key} MD5 mismatch: ETag {get_etag}, "
            f"computed {hashing_reader.md5_hex}"
        )
```

This eliminates the entire re-read. MD5 is computed during the download stream alongside SHA-256, at zero extra I/O cost.

---

## Change B: New `BackupProgress` Class (`r2_backup.py`)

### B.1 Thread-safe progress tracker

```python
import time


class BackupProgress:
    """Thread-safe progress tracker for concurrent backup downloads.

    Tracks bytes downloaded, objects downloaded, and active worker count
    across all download threads. Designed to be safely updated from worker
    threads and read from the progress callback.
    """

    def __init__(
        self,
        total_objects: int,
        total_bytes: int,
        on_progress: Optional[Callable[["BackupProgress"], None]] = None,
        min_report_interval: float = 0.1,
    ):
        self._lock = threading.Lock()
        self._bytes_downloaded = 0
        self._objects_downloaded = 0
        self._active_workers = 0
        self._objects_written = 0
        self._bytes_written = 0
        self.total_objects = total_objects
        self.total_bytes = total_bytes
        self._on_progress = on_progress
        self._min_report_interval = min_report_interval
        self._last_report_time = 0.0

    def worker_started(self) -> None:
        with self._lock:
            self._active_workers += 1
        self._maybe_report()

    def worker_finished(self) -> None:
        with self._lock:
            self._active_workers -= 1
            self._objects_downloaded += 1
        self._maybe_report()

    def add_bytes(self, n: int) -> None:
        with self._lock:
            self._bytes_downloaded += n
        self._maybe_report()

    def object_written(self, bytes_written: int) -> None:
        with self._lock:
            self._objects_written += 1
            self._bytes_written += bytes_written
        self._maybe_report()

    def _maybe_report(self) -> None:
        """Call on_progress if enough time has elapsed since last report."""
        if self._on_progress is None:
            return
        now = time.monotonic()
        with self._lock:
            if now - self._last_report_time < self._min_report_interval:
                return
            self._last_report_time = now
        self._on_progress(self)

    @property
    def bytes_downloaded(self) -> int:
        with self._lock:
            return self._bytes_downloaded

    @property
    def objects_downloaded(self) -> int:
        with self._lock:
            return self._objects_downloaded

    @property
    def active_workers(self) -> int:
        with self._lock:
            return self._active_workers

    @property
    def objects_written(self) -> int:
        with self._lock:
            return self._objects_written

    @property
    def bytes_written(self) -> int:
        with self._lock:
            return self._bytes_written
```

**Design notes:**
- All counter updates are atomic (lock-protected).
- `_maybe_report()` throttles `on_progress` calls to at most ~10/second, preventing excessive lock contention from Rich's `Progress.update()`.
- `on_progress` is called from worker threads. Rich's `Progress.update()` is thread-safe (uses an internal lock), so this is safe.
- `min_report_interval=0.1` (100ms) balances smoothness vs. overhead. For 679 objects / 12 GB, this yields ~10 updates/second regardless of object count or size.

---

## Change C: Wire `BackupProgress` into `_download_object_to_tempfile()` (`r2_backup.py`)

### C.1 New `progress` parameter

```python
def _download_object_to_tempfile(
    r2_client: R2Client,
    inv_obj: InventoryObject,
    temp_dir: Path,
    progress: Optional[BackupProgress] = None,
    max_retries: int = 2,
) -> DownloadResult:
```

### C.2 Update download function to use progress tracker

Inside `_download_object_to_tempfile()`, wrap the download with progress tracking:

```python
    last_error: Optional[str] = None
    for attempt in range(max_retries + 1):
        temp_path: Optional[Path] = None
        try:
            if progress is not None:
                progress.worker_started()

            resp = r2_client.get_object_stream(inv_obj.key)
            body = resp["body"]
            content_length = resp["content_length"]
            get_etag = resp["etag"]

            try:
                temp_dir.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=temp_dir, delete=False) as temp_file:
                    temp_path = Path(temp_file.name)

                    on_read = progress.add_bytes if progress is not None else None
                    hashing_reader = HashingReader(body, on_read=on_read)
                    shutil.copyfileobj(hashing_reader, temp_file, length=COPY_BUFFER_SIZE)

                # ... existing validation (short read, size mismatch, ETag check) ...

                # MD5 body check — now uses streaming MD5 from HashingReader
                if "-" not in get_etag:
                    if hashing_reader.md5_hex != get_etag:
                        raise BackupError(
                            f"Object {inv_obj.key} MD5 mismatch: ETag {get_etag}, "
                            f"computed {hashing_reader.md5_hex}"
                        )

                # ... existing return DownloadResult ...

            finally:
                body.close()

        except Exception as e:
            last_error = str(e)
            if temp_path is not None:
                try:
                    temp_path.unlink()
                except OSError:
                    pass
            if attempt < max_retries:
                continue
            raise BackupError(
                f"Failed to backup {inv_obj.key} after retries: {last_error}"
            ) from e
        finally:
            if progress is not None:
                progress.worker_finished()
```

**Key changes:**
- `progress.worker_started()` / `progress.worker_finished()` bracket each download attempt (in `finally` so workers are always decremented, even on failure).
- `HashingReader` receives `on_read=progress.add_bytes` so every chunk read updates the download byte counter.
- MD5 check uses `hashing_reader.md5_hex` (streaming) instead of re-reading the file.
- `worker_finished()` is in the outermost `finally` block so it runs exactly once per call, regardless of retry attempts.

**Note on retry + worker_started/finished:** If a download fails and retries, `worker_started()` is called at the top of each attempt and `worker_finished()` in the `finally`. This means for N retries, `worker_started` is called N times and `worker_finished` is called N times, keeping the counter balanced. The `objects_downloaded` counter is only incremented on `worker_finished()`, so it will be incremented once per retry attempt. This is acceptable — the final count will be correct (one increment per successful or exhausted-retry download). If exact `objects_downloaded` accuracy is needed, move `worker_finished()` to only run on the last attempt. For simplicity, the current design is sufficient since the progress bar primarily tracks `bytes_downloaded` and `active_workers`.

**Correction:** To keep `objects_downloaded` accurate (one increment per object, not per retry), `worker_finished()` should only increment `objects_downloaded` on the final attempt. Revised approach:

```python
    for attempt in range(max_retries + 1):
        temp_path: Optional[Path] = None
        try:
            if progress is not None:
                progress.worker_started()

            # ... download + validate ...

            if progress is not None:
                progress.worker_finished()
            return DownloadResult(...)

        except Exception as e:
            # ... error handling ...
        finally:
            if progress is not None:
                progress.worker_finished()
```

Wait — this would call `worker_finished()` twice on success (once before return, once in finally). Better design: only call `worker_finished()` in `finally`, and don't increment `objects_downloaded` in `worker_finished()`. Instead, increment `objects_downloaded` separately on success:

```python
    def worker_finished(self, success: bool = True) -> None:
        with self._lock:
            self._active_workers -= 1
            if success:
                self._objects_downloaded += 1
        self._maybe_report()
```

And in `_download_object_to_tempfile`:
```python
    finally:
        if progress is not None:
            progress.worker_finished(success=(return_value is not None))
```

Actually, the simplest correct approach: `worker_started()` is called once per attempt. `worker_finished()` is called once per attempt in `finally`. `objects_downloaded` is NOT incremented in `worker_finished()` — it's incremented in `write_backup()` after `future.result()` succeeds. This separates "worker is done" from "object was successfully downloaded."

**Final design for BackupProgress:**

```python
    def worker_started(self) -> None:
        with self._lock:
            self._active_workers += 1
        self._maybe_report()

    def worker_finished(self) -> None:
        with self._lock:
            self._active_workers -= 1
        self._maybe_report()

    def mark_object_downloaded(self, bytes_downloaded: int) -> None:
        with self._lock:
            self._objects_downloaded += 1
            self._bytes_downloaded += bytes_downloaded
        self._maybe_report()
```

Wait, but `add_bytes` already increments `_bytes_downloaded`. If we also increment in `mark_object_downloaded`, we'd double-count. 

The issue is that `add_bytes` is called during streaming (every 1MB chunk), and by the time the download finishes, all bytes have been added. So `mark_object_downloaded` should only increment `_objects_downloaded`, not `_bytes_downloaded`:

```python
    def mark_object_downloaded(self) -> None:
        with self._lock:
            self._objects_downloaded += 1
        self._maybe_report()
```

And in `write_backup()`, after `future.result()` succeeds:
```python
    download_result = future.result()
    if progress is not None:
        progress.mark_object_downloaded()
```

This is clean:
- `add_bytes(n)` — called during streaming, updates `_bytes_downloaded`
- `worker_started()` / `worker_finished()` — bracket the download, update `_active_workers`
- `mark_object_downloaded()` — called after successful download, updates `_objects_downloaded`
- `object_written(bytes)` — called after tar write, updates `_objects_written` and `_bytes_written`

---

## Change D: Wire `BackupProgress` into `write_backup()` (`r2_backup.py`)

### D.1 Change `on_progress` signature

```python
def write_backup(
    r2_client: R2Client,
    output_dir: Path,
    inventory: Inventory,
    chunk_size_bytes: int = DEFAULT_CHUNK_SIZE_BYTES,
    concurrency: int = DEFAULT_CONCURRENCY,
    on_progress: Optional[Callable[[BackupProgress], None]] = None,
) -> BackupResult:
```

**Breaking change:** `on_progress` was `Callable[[int, int], None]` (objects_done, bytes_done). Now `Callable[[BackupProgress], None]`.

### D.2 Create `BackupProgress` and pass to workers

```python
    progress = BackupProgress(
        total_objects=inventory.object_count,
        total_bytes=inventory.total_bytes,
        on_progress=on_progress,
    )

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {
            idx: executor.submit(
                _download_object_to_tempfile, r2_client, inv_obj, temp_dir, progress
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
                try:
                    download_result = future.result()
                except BaseException:
                    for f in futures.values():
                        f.cancel()
                    raise

                if progress is not None:
                    progress.mark_object_downloaded()

                _track_temp(download_result.temp_path)
                try:
                    # ... existing tar write logic ...

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
```

**Key changes:**
- `BackupProgress` created with `on_progress` callback and totals.
- Passed to `_download_object_to_tempfile` as the `progress` parameter.
- `progress.mark_object_downloaded()` called after successful `future.result()`.
- `progress.object_written(inv_obj.size)` called after tar write (replaces old `on_progress(idx + 1, bytes_completed)` call).
- The old `on_progress(idx + 1, bytes_completed)` call is removed — progress is now reported via `BackupProgress._maybe_report()` which is called from `add_bytes`, `worker_started`, `worker_finished`, `mark_object_downloaded`, and `object_written`.

### D.3 Remove old `bytes_completed` tracking

The old `bytes_completed` variable and `on_progress(idx + 1, bytes_completed)` call (lines 463, 510-513) are removed. `BackupProgress` handles all progress tracking.

---

## Change E: Update Progress Bar in `maintenance.py`

### E.1 Add `TimeElapsedColumn` import

```python
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
```

### E.2 Update progress bar columns and callback

```python
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

            def _on_progress(prog: BackupProgress) -> None:
                progress.update(
                    task,
                    completed=prog.bytes_downloaded,
                    workers=prog.active_workers,
                    objects_done=prog.objects_downloaded,
                )

            result = write_backup(
                r2_client=r2_client,
                output_dir=output,
                inventory=inventory,
                chunk_size_bytes=chunk_size_bytes,
                concurrency=concurrency,
                on_progress=_on_progress,
            )
    except BackupError as e:
        console.print(f"[red]Backup failed: {e}[/red]")
        raise typer.Exit(1)
```

**Key changes:**
- `completed=prog.bytes_downloaded` (was `bytes_done` = bytes written to tar). Now reflects actual download progress across all parallel workers.
- `workers=prog.active_workers` — new field showing active download threads.
- `objects_done=prog.objects_downloaded` — now reflects downloaded objects, not written objects.
- `TimeElapsedColumn()` — new column showing elapsed time.
- Removed the `TextColumn("[progress.description]{task.description}")` column (description was always "Backing up...", not useful inline).
- `BackupProgress` import added to imports from `r2_backup`.

### E.3 Add `BackupProgress` to imports

```python
from stream_of_worship.admin.services.r2_backup import (
    DEFAULT_CHUNK_SIZE_BYTES,
    MIN_CHUNK_SIZE_BYTES,
    BackupError,
    BackupProgress,
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

---

## Change F: Test Updates

### F.1 `test_r2_backup.py` — `TestHashingReader`

**Update existing tests** to verify MD5 computation and `on_read` callback:

```python
class TestHashingReader:
    def test_reads_and_hashes(self):
        data = b"hello world"
        source = io.BytesIO(data)
        reader = HashingReader(source)
        result = reader.read()
        assert result == data
        assert reader.bytes_read == len(data)
        assert reader.sha256_hex == hashlib.sha256(data).hexdigest()
        assert reader.md5_hex == hashlib.md5(data).hexdigest()

    def test_partial_reads(self):
        data = b"hello world"
        source = io.BytesIO(data)
        reader = HashingReader(source)
        chunk1 = reader.read(5)
        chunk2 = reader.read(6)
        assert chunk1 == b"hello"
        assert chunk2 == b" world"
        assert reader.bytes_read == len(data)
        assert reader.sha256_hex == hashlib.sha256(data).hexdigest()
        assert reader.md5_hex == hashlib.md5(data).hexdigest()

    def test_empty_read(self):
        source = io.BytesIO(b"")
        reader = HashingReader(source)
        result = reader.read()
        assert result == b""
        assert reader.bytes_read == 0
        assert reader.sha256_hex == hashlib.sha256(b"").hexdigest()
        assert reader.md5_hex == hashlib.md5(b"").hexdigest()

    def test_close(self):
        source = MagicMock()
        reader = HashingReader(source)
        reader.close()
        source.close.assert_called_once()

    def test_on_read_callback(self):
        """on_read callback is invoked with chunk byte count after each read."""
        data = b"hello world"
        source = io.BytesIO(data)
        calls = []
        reader = HashingReader(source, on_read=lambda n: calls.append(n))
        reader.read(5)
        reader.read(6)
        assert calls == [5, 6]

    def test_on_read_not_called_on_empty_read(self):
        """on_read is not called when read returns empty bytes."""
        source = io.BytesIO(b"")
        reader = HashingReader(source, on_read=lambda n: pytest.fail("should not be called"))
        reader.read()
```

### F.2 `test_r2_backup.py` — `TestWriteBackupProgress`

**Update `test_on_progress_called_with_correct_counts`** for new callback signature:

```python
class TestWriteBackupProgress:
    def test_on_progress_called_with_correct_counts(self, tmp_path):
        """write_backup calls on_progress with BackupProgress reflecting downloads."""
        objects = [
            {"key": "a/file1", "size": 100, "etag": "etag1", "data": b"x" * 100},
            {"key": "a/file2", "size": 200, "etag": "etag2", "data": b"y" * 200},
        ]
        r2 = _make_r2_mock(objects)
        inventory = build_inventory(r2)
        output = tmp_path / "backup"

        last_progress = None

        def _on_progress(prog: BackupProgress) -> None:
            nonlocal last_progress
            last_progress = prog

        result = write_backup(r2, output, inventory, on_progress=_on_progress)

        assert last_progress is not None
        assert last_progress.bytes_downloaded == 300
        assert last_progress.objects_downloaded == 2
        assert last_progress.active_workers == 0
        assert last_progress.objects_written == 2
        assert last_progress.bytes_written == 300
        assert result.object_count == 2
        assert result.total_bytes == 300

    def test_on_progress_none_works(self, tmp_path):
        """write_backup works with on_progress=None (default)."""
        objects = [
            {"key": "a/file1", "size": 100, "etag": "etag1", "data": b"x" * 100},
        ]
        r2 = _make_r2_mock(objects)
        inventory = build_inventory(r2)
        output = tmp_path / "backup"

        result = write_backup(r2, output, inventory)

        assert result.object_count == 1
```

### F.3 `test_r2_backup.py` — New `TestBackupProgress` class

```python
class TestBackupProgress:
    def test_initial_state(self):
        prog = BackupProgress(total_objects=10, total_bytes=1000)
        assert prog.bytes_downloaded == 0
        assert prog.objects_downloaded == 0
        assert prog.active_workers == 0
        assert prog.objects_written == 0
        assert prog.bytes_written == 0
        assert prog.total_objects == 10
        assert prog.total_bytes == 1000

    def test_worker_started_finished(self):
        prog = BackupProgress(total_objects=10, total_bytes=1000)
        prog.worker_started()
        assert prog.active_workers == 1
        prog.worker_started()
        assert prog.active_workers == 2
        prog.worker_finished()
        assert prog.active_workers == 1
        prog.worker_finished()
        assert prog.active_workers == 0

    def test_add_bytes(self):
        prog = BackupProgress(total_objects=10, total_bytes=1000)
        prog.add_bytes(100)
        prog.add_bytes(200)
        assert prog.bytes_downloaded == 300

    def test_mark_object_downloaded(self):
        prog = BackupProgress(total_objects=10, total_bytes=1000)
        prog.mark_object_downloaded()
        prog.mark_object_downloaded()
        assert prog.objects_downloaded == 2

    def test_object_written(self):
        prog = BackupProgress(total_objects=10, total_bytes=1000)
        prog.object_written(100)
        prog.object_written(200)
        assert prog.objects_written == 2
        assert prog.bytes_written == 300

    def test_on_progress_callback_invoked(self):
        calls = []
        prog = BackupProgress(
            total_objects=10, total_bytes=1000,
            on_progress=lambda p: calls.append(p),
            min_report_interval=0.0,  # no throttling for tests
        )
        prog.add_bytes(100)
        assert len(calls) >= 1
        assert calls[-1].bytes_downloaded == 100

    def test_on_progress_throttled(self):
        calls = []
        prog = BackupProgress(
            total_objects=10, total_bytes=1000,
            on_progress=lambda p: calls.append(p),
            min_report_interval=1.0,  # 1 second throttle
        )
        prog.add_bytes(10)
        prog.add_bytes(20)
        prog.add_bytes(30)
        # Only first call should trigger (subsequent calls within 1s are throttled)
        assert len(calls) == 1
        # But the counter still reflects all additions
        assert calls[0].bytes_downloaded == 60

    def test_on_progress_none_no_error(self):
        """No error when on_progress is None."""
        prog = BackupProgress(total_objects=10, total_bytes=1000)
        prog.add_bytes(100)
        prog.worker_started()
        prog.worker_finished()
        prog.mark_object_downloaded()
        prog.object_written(100)

    def test_thread_safety(self):
        """Concurrent updates from multiple threads produce correct totals."""
        import threading

        prog = BackupProgress(
            total_objects=100, total_bytes=10000,
            min_report_interval=0.0,
        )

        def worker():
            for _ in range(100):
                prog.add_bytes(1)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert prog.bytes_downloaded == 1000
```

### F.4 `test_r2_backup.py` — Update imports

```python
from stream_of_worship.admin.services.r2_backup import (
    DEFAULT_CHUNK_SIZE_BYTES,
    MANIFEST_VERSION,
    MIN_CHUNK_SIZE_BYTES,
    BackupError,
    BackupProgress,
    HashingReader,
    Inventory,
    InventoryObject,
    RestoreError,
    VerifyError,
    build_inventory,
    load_manifest,
    parse_size,
    plan_restore,
    restore_from_archive,
    verify_archive,
    write_backup,
    DEFAULT_CONCURRENCY,
    COPY_BUFFER_SIZE,
    SPOT_CHECK_HEAD_RATIO,
)
```

### F.5 `test_r2_backup.py` — Existing tests that should pass unchanged

All `TestConcurrentBackup` tests, `TestVerifyArchive` tests, `TestPlanRestore` tests, and `TestRestoreFromArchive` tests should pass unchanged because:
- `write_backup()` with `on_progress=None` (default) creates a `BackupProgress` with no callback — no behavior change.
- `_download_object_to_tempfile()` with `progress=None` (default when called directly in tests) has no progress tracking — no behavior change.
- The MD5 check change is semantically identical (streaming MD5 produces the same hash as re-read MD5).
- Archive format is unchanged (MANIFEST_VERSION stays 4).

### F.6 `test_r2_backup_commands.py` — No changes needed

The `test_backup_concurrency_flag` test mocks `write_backup` and checks `concurrency` kwarg. The `on_progress` signature change doesn't affect this test since it doesn't exercise the callback.

---

## Expected Behavior After Changes

### Progress bar output

```
⠙ ████████████████░░░░░░░░░░░░  53% 6.8/12.9 GB 8.2 MB/s 0:12 5 workers 360/679 objects
```

Columns (left to right):
1. Spinner
2. Progress bar
3. Percentage (53%)
4. Downloaded/total bytes (6.8/12.9 GB)
5. Transfer speed (8.2 MB/s — reflects aggregate download rate across all workers)
6. Time remaining (ETA based on download rate, stable because progress is smooth)
7. Elapsed time (0:12)
8. Active workers (5 workers)
9. Objects downloaded/total (360/679 objects)

### Performance impact

| Change | Effect |
|---|---|
| Progress tracks downloads, not tar writes | Progress bar is smooth, ETA is stable, speed reflects actual download throughput |
| Streaming MD5 | Eliminates ~12.9 GB of extra disk reads (one full re-read per object). For disk-bound systems, this alone could improve throughput significantly. |
| Worker count display | User can verify parallelism is working (e.g., "8 workers" confirms all threads are active) |
| Elapsed time | User can compare wall-clock time against expectations |

---

## Edge Cases and Considerations

1. **Empty bucket:** `BackupProgress` created with `total_objects=0, total_bytes=0`. No workers started. Progress bar shows `0/0 objects, 0 workers`. Completes immediately.

2. **`concurrency=1`:** Single worker. `active_workers` will be 0 or 1. Progress bar updates after each download chunk. Behavior is correct.

3. **Download failure:** `worker_finished()` is called in `finally` block, so `active_workers` is always decremented even on failure. After failure, `write_backup()` cancels remaining futures and raises `BackupError`. The `Progress` context manager exits cleanly.

4. **`on_progress=None`:** `BackupProgress` is still created (for internal tracking) but `_maybe_report()` is a no-op. No behavior change from caller's perspective.

5. **Thread safety of Rich `Progress.update()`:** Rich's `Progress` class uses an internal `Lock` around `update()`. Calling it from worker threads is safe. The `Live` display refresh thread reads the task state on its own schedule (~10 Hz).

6. **Callback throttling:** `min_report_interval=0.1` (100ms) limits `on_progress` calls to ~10/second. For 679 objects / 12 GB, this means ~10 Rich updates/second regardless of object count or size. Each update acquires the Rich lock briefly. This is negligible overhead.

7. **`add_bytes` called from `HashingReader.read()`:** With `COPY_BUFFER_SIZE=1MB`, each `read()` call processes up to 1 MB. With 8 workers, that's up to 8 `add_bytes` calls per MB of aggregate download. Each call does a lock + counter update + throttle check. This is O(1) and negligible.

8. **MD5 for multipart objects:** Objects with `-` in ETag (multipart uploads) skip the MD5 check. The streaming MD5 is still computed but not used. This is a tiny waste of CPU (MD5 update is fast) but simplifies the code. No action needed.

9. **`objects_downloaded` vs `objects_written`:** `objects_downloaded` reflects completed downloads (may be ahead of tar writes). `objects_written` reflects committed archive entries. The progress bar shows `objects_downloaded` for the "objects" field. This is correct — the user wants to see download progress.

10. **Spot-check HEAD phase:** No progress updates during spot-check HEAD (runs after backup is committed). The progress bar will show 100% during this phase. This is acceptable — spot-check is fast (~34 HEAD requests).

11. **`BackupProgress` not exported in `__init__`:** `BackupProgress` is a public class in `r2_backup.py` (no underscore prefix). It's imported directly in `maintenance.py` and tests. No `__init__.py` export needed.

---

## Verification

```bash
# Run R2 backup tests
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest \
  tests/admin/test_r2_backup.py tests/admin/test_r2_backup_commands.py -v

# Run all admin tests
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/admin/ -v

# Manual smoke test (requires R2 credentials)
uv run --extra admin sow-admin maintenance backup-r2 \
  --output /tmp/sow-r2-backup-test \
  --concurrency 8 \
  -c ~/.config/stream-of-worship-admin/config.toml
```

---

## Assumptions and Out of Scope

**Assumptions:**
- Rich's `Progress.update()` is thread-safe (confirmed by Rich source code — uses `threading.Lock`).
- `hashlib.md5().update()` is thread-safe when called from the same thread that owns the hasher instance (each `HashingReader` is used by a single worker thread).
- The `on_read` callback in `HashingReader` is lightweight enough (lock + counter + throttle check) to not measurably impact download throughput.

**Out of scope:**
- Changing the tar-write sequential model (tar format requires sequential writes).
- Streaming directly to tar (user previously chose to keep temp files).
- Per-worker progress bars (single aggregate bar is sufficient).
- Download timeout / stall detection (can be added later if observed).
- Bandwidth limiting.
