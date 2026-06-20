"""R2 backup and restore service.

Implements full-bucket backup, verification, and restore for Cloudflare R2
disaster recovery.  Backups are chunked tar archives with a JSON manifest
that maps safe internal member names back to R2 keys and metadata.
"""

import hashlib
import json
import logging
import os
import re
import shutil
import tarfile
import threading
import time
from concurrent.futures import as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from botocore.exceptions import ClientError
from stream_of_worship.admin.services.r2 import R2Client

logger = logging.getLogger(__name__)

MANIFEST_VERSION = 4
DEFAULT_CHUNK_SIZE_BYTES = 10 * 1024 * 1024 * 1024  # 10 GiB
MIN_CHUNK_SIZE_BYTES = 64 * 1024 * 1024  # 64 MiB
PARTIAL_MARKER = ".sow-r2-backup-partial"
# Bumped from 8 → 32 based on trace evidence (2026-06): with 8 workers, each
# R2 stream caps at ~1 MBps and aggregate throughput plateaus at ~7.3 MBps
# (see specs/admin-r2-backup-throughput-remediation-v1.md). The per-stream cap is
# R2-side (confirmed by --diag-range-key), so more connections is the only way
# to scale until/if multipart Range-GET per object is added.
DEFAULT_CONCURRENCY = 32
COPY_BUFFER_SIZE = 1024 * 1024  # 1 MB
SPOT_CHECK_HEAD_RATIO = 0.05  # 5% random sample

_BINARY_SUFFIXES = {
    "KiB": 1024,
    "MiB": 1024**2,
    "GiB": 1024**3,
    "TiB": 1024**4,
    "KB": 1000,
    "MB": 1000**2,
    "GB": 1000**3,
    "TB": 1000**4,
}

_SIZE_RE = re.compile(r"^\s*(\d+)\s*([A-Za-z]*)\s*$")


class BackupError(Exception):
    """Raised when a backup operation fails."""


class VerifyError(Exception):
    """Raised when archive verification fails."""


class RestoreError(Exception):
    """Raised when a restore operation fails."""


def parse_size(value: str) -> int:
    """Parse a human-readable size string into bytes.

    Supports binary suffixes (KiB, MiB, GiB, TiB), decimal suffixes
    (KB, MB, GB, TB), and raw integer bytes.

    Args:
        value: Size string like "10GiB", "500MiB", "1024"

    Returns:
        Number of bytes

    Raises:
        ValueError: If the value cannot be parsed or suffix is unknown.
    """
    match = _SIZE_RE.match(value)
    if not match:
        raise ValueError(f"Invalid size format: {value!r}")
    num_str, suffix = match.groups()
    try:
        num = int(num_str)
    except ValueError:
        raise ValueError(f"Invalid size number: {num_str!r}")
    if not suffix:
        return num
    suffix = suffix.strip()
    multiplier = _BINARY_SUFFIXES.get(suffix)
    if multiplier is None:
        raise ValueError(f"Unknown size suffix: {suffix!r}")
    return num * multiplier


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
        self._maybe_report()

    def add_bytes(self, n: int) -> None:
        with self._lock:
            self._bytes_downloaded += n
        self._maybe_report()

    def mark_object_downloaded(self) -> None:
        with self._lock:
            self._objects_downloaded += 1
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


@dataclass
class DownloadResult:
    """Result of downloading a single object to a temp file."""

    temp_path: Path
    sha256: str
    bytes_read: int
    metadata: dict


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
        self._timeout_retries = 0
        self._sum_download_ms = 0.0  # sum of worker download_ms across objects
        self._sum_conn_ms = 0.0  # sum of boto3 get_object_stream ms
        self._sum_wait_ms = 0.0  # sum of main-thread future.result() wait ms
        self._sum_tar_write_ms = 0.0  # sum of main-thread tar.addfile ms
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
        self._logger.debug(f"phase_end name={name} elapsed_ms={elapsed_ms:.1f} {fields}")

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

    # ---- Retry tracing ----

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
            timeout_retries = self._timeout_retries
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
        avg_per_object_download_ms = sum_download_ms / max(total_objects, 1)
        avg_object_bytes = bytes_downloaded / max(total_objects, 1)
        single_worker_avg_mbps = (
            avg_object_bytes / max(avg_per_object_download_ms, 1.0) * 1000 / (1024 * 1024)
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
            sum_download_ms,
            sum_conn_ms,
            sum_wait_ms,
            sum_tar_write_ms,
            wait_ratio,
            tar_ratio,
            max_object_download_ms,
            max_object_download_key,
            "yes"
            if single_worker_avg_mbps > 0
            and aggregate_mbps > 0
            and (aggregate_mbps / max(single_worker_avg_mbps * peak_workers, 1.0)) < 0.5
            else "no (or inconclusive)",
        )


@dataclass
class InventoryObject:
    """A single object from the R2 inventory."""

    key: str
    size: int
    etag: str
    last_modified: str


@dataclass
class Inventory:
    """Full bucket inventory built at backup start."""

    objects: list[InventoryObject] = field(default_factory=list)
    started_at: str = ""
    completed_at: str = ""

    @property
    def object_count(self) -> int:
        return len(self.objects)

    @property
    def total_bytes(self) -> int:
        return sum(obj.size for obj in self.objects)


def build_inventory(r2_client: R2Client) -> Inventory:
    """Build a start-of-backup inventory from R2 list pages.

    Args:
        r2_client: R2Client instance

    Returns:
        Inventory with all objects present at backup start.
    """
    started_at = datetime.now(timezone.utc).isoformat()
    objects: list[InventoryObject] = []
    for obj in r2_client.iter_objects():
        objects.append(
            InventoryObject(
                key=obj["key"],
                size=obj["size"],
                etag=obj["etag"],
                last_modified=obj["last_modified"] or "",
            )
        )
    completed_at = datetime.now(timezone.utc).isoformat()
    return Inventory(objects=objects, started_at=started_at, completed_at=completed_at)


def _member_name_for_index(index: int) -> str:
    """Generate a safe internal member name for an object index."""
    return f"objects/{index:012d}.bin"


def _chunk_path(chunk_dir: Path, chunk_index: int) -> Path:
    return chunk_dir / f"chunk-{chunk_index:06d}.tar"


def _check_disk_space(path: Path, required_bytes: int) -> None:
    """Check that the filesystem has enough free space.

    Args:
        path: Path on the target filesystem
        required_bytes: Required free bytes

    Raises:
        BackupError: If insufficient disk space.
    """
    check_path = path
    while not check_path.exists() and check_path != check_path.parent:
        check_path = check_path.parent
    try:
        usage = shutil.disk_usage(str(check_path))
    except OSError as e:
        raise BackupError(f"Failed to check disk space at {check_path}: {e}")
    if usage.free < required_bytes:
        raise BackupError(
            f"Insufficient disk space: {usage.free} bytes available, "
            f"{required_bytes} bytes required"
        )


def _is_owned_partial(path: Path) -> bool:
    """Check if a directory is an owned partial backup directory."""
    return path.is_dir() and (path / PARTIAL_MARKER).exists()


def _cleanup_owned_partial(path: Path) -> None:
    """Delete a partial directory only if it contains the ownership marker."""
    if _is_owned_partial(path):
        shutil.rmtree(path)


def _build_manifest_object(
    inv_obj: InventoryObject,
    member_name: str,
    sha256: str,
    chunk_index: int,
    metadata: Optional[dict],
) -> dict:
    """Build a manifest object entry from inventory + GET response metadata."""
    obj_entry: dict = {
        "key": inv_obj.key,
        "member_name": member_name,
        "size": inv_obj.size,
        "sha256": sha256,
        "chunk_index": chunk_index,
        "etag": inv_obj.etag,
        "last_modified": inv_obj.last_modified,
        "content_type": None,
        "cache_control": None,
        "content_disposition": None,
        "content_encoding": None,
        "metadata": {},
    }
    if metadata:
        obj_entry["content_type"] = metadata.get("content_type")
        obj_entry["cache_control"] = metadata.get("cache_control")
        obj_entry["content_disposition"] = metadata.get("content_disposition")
        obj_entry["content_encoding"] = metadata.get("content_encoding")
        obj_entry["metadata"] = metadata.get("metadata") or {}
    return obj_entry


def _download_object_to_tempfile(
    r2_client: R2Client,
    inv_obj: InventoryObject,
    temp_dir: Path,
    progress: Optional[BackupProgress] = None,
    tracer: Optional[BackupTracer] = None,
    max_retries: int = 2,
) -> DownloadResult:
    """Download a single object to a temp file with consistency checking.

    Downloads to a temporary file under temp_dir, validates ETag from GET
    response against inventory, performs MD5 body check for single-part
    objects, and returns the temp path + hash + metadata.

    Args:
        r2_client: R2Client instance
        inv_obj: Inventory object to download
        temp_dir: Directory for temporary files
        progress: Optional BackupProgress tracker for reporting
        tracer: Optional BackupTracer for performance tracing
        max_retries: Number of retry attempts for transient failures

    Raises:
        BackupError: If the object cannot be captured consistently.
    """
    import tempfile

    last_error: Optional[str] = None
    worker_name = threading.current_thread().name
    retries_taken = 0
    conn_ms = 0.0
    stream_ms = 0.0

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

                if get_etag != inv_obj.etag:
                    raise BackupError(
                        f"Object {inv_obj.key} ETag changed: inventory {inv_obj.etag}, "
                        f"download {get_etag}"
                    )

                # MD5 body check — uses streaming MD5 from HashingReader
                if "-" not in get_etag:
                    if hashing_reader.md5_hex != get_etag:
                        raise BackupError(
                            f"Object {inv_obj.key} MD5 mismatch: ETag {get_etag}, "
                            f"computed {hashing_reader.md5_hex}"
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


@dataclass
class BackupResult:
    """Result of a backup operation."""

    output_dir: Path
    object_count: int
    total_bytes: int
    chunk_count: int
    manifest: dict


def write_backup(
    r2_client: R2Client,
    output_dir: Path,
    inventory: Inventory,
    chunk_size_bytes: int = DEFAULT_CHUNK_SIZE_BYTES,
    concurrency: int = DEFAULT_CONCURRENCY,
    on_progress: Optional[Callable[[BackupProgress], None]] = None,
    tracer: Optional[BackupTracer] = None,
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
        on_progress: Optional callback invoked with BackupProgress updates.
            Called from worker threads (throttled to ~10/sec).
        tracer: Optional BackupTracer for performance tracing.

    Returns:
        BackupResult with summary info.

    Raises:
        BackupError: If backup fails. Partial directory is cleaned up.
    """
    if not (1 <= concurrency <= 128):
        raise BackupError(f"concurrency must be 1-128, got {concurrency}")

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

    required_space = int(inventory.total_bytes * 2.1) + 50 * 1024 * 1024

    if tracer is not None:
        tracer.phase_start("disk_check")
    _check_disk_space(output_dir.parent, required_space)
    if tracer is not None:
        tracer.phase_end("disk_check", required_bytes=required_space)

    if tracer is not None:
        tracer.phase_start("setup")
    partial_dir.mkdir(parents=True)
    (partial_dir / PARTIAL_MARKER).touch()
    temp_dir = partial_dir / "tmp"
    if tracer is not None:
        tracer.phase_end("setup")

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

        progress = BackupProgress(
            total_objects=inventory.object_count,
            total_bytes=inventory.total_bytes,
            on_progress=on_progress,
        )

        if tracer is not None:
            tracer.phase_start("total")
            tracer.phase_start("download_phase")

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
            tracer.phase_end(
                "download_phase",
                objects=inventory.object_count,
                total_bytes=inventory.total_bytes,
            )
            tracer.finalize(
                total_objects=inventory.object_count,
                total_bytes=inventory.total_bytes,
            )

        if current_tar is not None:
            if tracer is not None:
                tracer.phase_start("tar_close")
            current_tar.close()
            current_tar = None
            if tracer is not None:
                tracer.phase_end("tar_close")

        if manifest_objects and SPOT_CHECK_HEAD_RATIO > 0:
            if tracer is not None:
                tracer.phase_start("spot_check")
            sample_size = max(1, int(len(manifest_objects) * SPOT_CHECK_HEAD_RATIO))
            sample_indices = random.sample(
                range(len(manifest_objects)), min(sample_size, len(manifest_objects))
            )
            for sample_idx in sample_indices:
                obj_entry = manifest_objects[sample_idx]
                try:
                    head_data = r2_client.head_object(obj_entry["key"])
                    if head_data is None:
                        logger.warning(
                            f"Spot-check: Object {obj_entry['key']} was deleted after backup"
                        )
                        continue
                    if head_data["etag"] != obj_entry["etag"]:
                        logger.warning(
                            f"Spot-check: Object {obj_entry['key']} ETag changed after backup: "
                            f"expected {obj_entry['etag']}, got {head_data['etag']}"
                        )
                except ClientError as e:
                    logger.warning(f"Spot-check: Failed to HEAD {obj_entry['key']}: {e}")
            if tracer is not None:
                tracer.phase_end("spot_check", samples=sample_size)

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

        if tracer is not None:
            tracer.phase_start("manifest_write")
        manifest_path = partial_dir / "manifest.json"
        tmp_manifest = partial_dir / "manifest.json.tmp"
        with open(tmp_manifest, "w") as f:
            json.dump(manifest, f, indent=2)
        tmp_manifest.rename(manifest_path)
        if tracer is not None:
            tracer.phase_end("manifest_write")

        try:
            temp_dir.rmdir()
        except OSError:
            pass

        if tracer is not None:
            tracer.phase_start("rename")
        partial_dir.rename(output_dir)
        if tracer is not None:
            tracer.phase_end("rename")

        if tracer is not None:
            tracer.phase_end("total")

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


SUPPORTED_MANIFEST_VERSIONS = {3, 4}


def load_manifest(dir_path: Path) -> dict:
    """Load and return manifest.json from a backup directory.

    Supports manifest versions 3 and 4. Version 3 uses post-download HEAD
    consistency checks; version 4 uses GET-ETag checks with MD5 body
    verification. Both produce identical object/chunk structures for
    verification and restore.

    Raises:
        VerifyError: If manifest is missing or has unsupported version.
    """
    manifest_path = dir_path / "manifest.json"
    if not manifest_path.exists():
        raise VerifyError(f"manifest.json not found in {dir_path}")
    with open(manifest_path) as f:
        manifest = json.load(f)
    version = manifest.get("version")
    if version not in SUPPORTED_MANIFEST_VERSIONS:
        raise VerifyError(
            f"Unsupported manifest version: {version}, "
            f"expected one of {sorted(SUPPORTED_MANIFEST_VERSIONS)}"
        )
    return manifest


def _validate_manifest_invariants(manifest: dict) -> list[str]:
    """Validate manifest invariants. Returns list of error messages."""
    errors: list[str] = []
    objects = manifest.get("objects", [])

    keys = [o.get("key", "") for o in objects]
    member_names = [o.get("member_name", "") for o in objects]

    # Unique keys
    seen_keys: set[str] = set()
    for key in keys:
        if key in seen_keys:
            errors.append(f"Duplicate object key: {key}")
        seen_keys.add(key)

    # Unique member names
    seen_members: set[str] = set()
    for name in member_names:
        if name in seen_members:
            errors.append(f"Duplicate member name: {name}")
        seen_members.add(name)

    # Valid chunk indexes
    chunk_count = manifest.get("chunk_count", 0)
    for obj in objects:
        ci = obj.get("chunk_index", -1)
        if not isinstance(ci, int) or ci < 0 or ci >= chunk_count:
            errors.append(f"Invalid chunk_index for {obj.get('key')}: {ci}")

    # object_count matches
    if manifest.get("object_count") != len(objects):
        errors.append(
            f"object_count {manifest.get('object_count')} != actual {len(objects)}"
        )

    # total_bytes matches
    expected_total = sum(o.get("size", 0) for o in objects)
    if manifest.get("total_bytes") != expected_total:
        errors.append(
            f"total_bytes {manifest.get('total_bytes')} != actual {expected_total}"
        )

    # chunk_count matches
    if chunk_count != len({o.get("chunk_index", -1) for o in objects if o}):
        if objects:
            max_ci = max(o.get("chunk_index", 0) for o in objects)
            if chunk_count != max_ci + 1:
                errors.append(
                    f"chunk_count {chunk_count} != max chunk_index + 1 ({max_ci + 1})"
                )

    # Member names are relative, normalized, under objects/
    for name in member_names:
        if not name:
            errors.append("Empty member name")
            continue
        if os.path.isabs(name):
            errors.append(f"Absolute member name: {name}")
        normalized = os.path.normpath(name).replace("\\", "/")
        if normalized.startswith(".."):
            errors.append(f"Member name escapes directory: {name}")
        if not normalized.startswith("objects/"):
            errors.append(f"Member name not under objects/: {name}")

    return errors


@dataclass
class VerifyResult:
    """Result of archive verification."""

    ok: bool
    errors: list[str] = field(default_factory=list)
    object_count: int = 0
    total_bytes: int = 0
    chunk_count: int = 0


def verify_archive(dir_path: Path) -> VerifyResult:
    """Verify a backup archive directory.

    Pure local operation; no R2 credentials required.

    Args:
        dir_path: Path to backup directory

    Returns:
        VerifyResult with ok flag and any errors.
    """
    errors: list[str] = []

    try:
        manifest = load_manifest(dir_path)
    except VerifyError as e:
        return VerifyResult(ok=False, errors=[str(e)])
    except json.JSONDecodeError as e:
        return VerifyResult(ok=False, errors=[f"Invalid manifest JSON: {e}"])
    except OSError as e:
        return VerifyResult(ok=False, errors=[f"Failed to read manifest: {e}"])

    errors.extend(_validate_manifest_invariants(manifest))

    objects = manifest.get("objects", [])
    chunk_count = manifest.get("chunk_count", 0)

    # Build expected member map per chunk
    chunk_members: dict[int, dict[str, dict]] = {}
    for obj in objects:
        ci = obj.get("chunk_index", 0)
        member_name = obj.get("member_name", "")
        chunk_members.setdefault(ci, {})[member_name] = obj

    # Verify each chunk
    for ci in range(chunk_count):
        chunk_file = _chunk_path(dir_path, ci)
        if not chunk_file.exists():
            errors.append(f"Missing chunk file: {chunk_file.name}")
            continue

        try:
            with tarfile.open(chunk_file, "r") as tar:
                expected = chunk_members.get(ci, {})
                seen_names: set[str] = set()

                for member in tar.getmembers():
                    if not member.isreg():
                        errors.append(
                            f"Non-regular member in {chunk_file.name}: {member.name}"
                        )
                        continue
                    if member.name in seen_names:
                        errors.append(
                            f"Duplicate member in {chunk_file.name}: {member.name}"
                        )
                        continue
                    seen_names.add(member.name)

                    if member.name not in expected:
                        errors.append(
                            f"Orphan member in {chunk_file.name}: {member.name}"
                        )
                        continue

                    obj_entry = expected[member.name]

                    # Size check
                    if member.size != obj_entry.get("size", 0):
                        errors.append(
                            f"Size mismatch for {member.name} in {chunk_file.name}: "
                            f"tar {member.size}, manifest {obj_entry.get('size')}"
                        )

                    # SHA-256 check
                    hasher = hashlib.sha256()
                    f = tar.extractfile(member)
                    if f is None:
                        errors.append(
                            f"Cannot extract member {member.name} from {chunk_file.name}"
                        )
                        continue
                    try:
                        while True:
                            chunk = f.read(64 * 1024)
                            if not chunk:
                                break
                            hasher.update(chunk)
                    finally:
                        f.close()

                    actual_hash = hasher.hexdigest()
                    expected_hash = obj_entry.get("sha256", "")
                    if actual_hash != expected_hash:
                        errors.append(
                            f"Hash mismatch for {member.name} in {chunk_file.name}: "
                            f"actual {actual_hash}, expected {expected_hash}"
                        )

                # Check for missing members
                for expected_name in expected:
                    if expected_name not in seen_names:
                        errors.append(
                            f"Missing member {expected_name} in {chunk_file.name}"
                        )

        except (tarfile.TarError, OSError) as e:
            errors.append(f"Cannot read tar {chunk_file.name}: {e}")

    # Check for extra chunk files
    for entry in dir_path.iterdir():
        if entry.is_file() and entry.name.startswith("chunk-") and entry.name.endswith(".tar"):
            match = re.match(r"^chunk-(\d+)\.tar$", entry.name)
            if match:
                ci = int(match.group(1))
                if ci >= chunk_count:
                    errors.append(f"Extra chunk file: {entry.name}")
            else:
                errors.append(f"Unparseable chunk file name: {entry.name}")

    return VerifyResult(
        ok=len(errors) == 0,
        errors=errors,
        object_count=manifest.get("object_count", 0),
        total_bytes=manifest.get("total_bytes", 0),
        chunk_count=chunk_count,
    )


@dataclass
class RestorePlanRow:
    """A single row in a restore plan."""

    key: str
    member_name: str
    chunk_index: int
    size: int
    sha256: str
    action: str  # create, conflict, skip, overwrite
    target_exists: bool


@dataclass
class RestorePlan:
    """Result of restore planning."""

    rows: list[RestorePlanRow] = field(default_factory=list)

    @property
    def has_conflicts(self) -> bool:
        return any(r.action == "conflict" for r in self.rows)

    @property
    def upload_rows(self) -> list[RestorePlanRow]:
        return [r for r in self.rows if r.action in ("create", "overwrite")]


def plan_restore(
    r2_client: R2Client,
    manifest: dict,
    prefixes: Optional[list[str]] = None,
    skip_existing: bool = False,
    overwrite_existing: bool = False,
) -> RestorePlan:
    """Build a restore plan from manifest and current bucket state.

    Args:
        r2_client: R2Client instance
        manifest: Loaded manifest dict
        prefixes: Optional list of key prefixes to filter
        skip_existing: If True, skip existing target objects
        overwrite_existing: If True, overwrite existing target objects

    Returns:
        RestorePlan with action for each object.
    """
    if skip_existing and overwrite_existing:
        raise RestoreError("--skip-existing and --overwrite-existing are mutually exclusive")

    objects = manifest.get("objects", [])
    plan = RestorePlan()

    try:
        for obj in objects:
            key = obj["key"]
            if prefixes:
                if not any(key.startswith(p) for p in prefixes):
                    continue

            head = r2_client.head_object(key)
            target_exists = head is not None

            if not target_exists:
                action = "create"
            elif skip_existing:
                action = "skip"
            elif overwrite_existing:
                action = "overwrite"
            else:
                action = "conflict"

            plan.rows.append(
                RestorePlanRow(
                    key=key,
                    member_name=obj["member_name"],
                    chunk_index=obj["chunk_index"],
                    size=obj["size"],
                    sha256=obj["sha256"],
                    action=action,
                    target_exists=target_exists,
                )
            )
    except ClientError as e:
        raise RestoreError(f"Failed to check target object metadata: {e}") from e

    return plan


def _build_extra_args(obj_entry: dict) -> dict:
    """Build boto3 ExtraArgs from manifest object metadata."""
    extra: dict = {}
    if obj_entry.get("content_type"):
        extra["ContentType"] = obj_entry["content_type"]
    if obj_entry.get("cache_control"):
        extra["CacheControl"] = obj_entry["cache_control"]
    if obj_entry.get("content_disposition"):
        extra["ContentDisposition"] = obj_entry["content_disposition"]
    if obj_entry.get("content_encoding"):
        extra["ContentEncoding"] = obj_entry["content_encoding"]
    if obj_entry.get("metadata"):
        extra["Metadata"] = obj_entry["metadata"]
    return extra


@dataclass
class RestoreResult:
    """Result of a restore operation."""

    uploaded: int = 0
    skipped: int = 0
    conflicts: int = 0
    failed: int = 0
    failures: list[dict] = field(default_factory=list)


def restore_from_archive(
    r2_client: R2Client,
    dir_path: Path,
    manifest: dict,
    plan: RestorePlan,
    confirm: bool = False,
) -> RestoreResult:
    """Execute a restore from a verified backup archive.

    Args:
        r2_client: R2Client instance
        dir_path: Path to backup directory
        manifest: Loaded manifest dict
        plan: Pre-built restore plan
        confirm: If True, perform uploads; if False, dry-run only

    Returns:
        RestoreResult with counts.
    """
    result = RestoreResult()

    # Count non-upload rows
    for row in plan.rows:
        if row.action == "skip":
            result.skipped += 1
        elif row.action == "conflict":
            result.conflicts += 1

    if not confirm:
        return result

    # Abort on conflicts
    if plan.has_conflicts:
        raise RestoreError(
            f"Cannot restore: {result.conflicts} unresolved conflict(s). "
            "Use --skip-existing or --overwrite-existing."
        )

    upload_rows = plan.upload_rows
    if not upload_rows:
        return result

    # Build manifest object lookup
    obj_by_key = {o["key"]: o for o in manifest.get("objects", [])}

    # Group by chunk_index
    by_chunk: dict[int, list[RestorePlanRow]] = {}
    for row in upload_rows:
        by_chunk.setdefault(row.chunk_index, []).append(row)

    for chunk_index, rows in sorted(by_chunk.items()):
        chunk_file = _chunk_path(dir_path, chunk_index)
        if not chunk_file.exists():
            for row in rows:
                result.failed += 1
                result.failures.append(
                    {"key": row.key, "error": f"Missing chunk file: {chunk_file.name}"}
                )
            continue

        try:
            with tarfile.open(chunk_file, "r") as tar:
                for row in rows:
                    try:
                        member = tar.getmember(row.member_name)
                        f = tar.extractfile(member)
                        if f is None:
                            result.failed += 1
                            result.failures.append(
                                {"key": row.key, "error": "Cannot extract member"}
                            )
                            continue

                        with f:
                            obj_entry = obj_by_key.get(row.key, {})
                            extra_args = _build_extra_args(obj_entry)

                            r2_client.upload_fileobj(
                                f, row.key, extra_args=extra_args
                            )
                        result.uploaded += 1
                    except Exception as e:
                        result.failed += 1
                        result.failures.append({"key": row.key, "error": str(e)})
        except (tarfile.TarError, OSError) as e:
            for row in rows:
                result.failed += 1
                result.failures.append(
                    {"key": row.key, "error": f"Cannot read chunk: {e}"}
                )

    return result
