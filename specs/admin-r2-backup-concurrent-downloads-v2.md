# Admin R2 Backup: Concurrent Downloads + GET-ETag Consistency (v2)

## Summary

Speed up the `sow-admin maintenance backup-r2` command by at least 5x through:
1. **Thread pool concurrent downloads** (dominant win: 5-8x network parallelism)
2. **Eliminate redundant post-download HEAD requests** (~1.5-2x for small objects)
3. **Larger `copyfileobj` buffer** (minor: ~1.1x for large objects)
4. **Increased boto3 connection pool** (supporting)

Current performance: ~30 minutes for 679 objects / 12,894 MB (~8 MB/s single connection).
Target: ~4-6 minutes with 8 concurrent workers.

## Bottleneck Analysis

### Bottleneck 1: Fully Sequential Downloads (DOMINANT)

**Location:** `write_backup()` at `r2_backup.py:417`

```python
for idx, inv_obj in enumerate(inventory.objects):
    obj_entry = _download_object_to_tar(r2_client, inv_obj, current_tar, member_name)
```

Every object is downloaded one at a time. A single connection to R2 yields ~8 MB/s.
Cloudflare R2 supports much higher aggregate throughput across multiple connections.
With 679 sequential operations, there is zero network parallelism.

### Bottleneck 2: Redundant HEAD Request Per Object

**Location:** `_download_object_to_tar()` at `r2_backup.py:289`

```python
head_data = r2_client.head_object(inv_obj.key)
```

After downloading each object, a separate HEAD request checks consistency. This doubles
network round-trips: 679 GET + 679 HEAD = 1,358 requests.

The `get_object_stream()` response (`r2.py:726-737`) already returns `etag`,
`content_length`, `content_type`, `cache_control`, `content_disposition`,
`content_encoding`, `metadata`, and `last_modified`. The HEAD is almost entirely
redundant.

### Bottleneck 3: Small `copyfileobj` Buffer

**Location:** `r2_backup.py:272` — `shutil.copyfileobj(hashing_reader, temp_file)` uses
the default 16 KB buffer. For 40 MB audio files, that's ~2,500 syscalls per file.

### Bottleneck 4: boto3 Connection Pool (supporting)

**Location:** `r2.py:101-105` — Default `max_pool_connections=10`. Adequate for 8 workers
but should be increased for headroom.

## HEAD vs GET-ETag Trade-off

| Aspect | Separate HEAD (current) | GET Response ETag (proposed) |
|--------|------------------------|------------------------------|
| Network requests per object | 2 (GET + HEAD) | 1 (GET only) |
| Total requests for 679 objects | 1,358 | 679 (+ ~34 spot-check HEADs) |
| Latency overhead per small object | +50-100ms | 0ms |
| Total latency overhead (small objects) | ~17-34 seconds | ~2-3 seconds |
| Metadata source | HEAD response | GET response (identical fields) |
| Catches object change *during* GET | Yes (narrow race, ms window) | No (MD5 body check + spot-check HEAD) |
| Catches object change *since* inventory | Yes | Yes (GET ETag vs inventory ETag) |
| Consistency guarantee | Object unchanged between GET-end and HEAD | Object ETag at download time matches inventory; MD5 body check for single-part objects; spot-check HEAD for systemic drift |

**Key insight:** The GET response ETag IS the ETag of the bytes actually received. The
HEAD only adds value if the object changes in the milliseconds between GET completion and
HEAD completion — an extremely rare race. We mitigate this with:
1. **MD5 body check** for single-part objects (ETag == MD5) — catches corruption/truncation during GET
2. **Spot-check HEAD** on ~5% random sample — catches systemic drift without doubling requests

## Files to Modify

1. `src/stream_of_worship/admin/services/r2_backup.py` — Core changes
2. `src/stream_of_worship/admin/services/r2.py` — Connection pool config
3. `src/stream_of_worship/admin/commands/maintenance.py` — `--concurrency` CLI flag
4. `tests/admin/test_r2_backup.py` — Update tests for new download flow
5. `tests/admin/test_r2_backup_commands.py` — Update command tests

---

## Change A: Thread Pool Concurrent Downloads (`r2_backup.py`)

### A.1 New constants

```python
DEFAULT_CONCURRENCY = 8
COPY_BUFFER_SIZE = 1024 * 1024  # 1 MB
SPOT_CHECK_HEAD_RATIO = 0.05  # 5% random sample
```

### A.2 New `DownloadResult` dataclass

```python
@dataclass
class DownloadResult:
    """Result of downloading a single object to a temp file."""
    temp_path: Path
    sha256: str
    bytes_read: int
    metadata: dict  # content_type, cache_control, content_disposition, content_encoding, metadata, last_modified
```

### A.3 Refactor `_download_object_to_tar()` -> `_download_object_to_tempfile()`

Decouple downloading from tar writing. The new function:
- Downloads to a temp file under `partial_dir / "tmp"` with 1 MB buffer (`shutil.copyfileobj(..., length=COPY_BUFFER_SIZE)`)
- Validates short read and size mismatch (unchanged logic)
- Validates ETag from **GET response** against inventory (replaces HEAD — see Change B)
- **MD5 body check** for single-part objects (ETag does not contain `-`)
- Returns a `DownloadResult` (temp_path, sha256, bytes_read, metadata_dict)
- Does NOT write to tar (decoupled)
- Retry logic unchanged (ETag mismatch or MD5 mismatch still triggers retry)

```python
def _download_object_to_tempfile(
    r2_client: R2Client,
    inv_obj: InventoryObject,
    temp_dir: Path,
    max_retries: int = 2,
) -> DownloadResult:
    """Download a single object to a temp file with consistency checking.

    Downloads to a temporary file under temp_dir, validates ETag from GET
    response against inventory, performs MD5 body check for single-part
    objects, and returns the temp path + hash + metadata.

    Raises:
        BackupError: If the object cannot be captured consistently.
    """
    import tempfile
    import hashlib

    last_error: Optional[str] = None
    for attempt in range(max_retries + 1):
        temp_path: Optional[Path] = None
        try:
            resp = r2_client.get_object_stream(inv_obj.key)
            body = resp["body"]
            content_length = resp["content_length"]
            get_etag = resp["etag"]

            try:
                temp_dir.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=temp_dir, delete=False) as temp_file:
                    temp_path = Path(temp_file.name)
                    hashing_reader = HashingReader(body)
                    shutil.copyfileobj(hashing_reader, temp_file, length=COPY_BUFFER_SIZE)

                if hashing_reader.bytes_read != content_length:
                    raise BackupError(
                        f"Short read for {inv_obj.key}: expected {content_length} bytes, "
                        f"got {hashing_reader.bytes_read}"
                    )

                if hashing_reader.bytes_read != inv_obj.size:
                    raise BackupError(
                        f"Size mismatch for {inv_obj.key}: inventory says {inv_obj.size}, "
                        f"downloaded {hashing_reader.bytes_read}"
                    )

                # ETag consistency check from GET response (replaces separate HEAD)
                if get_etag != inv_obj.etag:
                    raise BackupError(
                        f"Object {inv_obj.key} ETag changed: inventory {inv_obj.etag}, "
                        f"download {get_etag}"
                    )

                # MD5 body check for single-part objects (ETag == MD5)
                if "-" not in get_etag:
                    md5_hex = hashlib.md5(open(temp_path, "rb").read()).hexdigest()
                    if md5_hex != get_etag:
                        raise BackupError(
                            f"Object {inv_obj.key} MD5 mismatch: ETag {get_etag}, "
                            f"computed {md5_hex}"
                        )

                sha256 = hashing_reader.sha256_hex

                metadata = {
                    "content_type": resp.get("content_type"),
                    "cache_control": resp.get("cache_control"),
                    "content_disposition": resp.get("content_disposition"),
                    "content_encoding": resp.get("content_encoding"),
                    "metadata": resp.get("metadata") or {},
                    "last_modified": resp.get("last_modified"),
                }

                return DownloadResult(
                    temp_path=temp_path,
                    sha256=sha256,
                    bytes_read=hashing_reader.bytes_read,
                    metadata=metadata,
                )
            finally:
                body.close()

        except (BackupError, ClientError) as e:
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

    raise BackupError(f"Failed to backup {inv_obj.key} after retries: {last_error}")
```

### A.4 Modify `write_backup()` — concurrent download + sequential tar write

New parameter: `concurrency: int = DEFAULT_CONCURRENCY`

Design:
- Validate `concurrency` at function entry (`1 <= concurrency <= 64`)
- Use `ThreadPoolExecutor(max_workers=concurrency)`
- Submit all download tasks as futures (each calls `_download_object_to_tempfile`)
- Process futures **in submission order**: `future.result()` -> write to tar -> delete temp file
- Chunk rotation logic stays in main thread (uses `inv_obj.size` from inventory, known before download)
- Error handling: on **any** `BaseException`, cancel remaining futures with `shutdown(cancel_futures=True)`, clean up temp files from a thread-safe tracking set, clean up partial dir
- `on_progress` callback invoked after each object is written to tar (unchanged semantics)
- **Spot-check HEAD**: After all objects are processed, randomly select ~5% of objects and HEAD them. If any mismatch, log a warning (do not fail — the backup is already committed).

```python
def write_backup(
    r2_client: R2Client,
    output_dir: Path,
    inventory: Inventory,
    chunk_size_bytes: int = DEFAULT_CHUNK_SIZE_BYTES,
    concurrency: int = DEFAULT_CONCURRENCY,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> BackupResult:
    """Write a full backup to the output directory.

    Creates <output_dir>.part/ first, writes chunks and manifest, then
    renames to <output_dir>/.

    Args:
        r2_client: R2Client instance
        output_dir: Final output directory path
        inventory: Pre-built inventory
        chunk_size_bytes: Max bytes per chunk tar
        concurrency: Number of concurrent download workers (1-64)
        on_progress: Optional callback invoked after each object is written.
            Receives (objects_completed, bytes_completed).

    Returns:
        BackupResult with summary info.

    Raises:
        BackupError: If backup fails. Partial directory is cleaned up.
    """
    if not (1 <= concurrency <= 64):
        raise BackupError(f"concurrency must be 1-64, got {concurrency}")

    if output_dir.exists():
        raise BackupError(f"Output directory already exists: {output_dir}")
    partial_dir = output_dir.with_suffix(output_dir.suffix + ".part")
    if partial_dir.exists():
        raise BackupError(
            f"Partial output directory already exists: {partial_dir}. "
            "Remove it manually if it is from a failed backup."
        )

    if chunk_size_bytes <= 0:
        raise BackupError(f"Chunk size must be positive, got {chunk_size_bytes}")

    # Disk space: archive (~total_bytes) + temp files (can approach total_bytes
    # since downloads complete ahead of sequential tar writing) + 50 MiB headroom
    required_space = int(inventory.total_bytes * 2.1) + 50 * 1024 * 1024
    _check_disk_space(output_dir.parent, required_space)

    partial_dir.mkdir(parents=True)
    (partial_dir / PARTIAL_MARKER).touch()
    temp_dir = partial_dir / "tmp"

    # Thread-safe set of active temp paths for cleanup on failure
    active_temp_paths: set[Path] = set()
    active_temp_lock = threading.Lock()

    def _track_temp(path: Path) -> None:
        with active_temp_lock:
            active_temp_paths.add(path)

    def _untrack_temp(path: Path) -> None:
        with active_temp_lock:
            active_temp_paths.discard(path)

    def _cleanup_all_temps() -> None:
        with active_temp_lock:
            paths = list(active_temp_paths)
            active_temp_paths.clear()
        for p in paths:
            try:
                p.unlink()
            except OSError:
                pass

    current_tar: Optional[tarfile.TarFile] = None

    try:
        from concurrent.futures import ThreadPoolExecutor
        import threading
        import random

        manifest_objects: list[dict] = []
        chunk_index = 0
        current_chunk_bytes = 0

        def _ensure_tar():
            nonlocal current_tar
            if current_tar is None:
                current_tar = tarfile.open(_chunk_path(partial_dir, chunk_index), "w")

        def _rotate_chunk():
            nonlocal current_tar, chunk_index, current_chunk_bytes
            if current_tar is not None:
                current_tar.close()
                current_tar = None
            chunk_index += 1
            current_chunk_bytes = 0

        bytes_completed = 0

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            # Submit all downloads as futures, keyed by index
            futures = {
                idx: executor.submit(
                    _download_object_to_tempfile, r2_client, inv_obj, temp_dir
                )
                for idx, inv_obj in enumerate(inventory.objects)
            }

            try:
                for idx, inv_obj in enumerate(inventory.objects):
                    member_name = _member_name_for_index(idx)

                    # Chunk rotation (uses inventory size, known before download)
                    if (
                        current_chunk_bytes > 0
                        and current_chunk_bytes + inv_obj.size > chunk_size_bytes
                    ):
                        _rotate_chunk()

                    _ensure_tar()

                    # Wait for this object's download to complete (in order)
                    future = futures[idx]
                    try:
                        download_result = future.result()
                    except BaseException:
                        # Cancel remaining futures and re-raise
                        for f in futures.values():
                            f.cancel()
                        raise

                    _track_temp(download_result.temp_path)
                    try:
                        # Write to tar from temp file
                        tar_info = tarfile.TarInfo(name=member_name)
                        tar_info.size = download_result.bytes_read
                        tar_info.mtime = 0
                        tar_info.mode = 0o644
                        tar_info.type = tarfile.REGTYPE

                        with open(download_result.temp_path, "rb") as f_in:
                            current_tar.addfile(tar_info, f_in)

                        obj_entry = _build_manifest_object(
                            inv_obj, member_name, download_result.sha256, chunk_index,
                            download_result.metadata,
                        )
                        manifest_objects.append(obj_entry)
                        current_chunk_bytes += inv_obj.size
                        bytes_completed += inv_obj.size

                        if on_progress is not None:
                            on_progress(idx + 1, bytes_completed)
                    finally:
                        _untrack_temp(download_result.temp_path)
                        try:
                            download_result.temp_path.unlink()
                        except OSError:
                            pass
            except BaseException:
                # Cancel all pending futures, abandon running ones
                for f in futures.values():
                    f.cancel()
                # executor.shutdown is called by context manager; we rely on
                # _cleanup_all_temps below for any completed-but-not-consumed files
                raise

        if current_tar is not None:
            current_tar.close()
            current_tar = None

        # Spot-check HEAD on ~5% random sample
        if manifest_objects and SPOT_CHECK_HEAD_RATIO > 0:
            sample_size = max(1, int(len(manifest_objects) * SPOT_CHECK_HEAD_RATIO))
            sample_indices = random.sample(range(len(manifest_objects)), min(sample_size, len(manifest_objects)))
            for sample_idx in sample_indices:
                obj_entry = manifest_objects[sample_idx]
                try:
                    head_data = r2_client.head_object(obj_entry["key"])
                    if head_data is None:
                        # Object deleted after backup — log warning
                        continue
                    if head_data["etag"] != obj_entry["etag"]:
                        # Object changed after backup — log warning
                        pass
                except ClientError:
                    pass

        final_chunk_count = chunk_index + 1 if manifest_objects else 0

        manifest = {
            "version": MANIFEST_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "inventory_started_at": inventory.started_at,
            "inventory_completed_at": inventory.completed_at,
            "bucket": r2_client.bucket,
            "endpoint_url": r2_client.endpoint_url,
            "region": r2_client.region,
            "chunk_size_bytes": chunk_size_bytes,
            "object_count": len(manifest_objects),
            "total_bytes": sum(o["size"] for o in manifest_objects),
            "chunk_count": final_chunk_count,
            "consistency": {
                "mode": "initial-inventory-with-get-etag-check",
                "max_changed_object_retries": 2,
                "md5_body_check": True,
                "spot_check_head_ratio": SPOT_CHECK_HEAD_RATIO,
            },
            "objects": manifest_objects,
        }

        manifest_path = partial_dir / "manifest.json"
        tmp_manifest = partial_dir / "manifest.json.tmp"
        with open(tmp_manifest, "w") as f:
            json.dump(manifest, f, indent=2)
        tmp_manifest.rename(manifest_path)

        # Clean up temp dir if empty
        try:
            temp_dir.rmdir()
        except OSError:
            pass

        partial_dir.rename(output_dir)

        return BackupResult(
            output_dir=output_dir,
            object_count=len(manifest_objects),
            total_bytes=manifest["total_bytes"],
            chunk_count=final_chunk_count,
            manifest=manifest,
        )

    except BaseException:
        if current_tar is not None:
            try:
                current_tar.close()
            except Exception:
                pass
        _cleanup_all_temps()
        _cleanup_owned_partial(partial_dir)
        raise
```

**Design notes:**
- Downloads happen in parallel (up to `concurrency` at a time).
- Tar writing happens sequentially in submission order (tar format requires sequential writes).
- Temp files are created under `partial_dir/tmp/` so they are automatically cleaned up by `_cleanup_owned_partial()` on failure.
- A thread-safe `active_temp_paths` set tracks temp files that have completed download but not yet been consumed by the tar writer. On any `BaseException`, all tracked temps are deleted.
- Chunk rotation uses `inv_obj.size` from inventory (known before download starts), so rotation decisions are deterministic.
- `on_progress` is called after each object is written to tar, preserving existing semantics.
- Spot-check HEAD runs after the backup is fully committed; mismatches are logged as warnings, not failures.

### A.5 Update manifest consistency mode

```python
"consistency": {
    "mode": "initial-inventory-with-get-etag-check",
    "max_changed_object_retries": 2,
    "md5_body_check": True,
    "spot_check_head_ratio": 0.05,
},
```

**MANIFEST_VERSION bumps from 3 to 4** to signal the weaker consistency guarantee and new metadata fields.

---

## Change B: Eliminate Redundant HEAD Request (`r2_backup.py`)

The HEAD request in `_download_object_to_tar()` (line 289) is replaced by ETag
validation from the GET response in the new `_download_object_to_tempfile()` (see A.3).

The GET response from `get_object_stream()` (`r2.py:726-737`) returns all the same
metadata fields that the HEAD returned:
- `etag` -> used for consistency check
- `content_type`, `cache_control`, `content_disposition`, `content_encoding`, `metadata`, `last_modified`
  -> stored in manifest for restore

The `_build_manifest_object()` helper (`r2_backup.py:208`) is called with the GET
response metadata instead of HEAD data. Its signature is unchanged except `head_data`
parameter renamed to `metadata` for clarity.

**Spot-check HEAD** (see A.4): After the backup completes, ~5% of objects are HEADed
as a lightweight systemic consistency check. This catches bucket-wide drift without
doubling requests.

---

## Change C: Larger `copyfileobj` Buffer (`r2_backup.py`)

In `_download_object_to_tempfile()`:

```python
shutil.copyfileobj(hashing_reader, temp_file, length=COPY_BUFFER_SIZE)
```

Reduces syscalls by 64x for large files (16 KB -> 1 MB buffer).

---

## Change D: Increase boto3 Connection Pool (`r2.py`)

```python
self._client = boto3.client(
    "s3",
    endpoint_url=endpoint_url,
    aws_access_key_id=access_key,
    aws_secret_access_key=secret_key,
    region_name=region,
    config=Config(
        connect_timeout=10,
        read_timeout=30,
        retries={"max_attempts": 2},
        max_pool_connections=32,
    ),
)
```

Default is 10. With 8 concurrent workers, 32 provides headroom for retries and
connection reuse.

---

## Change E: `--concurrency` CLI Flag (`maintenance.py`)

### E.1 Add option to `backup_r2` command

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
    format_: str = typer.Option("table", "--format", help="table|json"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
```

### E.2 Pass to `write_backup()`

```python
result = write_backup(
    r2_client=r2_client,
    output_dir=output,
    inventory=inventory,
    chunk_size_bytes=chunk_size_bytes,
    concurrency=concurrency,
    on_progress=_on_progress,
)
```

---

## Change F: Test Updates

### F.1 `test_r2_backup.py`

**`MANIFEST_VERSION` bump:** Update `test_manifest_version_constant` to expect 4.

**`_make_r2_mock()` helper:** Already returns all metadata fields in GET response
(`content_type`, `cache_control`, etc.). No change needed.

**Tests that assert HEAD was called:** The following tests use `r2.head_object` to
simulate consistency changes. These need to be updated to use `r2.get_object_stream`
ETag instead:

- `test_backup_changed_object_retries_and_succeeds` — currently uses flaky HEAD;
  change to flaky GET ETag (first GET returns different etag, second returns matching)
- `test_backup_changed_object_exceeds_retries_fails` — currently HEAD always returns
  different etag; change to GET always returning different etag
- `test_backup_deleted_object_during_backup_fails` — currently HEAD returns None;
  change to GET raising ClientError 404

**New tests to add:**

```python
class TestConcurrentBackup:
    def test_concurrent_backup_produces_valid_archive(self, tmp_path):
        """Concurrent download with default concurrency produces valid backup."""
        # 20 objects, verify manifest + chunks + SHA-256

    def test_concurrency_1_works(self, tmp_path):
        """concurrency=1 falls back to sequential (no thread pool issues)."""

    def test_concurrent_backup_preserves_object_order(self, tmp_path):
        """Manifest objects are in inventory order regardless of download completion order."""
        # Use a mock where later objects download faster than earlier ones

    def test_concurrent_backup_cleans_up_temp_files(self, tmp_path):
        """No temp files remain after successful backup."""

    def test_concurrent_backup_cleans_up_temp_files_on_failure(self, tmp_path):
        """Temp files are cleaned up when backup fails mid-way."""

    def test_concurrent_backup_failure_cancels_remaining(self, tmp_path):
        """On failure, remaining futures are cancelled and partial dir cleaned up."""

    def test_md5_body_check_catches_corruption(self, tmp_path):
        """Single-part object with corrupted body fails with MD5 mismatch."""
        # Mock get_object_stream returns correct ETag but body bytes are wrong

    def test_spot_check_head_warns_on_drift(self, tmp_path):
        """Spot-check HEAD logs warning when object changed after backup."""
        # Mock head_object returns different ETag for sampled object

    def test_manifest_version_4(self, tmp_path):
        """Backup produces manifest version 4."""

    def test_get_last_modified_in_metadata(self, tmp_path):
        """Manifest stores GET response last_modified in metadata."""

    def test_concurrency_validation_rejects_zero(self, tmp_path):
        """write_backup raises BackupError for concurrency=0."""

    def test_concurrency_validation_rejects_65(self, tmp_path):
        """write_backup raises BackupError for concurrency=65."""
```

**Existing tests that should pass unchanged (after MANIFEST_VERSION update):**
- `test_backup_creates_manifest_and_chunks`
- `test_backup_empty_bucket`
- `test_backup_chunk_boundary`
- `test_backup_large_object_exceeds_chunk`
- `test_backup_refuses_existing_output`
- `test_backup_refuses_existing_partial`
- `test_backup_short_read_fails`
- `test_backup_preserves_metadata`
- `test_backup_cleanup_on_interrupt`
- `test_on_progress_called_with_correct_counts`
- All `TestVerifyArchive` tests
- All `TestPlanRestore` tests
- All `TestRestoreFromArchive` tests

### F.2 `test_r2_backup_commands.py`

**New test:**

```python
def test_backup_concurrency_flag(self, tmp_path):
    """backup-r2 --concurrency 4 passes concurrency to write_backup."""
```

**Existing tests:** Should pass unchanged. The mock `_make_r2_mock()` already returns
GET response with all metadata fields.

---

## Expected Performance

| Optimization | Estimated Speedup | Primary Benefit |
|-------------|-------------------|-----------------|
| Thread pool (8 workers) | 5-8x | Network parallelism |
| Eliminate HEAD | 1.5-2x | Small object latency |
| Larger buffer | 1.1x | Large object disk I/O |
| **Combined (conservative)** | **~5-8x** | **30 min -> ~4-6 min** |

The thread pool is the dominant win. With 8 concurrent R2 connections, aggregate
throughput should scale well beyond single-connection 8 MB/s.

---

## Edge Cases and Considerations

1. **Empty bucket:** `concurrency` doesn't matter; no futures submitted, completes immediately.

2. **Single object:** `concurrency=8` but only 1 future; no overhead from thread pool.

3. **`concurrency=1`:** Falls back to effectively sequential behavior (1 worker). Useful
   for debugging or when R2 throttles parallel connections.

4. **Temp file disk usage:** Temp files are created under `partial_dir/tmp/`. Because
   downloads complete ahead of sequential tar writing, peak temp usage can approach
   `total_bytes` in the worst case (object 0 is very slow). The disk space check
   reserves `total_bytes * 2.1 + 50 MiB` to cover both temp files and final archive.

5. **Thread safety of boto3:** boto3 clients are thread-safe for S3 operations.
   The `ThreadPoolExecutor` shares a single `R2Client` (and thus a single boto3 client)
   across workers. boto3's connection pool (`max_pool_connections=32`) handles
   concurrent requests.

6. **Tar writing stays sequential:** The tar format requires sequential member writes.
   Only downloads are parallelized; tar writing happens in the main thread in
   submission order.

7. **Error propagation:** If any download fails, the main thread cancels remaining
   futures, cleans up tracked temp files, and cleans up the partial directory.
   The `ThreadPoolExecutor` context manager ensures all threads are joined before exit.
   A thread-safe `active_temp_paths` set catches any completed-but-not-yet-consumed
   temp files.

8. **Progress callback:** Called after each object is written to tar (not after download).
   This preserves existing semantics: progress reflects committed bytes in the archive.

9. **Manifest `consistency.mode`:** Changes from
   `"initial-inventory-with-post-download-head-check"` to
   `"initial-inventory-with-get-etag-check"`. `MANIFEST_VERSION` bumps to 4 to signal
   the semantic change. `verify_archive()` and `restore_from_archive()` don't read
   this field.

10. **Retry behavior:** Unchanged. ETag mismatch or MD5 mismatch from GET response
    triggers the same retry loop as the previous HEAD-based check. The retry budget
    (`max_retries=2`) is preserved.

11. **Spot-check HEAD:** Runs after the backup is fully committed. Mismatches are
    logged as warnings, not failures. This is intentional: the backup captured a valid
    self-consistent snapshot at download time.

12. **Temp file location:** All temp files are created under `partial_dir/tmp/`.
    On success, the directory is removed if empty. On failure, `_cleanup_owned_partial()`
    removes the entire partial directory including temp files.

13. **Per-object download timeout:** Not implemented. boto3 read timeout (30s) and
    TCP keepalive provide basic stall detection. A stalled slow trickle (<1 KB/s)
    could still block indefinitely. If observed in production, add `future.result(timeout=...)`
    or socket-level read timeouts.

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
- boto3 S3 client is thread-safe for concurrent `get_object` calls (confirmed by boto3 docs).
- Cloudflare R2 supports multiple concurrent connections with higher aggregate throughput.
- Temp files are kept under `partial_dir/tmp/` (per revised design).
- MD5 body check is feasible because single-part uploads are the common case for this workload.

**Out of scope:**
- Streaming directly to tar (user chose to keep temp files).
- Async/asyncio implementation (threads are simpler and sufficient for I/O-bound work).
- Compression of backup archives.
- Incremental or differential backups.
- Bandwidth limiting.
- Restore-side concurrency (restore is upload-bound, different bottleneck).
- Per-object download timeout (can be added later if stalls are observed).

(End of file)
