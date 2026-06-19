"""R2 backup and restore service.

Implements full-bucket backup, verification, and restore for Cloudflare R2
disaster recovery.  Backups are chunked tar archives with a JSON manifest
that maps safe internal member names back to R2 keys and metadata.
"""

import hashlib
import io
import json
import os
import re
import shutil
import tarfile
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from botocore.exceptions import ClientError
from stream_of_worship.admin.services.r2 import R2Client

MANIFEST_VERSION = 4
DEFAULT_CHUNK_SIZE_BYTES = 10 * 1024 * 1024 * 1024  # 10 GiB
MIN_CHUNK_SIZE_BYTES = 64 * 1024 * 1024  # 64 MiB
PARTIAL_MARKER = ".sow-r2-backup-partial"
DEFAULT_CONCURRENCY = 8
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
    """A wrapper around a readable stream that computes SHA-256 as data is read.

    Also tracks total bytes read for short-read detection.
    """

    def __init__(self, source):
        self._source = source
        self._hasher = hashlib.sha256()
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        data = self._source.read(size)
        if data:
            self._hasher.update(data)
            self.bytes_read += len(data)
        return data

    @property
    def sha256_hex(self) -> str:
        return self._hasher.hexdigest()

    def close(self) -> None:
        if hasattr(self._source, "close"):
            self._source.close()


@dataclass
class DownloadResult:
    """Result of downloading a single object to a temp file."""

    temp_path: Path
    sha256: str
    bytes_read: int
    metadata: dict


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

                if get_etag != inv_obj.etag:
                    raise BackupError(
                        f"Object {inv_obj.key} ETag changed: inventory {inv_obj.etag}, "
                        f"download {get_etag}"
                    )

                if "-" not in get_etag:
                    md5_hex = hashlib.md5(temp_path.read_bytes()).hexdigest()
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

    required_space = int(inventory.total_bytes * 2.1) + 50 * 1024 * 1024
    _check_disk_space(output_dir.parent, required_space)

    partial_dir.mkdir(parents=True)
    (partial_dir / PARTIAL_MARKER).touch()
    temp_dir = partial_dir / "tmp"

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

        bytes_completed = 0

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {
                idx: executor.submit(
                    _download_object_to_tempfile, r2_client, inv_obj, temp_dir
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

                    _track_temp(download_result.temp_path)
                    try:
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
                for f in futures.values():
                    f.cancel()
                raise

        if current_tar is not None:
            current_tar.close()
            current_tar = None

        if manifest_objects and SPOT_CHECK_HEAD_RATIO > 0:
            sample_size = max(1, int(len(manifest_objects) * SPOT_CHECK_HEAD_RATIO))
            sample_indices = random.sample(
                range(len(manifest_objects)), min(sample_size, len(manifest_objects))
            )
            for sample_idx in sample_indices:
                obj_entry = manifest_objects[sample_idx]
                try:
                    head_data = r2_client.head_object(obj_entry["key"])
                    if head_data is None:
                        continue
                    if head_data["etag"] != obj_entry["etag"]:
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


def load_manifest(dir_path: Path) -> dict:
    """Load and return manifest.json from a backup directory.

    Raises:
        VerifyError: If manifest is missing or has wrong version.
    """
    manifest_path = dir_path / "manifest.json"
    if not manifest_path.exists():
        raise VerifyError(f"manifest.json not found in {dir_path}")
    with open(manifest_path) as f:
        manifest = json.load(f)
    if manifest.get("version") != MANIFEST_VERSION:
        raise VerifyError(
            f"Unsupported manifest version: {manifest.get('version')}, "
            f"expected {MANIFEST_VERSION}"
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
