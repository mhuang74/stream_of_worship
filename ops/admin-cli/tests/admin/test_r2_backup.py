"""Tests for R2 backup and restore service."""

import hashlib
import io
import json
import tarfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from stream_of_worship.admin.services.r2_backup import (
    DEFAULT_CHUNK_SIZE_BYTES,
    MANIFEST_VERSION,
    MIN_CHUNK_SIZE_BYTES,
    BackupError,
    BackupProgress,
    BackupTracer,
    HashingReader,
    RestoreError,
    build_inventory,
    load_manifest,
    parse_size,
    plan_restore,
    restore_from_archive,
    verify_archive,
    write_backup,
    SPOT_CHECK_HEAD_RATIO,
)


# ---------------------------------------------------------------------------
# parse_size tests
# ---------------------------------------------------------------------------


class TestParseSize:
    def test_raw_bytes(self):
        assert parse_size("1024") == 1024

    def test_kib(self):
        assert parse_size("1KiB") == 1024

    def test_mib(self):
        assert parse_size("1MiB") == 1024**2

    def test_gib(self):
        assert parse_size("10GiB") == 10 * 1024**3

    def test_tib(self):
        assert parse_size("1TiB") == 1024**4

    def test_decimal_kb(self):
        assert parse_size("1KB") == 1000

    def test_decimal_mb(self):
        assert parse_size("1MB") == 1000**2

    def test_decimal_gb(self):
        assert parse_size("1GB") == 1000**3

    def test_decimal_tb(self):
        assert parse_size("1TB") == 1000**4

    def test_with_spaces(self):
        assert parse_size("  10 GiB  ") == 10 * 1024**3

    def test_invalid_suffix(self):
        with pytest.raises(ValueError, match="Unknown size suffix"):
            parse_size("10XiB")

    def test_invalid_format(self):
        with pytest.raises(ValueError, match="Invalid size format"):
            parse_size("abc")

    def test_too_small_chunk(self):
        assert parse_size("1MiB") < MIN_CHUNK_SIZE_BYTES


# ---------------------------------------------------------------------------
# HashingReader tests
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# build_inventory tests
# ---------------------------------------------------------------------------


class TestBuildInventory:
    def test_builds_inventory_from_iter_objects(self):
        r2 = MagicMock()
        r2.iter_objects.return_value = iter([
            {"key": "a/audio.mp3", "size": 100, "etag": "etag1", "last_modified": "2024-01-01T00:00:00"},
            {"key": "b/audio.mp3", "size": 200, "etag": "etag2", "last_modified": "2024-01-02T00:00:00"},
        ])

        inventory = build_inventory(r2)

        assert inventory.object_count == 2
        assert inventory.total_bytes == 300
        assert inventory.objects[0].key == "a/audio.mp3"
        assert inventory.objects[1].etag == "etag2"
        assert inventory.started_at != ""
        assert inventory.completed_at != ""

    def test_empty_bucket_inventory(self):
        r2 = MagicMock()
        r2.iter_objects.return_value = iter([])

        inventory = build_inventory(r2)

        assert inventory.object_count == 0
        assert inventory.total_bytes == 0
        assert inventory.objects == []


# ---------------------------------------------------------------------------
# Manifest and member name tests
# ---------------------------------------------------------------------------


class TestManifestStructure:
    def test_manifest_version_constant(self):
        assert MANIFEST_VERSION == 4

    def test_default_chunk_size(self):
        assert DEFAULT_CHUNK_SIZE_BYTES == 10 * 1024**3

    def test_member_name_generation(self):
        from stream_of_worship.admin.services.r2_backup import _member_name_for_index

        assert _member_name_for_index(0) == "objects/000000000000.bin"
        assert _member_name_for_index(1) == "objects/000000000001.bin"
        assert _member_name_for_index(1234) == "objects/000000001234.bin"


# ---------------------------------------------------------------------------
# write_backup tests
# ---------------------------------------------------------------------------


def _make_r2_mock(objects: list[dict], head_data: dict | None = None) -> MagicMock:
    """Create a mock R2Client for backup tests.

    Args:
        objects: list of {key, size, etag, last_modified, data}
        head_data: optional override for head_object returns per key
    """
    r2 = MagicMock()
    r2.bucket = "test-bucket"
    r2.endpoint_url = "https://test.r2.cloudflarestorage.com"
    r2.region = "auto"

    obj_list = []
    for o in objects:
        etag = o.get("etag")
        if etag is None or "-" not in etag:
            etag = hashlib.md5(o["data"]).hexdigest()
        obj_list.append({
            "key": o["key"],
            "size": o["size"],
            "etag": etag,
            "last_modified": o.get("last_modified", ""),
        })
    r2.iter_objects.return_value = iter(obj_list)

    obj_map = {o["key"]: o for o in objects}

    def _get_object_stream(key):
        o = obj_map[key]
        body = io.BytesIO(o["data"])
        etag = o.get("etag")
        if etag is None or "-" not in etag:
            etag = hashlib.md5(o["data"]).hexdigest()
        return {
            "body": body,
            "content_length": len(o["data"]),
            "etag": etag,
            "last_modified": o.get("last_modified", ""),
            "content_type": o.get("content_type"),
            "cache_control": o.get("cache_control"),
            "content_disposition": o.get("content_disposition"),
            "content_encoding": o.get("content_encoding"),
            "metadata": o.get("metadata", {}),
        }

    r2.get_object_stream.side_effect = _get_object_stream

    def _head_object(key):
        if head_data and key in head_data:
            return head_data[key]
        o = obj_map.get(key)
        if o is None:
            return None
        etag = o.get("etag")
        if etag is None or "-" not in etag:
            etag = hashlib.md5(o["data"]).hexdigest()
        return {
            "size": len(o["data"]),
            "etag": etag,
            "last_modified": o.get("last_modified", ""),
            "content_type": o.get("content_type"),
            "cache_control": o.get("cache_control"),
            "content_disposition": o.get("content_disposition"),
            "content_encoding": o.get("content_encoding"),
            "metadata": o.get("metadata", {}),
        }

    r2.head_object.side_effect = _head_object

    return r2


class TestWriteBackup:
    def test_backup_creates_manifest_and_chunks(self, tmp_path):
        objects = [
            {"key": "a/audio.mp3", "size": 11, "etag": "etag1", "data": b"hello world"},
            {"key": "b/audio.mp3", "size": 5, "etag": "etag2", "data": b"hello"},
        ]
        r2 = _make_r2_mock(objects)
        inventory = build_inventory(r2)

        output = tmp_path / "backup"
        result = write_backup(r2, output, inventory, chunk_size_bytes=DEFAULT_CHUNK_SIZE_BYTES)

        assert result.object_count == 2
        assert result.total_bytes == 16
        assert result.chunk_count == 1
        assert output.exists()
        assert (output / "manifest.json").exists()
        assert (output / "chunk-000000.tar").exists()

        manifest = json.loads((output / "manifest.json").read_text())
        assert manifest["version"] == 4
        assert manifest["object_count"] == 2
        assert manifest["total_bytes"] == 16
        assert manifest["chunk_count"] == 1
        assert manifest["bucket"] == "test-bucket"
        assert len(manifest["objects"]) == 2
        assert manifest["objects"][0]["key"] == "a/audio.mp3"
        assert manifest["objects"][0]["member_name"] == "objects/000000000000.bin"
        assert manifest["objects"][0]["sha256"] == hashlib.sha256(b"hello world").hexdigest()
        assert manifest["objects"][0]["chunk_index"] == 0

    def test_backup_empty_bucket(self, tmp_path):
        r2 = _make_r2_mock([])
        inventory = build_inventory(r2)

        output = tmp_path / "empty_backup"
        result = write_backup(r2, output, inventory)

        assert result.object_count == 0
        assert result.total_bytes == 0
        assert result.chunk_count == 0
        assert output.exists()
        manifest = json.loads((output / "manifest.json").read_text())
        assert manifest["object_count"] == 0
        assert manifest["chunk_count"] == 0
        assert manifest["objects"] == []
        assert not (output / "chunk-000000.tar").exists()

    def test_backup_chunk_boundary(self, tmp_path):
        """Objects rotate to new chunk when exceeding chunk_size_bytes."""
        data1 = b"x" * 100
        data2 = b"y" * 100
        objects = [
            {"key": "a/file", "size": 100, "etag": "etag1", "data": data1},
            {"key": "b/file", "size": 100, "etag": "etag2", "data": data2},
        ]
        r2 = _make_r2_mock(objects)
        inventory = build_inventory(r2)

        output = tmp_path / "backup"
        # Set chunk size to 150 bytes so second object goes to new chunk
        result = write_backup(r2, output, inventory, chunk_size_bytes=150)

        assert result.chunk_count == 2
        assert (output / "chunk-000000.tar").exists()
        assert (output / "chunk-000001.tar").exists()

        manifest = json.loads((output / "manifest.json").read_text())
        assert manifest["objects"][0]["chunk_index"] == 0
        assert manifest["objects"][1]["chunk_index"] == 1

    def test_backup_large_object_exceeds_chunk(self, tmp_path):
        """A single object larger than chunk_size goes to its own chunk."""
        data = b"x" * 200
        objects = [
            {"key": "big/file", "size": 200, "etag": "etag1", "data": data},
        ]
        r2 = _make_r2_mock(objects)
        inventory = build_inventory(r2)

        output = tmp_path / "backup"
        result = write_backup(r2, output, inventory, chunk_size_bytes=100)

        assert result.chunk_count == 1
        assert result.object_count == 1

    def test_backup_refuses_existing_output(self, tmp_path):
        r2 = _make_r2_mock([])
        inventory = build_inventory(r2)
        output = tmp_path / "backup"
        output.mkdir()

        with pytest.raises(BackupError, match="Output directory already exists"):
            write_backup(r2, output, inventory)

    def test_backup_refuses_existing_partial(self, tmp_path):
        r2 = _make_r2_mock([])
        inventory = build_inventory(r2)
        output = tmp_path / "backup"
        partial = output.with_suffix(output.suffix + ".part")
        partial.mkdir()

        with pytest.raises(BackupError, match="Partial output directory already exists"):
            write_backup(r2, output, inventory)

    def test_backup_short_read_fails(self, tmp_path):
        """Short read (body returns fewer bytes than content_length) fails backup."""
        r2 = MagicMock()
        r2.bucket = "test-bucket"
        r2.endpoint_url = "https://test.r2.cloudflarestorage.com"
        r2.region = "auto"
        r2.iter_objects.return_value = iter([
            {"key": "a/file", "size": 100, "etag": "etag1", "last_modified": ""}
        ])

        def _get_object_stream(key):
            # Return fewer bytes than declared
            return {
                "body": io.BytesIO(b"short"),
                "content_length": 100,
                "etag": "etag1-with-dash-suffix",
                "last_modified": "",
                "content_type": None,
                "cache_control": None,
                "content_disposition": None,
                "content_encoding": None,
                "metadata": {},
            }

        r2.get_object_stream.side_effect = _get_object_stream

        inventory = build_inventory(r2)
        output = tmp_path / "backup"

        with pytest.raises(BackupError, match="Short read"):
            write_backup(r2, output, inventory)

        # Partial directory should be cleaned up
        assert not output.exists()
        partial = output.with_suffix(output.suffix + ".part")
        assert not partial.exists()

    def test_backup_changed_object_retries_and_succeeds(self, tmp_path):
        """Object that changes on first attempt succeeds on retry."""
        data = b"hello world"
        etag = hashlib.md5(data).hexdigest()
        objects = [{"key": "a/file", "size": 11, "etag": etag, "data": data}]
        r2 = _make_r2_mock(objects)

        # First GET returns different etag, second returns matching
        call_count = [0]
        original_get = r2.get_object_stream.side_effect

        def _flaky_get(key):
            call_count[0] += 1
            if call_count[0] == 1:
                resp = original_get(key)
                resp["etag"] = "changed_etag"
                return resp
            return original_get(key)

        r2.get_object_stream.side_effect = _flaky_get

        inventory = build_inventory(r2)
        output = tmp_path / "backup"
        result = write_backup(r2, output, inventory)

        assert result.object_count == 1
        assert output.exists()

    def test_backup_changed_object_exceeds_retries_fails(self, tmp_path):
        """Object that keeps changing fails after retry budget."""
        data = b"hello world"
        etag = hashlib.md5(data).hexdigest()
        objects = [{"key": "a/file", "size": 11, "etag": etag, "data": data}]
        r2 = _make_r2_mock(objects)

        # GET always returns different etag
        original_get = r2.get_object_stream.side_effect

        def _always_different_get(key):
            resp = original_get(key)
            resp["etag"] = "always_different"
            return resp

        r2.get_object_stream.side_effect = _always_different_get

        inventory = build_inventory(r2)
        output = tmp_path / "backup"

        with pytest.raises(BackupError, match="ETag changed"):
            write_backup(r2, output, inventory)

        assert not output.exists()

    def test_backup_deleted_object_during_backup_fails(self, tmp_path):
        """Object that disappears during backup fails and cleans up."""
        data = b"hello world"
        etag = hashlib.md5(data).hexdigest()
        objects = [{"key": "a/file", "size": 11, "etag": etag, "data": data}]
        r2 = _make_r2_mock(objects)

        # GET raises ClientError 404 (object deleted)
        from botocore.exceptions import ClientError

        def _not_found_get(key):
            raise ClientError(
                {"Error": {"Code": "404", "Message": "Not Found"}},
                "GetObject",
            )

        r2.get_object_stream.side_effect = _not_found_get

        inventory = build_inventory(r2)
        output = tmp_path / "backup"

        with pytest.raises(BackupError, match="Failed to backup"):
            write_backup(r2, output, inventory)

        assert not output.exists()

    def test_backup_preserves_metadata(self, tmp_path):
        """Backup manifest preserves content_type, cache_control, etc."""
        data = b"hello"
        objects = [
            {
                "key": "a/file",
                "size": 5,
                "etag": "etag1",
                "data": data,
                "content_type": "audio/mpeg",
                "cache_control": "max-age=3600",
                "content_disposition": "attachment",
                "content_encoding": "gzip",
                "metadata": {"custom": "value"},
            }
        ]
        r2 = _make_r2_mock(objects)
        inventory = build_inventory(r2)

        output = tmp_path / "backup"
        write_backup(r2, output, inventory)

        manifest = json.loads((output / "manifest.json").read_text())
        obj = manifest["objects"][0]
        assert obj["content_type"] == "audio/mpeg"
        assert obj["cache_control"] == "max-age=3600"
        assert obj["content_disposition"] == "attachment"
        assert obj["content_encoding"] == "gzip"
        assert obj["metadata"] == {"custom": "value"}

    def test_backup_cleanup_on_interrupt(self, tmp_path):
        """Failure during backup removes only owned partial directory."""
        r2 = MagicMock()
        r2.bucket = "test-bucket"
        r2.endpoint_url = "https://test.r2.cloudflarestorage.com"
        r2.region = "auto"
        r2.iter_objects.return_value = iter([
            {"key": "a/file", "size": 100, "etag": "etag1", "last_modified": ""}
        ])
        r2.get_object_stream.side_effect = RuntimeError("connection failed")

        inventory = build_inventory(r2)
        output = tmp_path / "backup"

        with pytest.raises(BackupError, match="Failed to backup"):
            write_backup(r2, output, inventory)

        assert not output.exists()
        partial = output.with_suffix(output.suffix + ".part")
        assert not partial.exists()


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


class TestConcurrentBackup:
    def test_concurrent_backup_produces_valid_archive(self, tmp_path):
        """Concurrent download with default concurrency produces valid backup."""
        objects = [
            {"key": f"obj{i}/file", "size": 100, "etag": f"etag{i}", "data": bytes([i]) * 100}
            for i in range(20)
        ]
        r2 = _make_r2_mock(objects)
        inventory = build_inventory(r2)
        output = tmp_path / "backup"

        result = write_backup(r2, output, inventory)

        assert result.object_count == 20
        assert result.total_bytes == 2000
        assert (output / "manifest.json").exists()
        assert (output / "chunk-000000.tar").exists()

        manifest = json.loads((output / "manifest.json").read_text())
        assert manifest["version"] == 4
        assert manifest["consistency"]["mode"] == "initial-inventory-with-get-etag-check"
        assert manifest["consistency"]["md5_body_check"] is True

        # Objects are in completion order, not inventory order — verify by key lookup
        obj_by_key = {o["key"]: o for o in manifest["objects"]}
        for i in range(20):
            key = f"obj{i}/file"
            assert key in obj_by_key
            expected_hash = hashlib.sha256(bytes([i]) * 100).hexdigest()
            assert obj_by_key[key]["sha256"] == expected_hash

    def test_concurrency_1_works(self, tmp_path):
        """concurrency=1 falls back to sequential (no thread pool issues)."""
        objects = [
            {"key": "a/file", "size": 5, "etag": "etag1", "data": b"hello"},
            {"key": "b/file", "size": 5, "etag": "etag2", "data": b"world"},
        ]
        r2 = _make_r2_mock(objects)
        inventory = build_inventory(r2)
        output = tmp_path / "backup"

        result = write_backup(r2, output, inventory, concurrency=1)

        assert result.object_count == 2
        assert output.exists()

    def test_concurrent_backup_preserves_object_order(self, tmp_path):
        """Manifest objects are in completion order with as_completed, but all present."""
        objects = [
            {"key": f"obj{i}/file", "size": 10, "etag": f"etag{i}", "data": bytes([i]) * 10}
            for i in range(10)
        ]
        r2 = _make_r2_mock(objects)
        inventory = build_inventory(r2)
        output = tmp_path / "backup"

        write_backup(r2, output, inventory, concurrency=4)

        manifest = json.loads((output / "manifest.json").read_text())
        # All objects present regardless of completion order
        keys = {o["key"] for o in manifest["objects"]}
        assert keys == {f"obj{i}/file" for i in range(10)}

    def test_concurrent_backup_cleans_up_temp_files(self, tmp_path):
        """No temp files remain after successful backup."""
        objects = [
            {"key": f"obj{i}/file", "size": 100, "etag": f"etag{i}", "data": b"x" * 100}
            for i in range(5)
        ]
        r2 = _make_r2_mock(objects)
        inventory = build_inventory(r2)
        output = tmp_path / "backup"

        write_backup(r2, output, inventory)

        temp_dir = output.with_suffix(output.suffix + ".part")
        assert not temp_dir.exists()
        assert not (output / "tmp").exists()

    def test_concurrent_backup_cleans_up_temp_files_on_failure(self, tmp_path):
        """Temp files are cleaned up when backup fails mid-way."""
        data1 = b"hello"
        data2 = b"world"
        etag1 = hashlib.md5(data1).hexdigest()
        etag2 = hashlib.md5(data2).hexdigest()
        objects = [
            {"key": "a/file", "size": 5, "etag": etag1, "data": data1},
            {"key": "b/file", "size": 5, "etag": etag2, "data": data2},
        ]
        r2 = _make_r2_mock(objects)

        # Make the second object fail with a different etag
        original_get = r2.get_object_stream.side_effect

        def _flaky_get(key):
            if key == "b/file":
                resp = original_get(key)
                resp["etag"] = "wrong"
                return resp
            return original_get(key)

        r2.get_object_stream.side_effect = _flaky_get

        inventory = build_inventory(r2)
        output = tmp_path / "backup"

        with pytest.raises(BackupError):
            write_backup(r2, output, inventory)

        assert not output.exists()
        partial = output.with_suffix(output.suffix + ".part")
        assert not partial.exists()

    def test_concurrent_backup_failure_cancels_remaining(self, tmp_path):
        """On failure, remaining futures are cancelled and partial dir cleaned up."""
        r2 = MagicMock()
        r2.bucket = "test-bucket"
        r2.endpoint_url = "https://test.r2.cloudflarestorage.com"
        r2.region = "auto"
        r2.iter_objects.return_value = iter([
            {"key": "a/file", "size": 100, "etag": "etag1", "last_modified": ""},
            {"key": "b/file", "size": 100, "etag": "etag2", "last_modified": ""},
        ])
        r2.get_object_stream.side_effect = RuntimeError("connection failed")

        inventory = build_inventory(r2)
        output = tmp_path / "backup"

        with pytest.raises(BackupError, match="Failed to backup"):
            write_backup(r2, output, inventory)

        assert not output.exists()
        partial = output.with_suffix(output.suffix + ".part")
        assert not partial.exists()

    def test_md5_body_check_catches_corruption(self, tmp_path):
        """Single-part object with corrupted body fails with MD5 mismatch."""
        data = b"hello world"
        etag = hashlib.md5(data).hexdigest()
        objects = [{"key": "a/file", "size": 11, "etag": etag, "data": data}]
        r2 = _make_r2_mock(objects)

        # Corrupt the body but keep the correct etag
        original_get = r2.get_object_stream.side_effect

        def _corrupted_get(key):
            resp = original_get(key)
            resp["body"] = io.BytesIO(b"corrupted!")
            return resp

        r2.get_object_stream.side_effect = _corrupted_get

        inventory = build_inventory(r2)
        output = tmp_path / "backup"

        with pytest.raises(BackupError, match="Short read|Size mismatch|MD5 mismatch"):
            write_backup(r2, output, inventory)

        assert not output.exists()

    def test_spot_check_head_warns_on_drift(self, tmp_path):
        """Spot-check HEAD logs warning when object changed after backup."""
        data = b"hello"
        etag = hashlib.md5(data).hexdigest()
        objects = [{"key": "a/file", "size": 5, "etag": etag, "data": data}]
        r2 = _make_r2_mock(objects)

        # head_object returns different etag (drift detected)
        r2.head_object.side_effect = lambda key: {
            "size": 5,
            "etag": "different_etag",
            "last_modified": "",
            "content_type": None,
            "cache_control": None,
            "content_disposition": None,
            "content_encoding": None,
            "metadata": {},
        }

        inventory = build_inventory(r2)
        output = tmp_path / "backup"

        result = write_backup(r2, output, inventory)

        assert result.object_count == 1
        assert output.exists()

    def test_manifest_version_4(self, tmp_path):
        """Backup produces manifest version 4."""
        objects = [{"key": "a/file", "size": 5, "etag": "etag1", "data": b"hello"}]
        r2 = _make_r2_mock(objects)
        inventory = build_inventory(r2)
        output = tmp_path / "backup"

        write_backup(r2, output, inventory)

        manifest = json.loads((output / "manifest.json").read_text())
        assert manifest["version"] == 4
        assert manifest["consistency"]["mode"] == "initial-inventory-with-get-etag-check"
        assert manifest["consistency"]["md5_body_check"] is True
        assert manifest["consistency"]["spot_check_head_ratio"] == SPOT_CHECK_HEAD_RATIO

    def test_get_last_modified_in_metadata(self, tmp_path):
        """Manifest stores GET response last_modified in metadata."""
        data = b"hello"
        objects = [
            {
                "key": "a/file",
                "size": 5,
                "etag": "etag1",
                "data": data,
                "last_modified": "2024-01-15T10:30:00+00:00",
            }
        ]
        r2 = _make_r2_mock(objects)
        inventory = build_inventory(r2)
        output = tmp_path / "backup"

        write_backup(r2, output, inventory)

        manifest = json.loads((output / "manifest.json").read_text())
        obj = manifest["objects"][0]
        assert obj["last_modified"] == "2024-01-15T10:30:00+00:00"

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


# ---------------------------------------------------------------------------
# verify_archive tests
# ---------------------------------------------------------------------------


def _create_valid_backup(tmp_path, objects: list[dict] | None = None) -> Path:
    """Helper to create a valid backup directory for verification tests."""
    objects = objects or []
    r2 = _make_r2_mock(objects)
    inventory = build_inventory(r2)
    output = tmp_path / "backup"
    write_backup(r2, output, inventory)
    return output


class TestVerifyArchive:
    def test_verify_ok(self, tmp_path):
        data1 = b"hello world"
        data2 = b"foo bar"
        objects = [
            {"key": "a/file", "size": len(data1), "etag": "etag1", "data": data1},
            {"key": "b/file", "size": len(data2), "etag": "etag2", "data": data2},
        ]
        backup_dir = _create_valid_backup(tmp_path, objects)

        result = verify_archive(backup_dir)

        assert result.ok is True
        assert result.errors == []
        assert result.object_count == 2
        assert result.total_bytes == len(data1) + len(data2)

    def test_verify_empty_backup_ok(self, tmp_path):
        backup_dir = _create_valid_backup(tmp_path, [])

        result = verify_archive(backup_dir)

        assert result.ok is True
        assert result.object_count == 0
        assert result.chunk_count == 0

    def test_verify_missing_manifest(self, tmp_path):
        backup_dir = tmp_path / "backup"
        backup_dir.mkdir()

        result = verify_archive(backup_dir)

        assert result.ok is False
        assert any("manifest.json not found" in e for e in result.errors)

    def test_verify_unsupported_version(self, tmp_path):
        backup_dir = tmp_path / "backup"
        backup_dir.mkdir()
        (backup_dir / "manifest.json").write_text(json.dumps({"version": 99, "objects": []}))

        result = verify_archive(backup_dir)

        assert result.ok is False
        assert any("Unsupported manifest version" in e for e in result.errors)

    def test_verify_v3_manifest_still_supported(self, tmp_path):
        """Manifests with version 3 (from previous implementation) are still readable."""
        backup_dir = _create_valid_backup(tmp_path, [
            {"key": "a/file", "size": 5, "etag": "etag1", "data": b"hello"}
        ])
        # Downgrade manifest version to 3 to simulate old backup
        manifest = json.loads((backup_dir / "manifest.json").read_text())
        manifest["version"] = 3
        (backup_dir / "manifest.json").write_text(json.dumps(manifest))

        result = verify_archive(backup_dir)

        assert result.ok is True
        assert result.object_count == 1

    def test_verify_corrupt_tar(self, tmp_path):
        backup_dir = _create_valid_backup(tmp_path, [
            {"key": "a/file", "size": 5, "etag": "etag1", "data": b"hello"}
        ])
        # Corrupt the tar file
        (backup_dir / "chunk-000000.tar").write_bytes(b"not a tar file")

        result = verify_archive(backup_dir)

        assert result.ok is False
        assert any("Cannot read tar" in e for e in result.errors)

    def test_verify_missing_chunk(self, tmp_path):
        backup_dir = _create_valid_backup(tmp_path, [
            {"key": "a/file", "size": 5, "etag": "etag1", "data": b"hello"}
        ])
        (backup_dir / "chunk-000000.tar").unlink()

        result = verify_archive(backup_dir)

        assert result.ok is False
        assert any("Missing chunk file" in e for e in result.errors)

    def test_verify_orphan_member(self, tmp_path):
        backup_dir = _create_valid_backup(tmp_path, [
            {"key": "a/file", "size": 5, "etag": "etag1", "data": b"hello"}
        ])
        # Add an orphan member to the tar
        with tarfile.open(backup_dir / "chunk-000000.tar", "a") as tar:
            info = tarfile.TarInfo(name="objects/orphan.bin")
            info.size = 3
            info.mtime = 0
            tar.addfile(info, io.BytesIO(b"abc"))

        result = verify_archive(backup_dir)

        assert result.ok is False
        assert any("Orphan member" in e for e in result.errors)

    def test_verify_duplicate_member(self, tmp_path):
        """Duplicate members in a tar are rejected."""
        backup_dir = _create_valid_backup(tmp_path, [
            {"key": "a/file", "size": 5, "etag": "etag1", "data": b"hello"}
        ])
        # We need to create a tar with duplicate member names
        # Rewrite the tar with a duplicate
        chunk_path = backup_dir / "chunk-000000.tar"
        chunk_path.unlink()

        with tarfile.open(chunk_path, "w") as tar:
            for name in ["objects/000000000000.bin", "objects/000000000000.bin"]:
                info = tarfile.TarInfo(name=name)
                info.size = 5
                info.mtime = 0
                tar.addfile(info, io.BytesIO(b"hello"))

        result = verify_archive(backup_dir)

        assert result.ok is False
        assert any("Duplicate member" in e for e in result.errors)

    def test_verify_duplicate_key(self, tmp_path):
        """Duplicate keys in manifest are rejected."""
        backup_dir = _create_valid_backup(tmp_path, [
            {"key": "a/file", "size": 5, "etag": "etag1", "data": b"hello"}
        ])
        # Tamper with manifest to add duplicate key
        manifest = json.loads((backup_dir / "manifest.json").read_text())
        manifest["objects"].append(dict(manifest["objects"][0]))
        (backup_dir / "manifest.json").write_text(json.dumps(manifest))

        result = verify_archive(backup_dir)

        assert result.ok is False
        assert any("Duplicate object key" in e for e in result.errors)

    def test_verify_non_regular_member(self, tmp_path):
        """Non-regular members (symlinks, directories) are rejected."""
        backup_dir = _create_valid_backup(tmp_path, [
            {"key": "a/file", "size": 5, "etag": "etag1", "data": b"hello"}
        ])
        # Rewrite tar with a directory member
        chunk_path = backup_dir / "chunk-000000.tar"
        chunk_path.unlink()

        with tarfile.open(chunk_path, "w") as tar:
            # Add the expected regular member
            info = tarfile.TarInfo(name="objects/000000000000.bin")
            info.size = 5
            info.mtime = 0
            tar.addfile(info, io.BytesIO(b"hello"))
            # Add a directory member
            dir_info = tarfile.TarInfo(name="objects/")
            dir_info.type = tarfile.DIRTYPE
            dir_info.mode = 0o755
            tar.addfile(dir_info)

        result = verify_archive(backup_dir)

        assert result.ok is False
        assert any("Non-regular member" in e for e in result.errors)

    def test_verify_size_mismatch(self, tmp_path):
        """Member size not matching manifest is rejected."""
        backup_dir = _create_valid_backup(tmp_path, [
            {"key": "a/file", "size": 5, "etag": "etag1", "data": b"hello"}
        ])
        # Tamper with manifest to change expected size
        manifest = json.loads((backup_dir / "manifest.json").read_text())
        manifest["objects"][0]["size"] = 999
        (backup_dir / "manifest.json").write_text(json.dumps(manifest))

        result = verify_archive(backup_dir)

        assert result.ok is False
        assert any("Size mismatch" in e for e in result.errors)

    def test_verify_hash_mismatch(self, tmp_path):
        """Member SHA-256 not matching manifest is rejected."""
        backup_dir = _create_valid_backup(tmp_path, [
            {"key": "a/file", "size": 5, "etag": "etag1", "data": b"hello"}
        ])
        # Tamper with manifest to change expected hash
        manifest = json.loads((backup_dir / "manifest.json").read_text())
        manifest["objects"][0]["sha256"] = "0" * 64
        (backup_dir / "manifest.json").write_text(json.dumps(manifest))

        result = verify_archive(backup_dir)

        assert result.ok is False
        assert any("Hash mismatch" in e for e in result.errors)


# ---------------------------------------------------------------------------
# plan_restore tests
# ---------------------------------------------------------------------------


class TestPlanRestore:
    def _make_manifest(self, objects: list[dict]) -> dict:
        return {
            "version": 4,
            "objects": objects,
        }

    def test_plan_create_for_missing_objects(self):
        r2 = MagicMock()
        r2.head_object.return_value = None

        manifest = self._make_manifest([
            {"key": "a/file", "member_name": "objects/000000000000.bin", "chunk_index": 0, "size": 5, "sha256": "abc"},
        ])

        plan = plan_restore(r2, manifest)

        assert len(plan.rows) == 1
        assert plan.rows[0].action == "create"
        assert not plan.has_conflicts

    def test_plan_conflict_for_existing_objects(self):
        r2 = MagicMock()
        r2.head_object.return_value = {"size": 5, "etag": "etag"}

        manifest = self._make_manifest([
            {"key": "a/file", "member_name": "objects/000000000000.bin", "chunk_index": 0, "size": 5, "sha256": "abc"},
        ])

        plan = plan_restore(r2, manifest)

        assert len(plan.rows) == 1
        assert plan.rows[0].action == "conflict"
        assert plan.has_conflicts

    def test_plan_skip_existing(self):
        r2 = MagicMock()
        r2.head_object.return_value = {"size": 5, "etag": "etag"}

        manifest = self._make_manifest([
            {"key": "a/file", "member_name": "objects/000000000000.bin", "chunk_index": 0, "size": 5, "sha256": "abc"},
        ])

        plan = plan_restore(r2, manifest, skip_existing=True)

        assert plan.rows[0].action == "skip"

    def test_plan_overwrite_existing(self):
        r2 = MagicMock()
        r2.head_object.return_value = {"size": 5, "etag": "etag"}

        manifest = self._make_manifest([
            {"key": "a/file", "member_name": "objects/000000000000.bin", "chunk_index": 0, "size": 5, "sha256": "abc"},
        ])

        plan = plan_restore(r2, manifest, overwrite_existing=True)

        assert plan.rows[0].action == "overwrite"

    def test_plan_mutually_exclusive_flags(self):
        r2 = MagicMock()
        manifest = self._make_manifest([])

        with pytest.raises(RestoreError, match="mutually exclusive"):
            plan_restore(r2, manifest, skip_existing=True, overwrite_existing=True)

    def test_plan_prefix_filtering(self):
        r2 = MagicMock()
        r2.head_object.return_value = None

        manifest = self._make_manifest([
            {"key": "prefix_a/file", "member_name": "objects/000000000000.bin", "chunk_index": 0, "size": 5, "sha256": "abc"},
            {"key": "prefix_b/file", "member_name": "objects/000000000001.bin", "chunk_index": 0, "size": 5, "sha256": "def"},
        ])

        plan = plan_restore(r2, manifest, prefixes=["prefix_a"])

        assert len(plan.rows) == 1
        assert plan.rows[0].key == "prefix_a/file"

    def test_plan_no_objects_matched(self):
        r2 = MagicMock()
        manifest = self._make_manifest([
            {"key": "a/file", "member_name": "objects/000000000000.bin", "chunk_index": 0, "size": 5, "sha256": "abc"},
        ])

        plan = plan_restore(r2, manifest, prefixes=["nonexistent_prefix"])

        assert len(plan.rows) == 0


# ---------------------------------------------------------------------------
# restore_from_archive tests
# ---------------------------------------------------------------------------


class TestRestoreFromArchive:
    def test_restore_dry_run_no_upload(self, tmp_path):
        """Dry run (confirm=False) performs no uploads."""
        data = b"hello"
        objects = [{"key": "a/file", "size": 5, "etag": "etag1", "data": data}]
        backup_dir = _create_valid_backup(tmp_path, objects)

        r2 = MagicMock()
        r2.head_object.return_value = None

        manifest = load_manifest(backup_dir)
        plan = plan_restore(r2, manifest)

        result = restore_from_archive(r2, backup_dir, manifest, plan, confirm=False)

        assert result.uploaded == 0
        r2.upload_fileobj.assert_not_called()

    def test_restore_conflict_aborts_before_upload(self, tmp_path):
        """Restore with conflicts and --confirm aborts before any upload."""
        data = b"hello"
        objects = [{"key": "a/file", "size": 5, "etag": "etag1", "data": data}]
        backup_dir = _create_valid_backup(tmp_path, objects)

        r2 = MagicMock()
        r2.head_object.return_value = {"size": 5, "etag": "different"}

        manifest = load_manifest(backup_dir)
        plan = plan_restore(r2, manifest)

        with pytest.raises(RestoreError, match="unresolved conflict"):
            restore_from_archive(r2, backup_dir, manifest, plan, confirm=True)

        r2.upload_fileobj.assert_not_called()

    def test_restore_skip_existing_skips_conflicts(self, tmp_path):
        """--skip-existing skips existing objects without uploading."""
        data = b"hello"
        objects = [{"key": "a/file", "size": 5, "etag": "etag1", "data": data}]
        backup_dir = _create_valid_backup(tmp_path, objects)

        r2 = MagicMock()
        r2.head_object.return_value = {"size": 5, "etag": "different"}

        manifest = load_manifest(backup_dir)
        plan = plan_restore(r2, manifest, skip_existing=True)

        result = restore_from_archive(r2, backup_dir, manifest, plan, confirm=True)

        assert result.uploaded == 0
        assert result.skipped == 1
        r2.upload_fileobj.assert_not_called()

    def test_restore_overwrite_existing_uploads(self, tmp_path):
        """--overwrite-existing uploads over existing objects."""
        data = b"hello"
        objects = [{"key": "a/file", "size": 5, "etag": "etag1", "data": data}]
        backup_dir = _create_valid_backup(tmp_path, objects)

        r2 = MagicMock()
        r2.head_object.return_value = {"size": 5, "etag": "different"}

        manifest = load_manifest(backup_dir)
        plan = plan_restore(r2, manifest, overwrite_existing=True)

        result = restore_from_archive(r2, backup_dir, manifest, plan, confirm=True)

        assert result.uploaded == 1
        r2.upload_fileobj.assert_called_once()

    def test_restore_creates_missing_objects(self, tmp_path):
        """Restore with --confirm creates missing objects."""
        data = b"hello"
        objects = [{"key": "a/file", "size": 5, "etag": "etag1", "data": data}]
        backup_dir = _create_valid_backup(tmp_path, objects)

        r2 = MagicMock()
        r2.head_object.return_value = None

        manifest = load_manifest(backup_dir)
        plan = plan_restore(r2, manifest)

        result = restore_from_archive(r2, backup_dir, manifest, plan, confirm=True)

        assert result.uploaded == 1
        r2.upload_fileobj.assert_called_once()

    def test_restore_preserves_metadata(self, tmp_path):
        """Restore uploads with extra_args containing stored metadata."""
        data = b"hello"
        objects = [
            {
                "key": "a/file",
                "size": 5,
                "etag": "etag1",
                "data": data,
                "content_type": "audio/mpeg",
                "cache_control": "max-age=3600",
                "content_disposition": "attachment",
                "content_encoding": "gzip",
                "metadata": {"custom": "val"},
            }
        ]
        backup_dir = _create_valid_backup(tmp_path, objects)

        r2 = MagicMock()
        r2.head_object.return_value = None

        manifest = load_manifest(backup_dir)
        plan = plan_restore(r2, manifest)

        restore_from_archive(r2, backup_dir, manifest, plan, confirm=True)

        call_kwargs = r2.upload_fileobj.call_args
        extra_args = call_kwargs.kwargs["extra_args"]
        assert extra_args["ContentType"] == "audio/mpeg"
        assert extra_args["CacheControl"] == "max-age=3600"
        assert extra_args["ContentDisposition"] == "attachment"
        assert extra_args["ContentEncoding"] == "gzip"
        assert extra_args["Metadata"] == {"custom": "val"}

    def test_restore_opens_each_chunk_once(self, tmp_path):
        """Restore groups by chunk_index and opens each chunk once."""
        data1 = b"x" * 100
        data2 = b"y" * 100
        data3 = b"z" * 100
        objects = [
            {"key": "a/file", "size": 100, "etag": "etag1", "data": data1},
            {"key": "b/file", "size": 100, "etag": "etag2", "data": data2},
            {"key": "c/file", "size": 100, "etag": "etag3", "data": data3},
        ]
        r2 = _make_r2_mock(objects)
        inventory = build_inventory(r2)
        backup_dir = tmp_path / "backup"
        # Use small chunk size to force 2 chunks (150 bytes per chunk)
        write_backup(r2, backup_dir, inventory, chunk_size_bytes=150)

        # Now restore
        restore_r2 = MagicMock()
        restore_r2.head_object.return_value = None

        manifest = load_manifest(backup_dir)
        plan = plan_restore(restore_r2, manifest)

        result = restore_from_archive(restore_r2, backup_dir, manifest, plan, confirm=True)

        assert result.uploaded == 3
        # 2 chunks, each opened once
        assert restore_r2.upload_fileobj.call_count == 3

    def test_restore_continues_on_upload_failure(self, tmp_path):
        """If an upload fails, restore continues with remaining uploads."""
        data1 = b"hello"
        data2 = b"world"
        objects = [
            {"key": "a/file", "size": 5, "etag": "etag1", "data": data1},
            {"key": "b/file", "size": 5, "etag": "etag2", "data": data2},
        ]
        backup_dir = _create_valid_backup(tmp_path, objects)

        r2 = MagicMock()
        r2.head_object.return_value = None

        # First upload fails, second succeeds
        call_count = [0]

        def _upload(fileobj, s3_key, extra_args=None):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("upload failed")

        r2.upload_fileobj.side_effect = _upload

        manifest = load_manifest(backup_dir)
        plan = plan_restore(r2, manifest)

        result = restore_from_archive(r2, backup_dir, manifest, plan, confirm=True)

        assert result.uploaded == 1
        assert result.failed == 1
        assert len(result.failures) == 1


# ---------------------------------------------------------------------------
# BackupTracer tests
# ---------------------------------------------------------------------------


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
        tracer.object_download_trace(
            key="k",
            worker="t1",
            attempt=0,
            conn_ms=1.0,
            stream_ms=2.0,
            bytes_read=10,
            retries=0,
        )
        tracer.tar_write_trace(
            idx=0, key="k", wait_ms=1.0, tar_write_ms=2.0, bytes_written=10
        )
        tracer.bytes_downloaded_sample(100, 1)
        tracer.finalize(total_objects=1, total_bytes=10)
        # No assert needed; absence of error proves no-op

    def test_phase_start_end_emits_debug(self, caplog):
        import logging

        with caplog.at_level(
            logging.DEBUG, logger="stream_of_worship.admin.services.r2_backup"
        ):
            tracer = BackupTracer()
            tracer.phase_start("phase_x")
            tracer.phase_end("phase_x", foo="bar")
        assert any("phase_end name=phase_x" in r.message for r in caplog.records)
        assert any("foo=bar" in r.message for r in caplog.records)

    def test_object_download_trace_updates_accumulators(self, caplog):
        import logging

        with caplog.at_level(
            logging.DEBUG, logger="stream_of_worship.admin.services.r2_backup"
        ):
            tracer = BackupTracer()
            tracer.object_download_trace(
                key="a",
                worker="t1",
                attempt=0,
                conn_ms=10.0,
                stream_ms=100.0,
                bytes_read=1024 * 1024,
                retries=0,
            )
        assert any("object_download key=a" in r.message for r in caplog.records)
        assert tracer._bytes_downloaded == 1024 * 1024
        assert tracer._sum_download_ms == 100.0
        assert tracer._sum_conn_ms == 10.0
        assert tracer._max_object_download_ms == 110.0
        assert tracer._max_object_download_key == "a"

    def test_tar_write_trace_updates_accumulators(self, caplog):
        import logging

        with caplog.at_level(
            logging.DEBUG, logger="stream_of_worship.admin.services.r2_backup"
        ):
            tracer = BackupTracer()
            tracer.tar_write_trace(
                idx=0, key="a", wait_ms=50.0, tar_write_ms=30.0, bytes_written=1024 * 1024
            )
            tracer.tar_write_trace(
                idx=1, key="b", wait_ms=10.0, tar_write_ms=20.0, bytes_written=1024 * 1024
            )
        assert tracer._sum_wait_ms == 60.0
        assert tracer._sum_tar_write_ms == 50.0
        assert tracer._bytes_written == 2 * 1024 * 1024
        assert tracer._object_count_written == 2
        assert any("wait_is_bottleneck=yes" in r.message for r in caplog.records)  # 50 > 30
        assert any("wait_is_bottleneck=no" in r.message for r in caplog.records)  # 10 < 20

    def test_bytes_downloaded_sample_throttles_logs(self, caplog):
        """Throughput log emitted at most once per THROUGHPUT_SAMPLE_INTERVAL."""
        import logging

        with caplog.at_level(
            logging.DEBUG, logger="stream_of_worship.admin.services.r2_backup"
        ):
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

        with caplog.at_level(
            logging.DEBUG, logger="stream_of_worship.admin.services.r2_backup"
        ):
            tracer = BackupTracer()
            tracer._run_start = 0.0
            tracer.bytes_downloaded_sample(0, 4)
            # Simulate wall-clock passage
            import time as _t

            new_t = _t.time() + 60  # 60s later in real time
            tracer._throughput_samples.append((new_t, 100 * 1024 * 1024, 4))
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
                    key=f"k{i}",
                    worker="w",
                    attempt=0,
                    conn_ms=1.0,
                    stream_ms=1.0,
                    bytes_read=100,
                    retries=0,
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

        with caplog.at_level(
            logging.DEBUG, logger="stream_of_worship.admin.services.r2_backup"
        ):
            tracer = BackupTracer()
            result = write_backup(
                r2,
                output,
                inventory,
                tracer=tracer,
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

        with caplog.at_level(
            logging.DEBUG, logger="stream_of_worship.admin.services.r2_backup"
        ):
            result = write_backup(r2, output, inventory)
        assert result.object_count == 1
        assert not [r for r in caplog.records if "object_download" in r.message]
        assert not [r for r in caplog.records if "tar_write" in r.message]
        assert not [r for r in caplog.records if "summary" in r.message]


# ---------------------------------------------------------------------------
# as_completed tar ingestion tests
# ---------------------------------------------------------------------------


class TestAsCompletedTarIngestion:
    def test_chunk_index_follows_completion_order_not_submission_order(self, tmp_path):
        """With as_completed, chunk_index is assigned in completion order."""
        # Create objects where the second one "downloads" faster than the first
        # by using a mock that simulates delay based on key
        data_large = b"x" * 100
        data_small = b"y" * 50
        objects = [
            {"key": "a/large", "size": 100, "etag": "etag1", "data": data_large},
            {"key": "b/small", "size": 50, "etag": "etag2", "data": data_small},
        ]
        r2 = _make_r2_mock(objects)
        inventory = build_inventory(r2)
        output = tmp_path / "backup"

        # Use tiny chunk size so each object gets its own chunk
        result = write_backup(r2, output, inventory, chunk_size_bytes=75)

        assert result.object_count == 2
        assert result.chunk_count == 2

        manifest = json.loads((output / "manifest.json").read_text())
        # Both objects present
        keys = {o["key"] for o in manifest["objects"]}
        assert keys == {"a/large", "b/small"}
        # chunk_index values are valid
        for obj in manifest["objects"]:
            assert obj["chunk_index"] in {0, 1}

        # Verify archive still passes
        verify_result = verify_archive(output)
        assert verify_result.ok is True

    def test_all_objects_present_after_as_completed(self, tmp_path):
        """All objects are present in manifest after as_completed rewrite."""
        objects = [
            {"key": f"obj{i}/file", "size": 10, "etag": f"etag{i}", "data": bytes([i]) * 10}
            for i in range(10)
        ]
        r2 = _make_r2_mock(objects)
        inventory = build_inventory(r2)
        output = tmp_path / "backup"

        result = write_backup(r2, output, inventory, concurrency=4)

        assert result.object_count == 10
        manifest = json.loads((output / "manifest.json").read_text())
        assert len(manifest["objects"]) == 10
        keys = {o["key"] for o in manifest["objects"]}
        assert keys == {f"obj{i}/file" for i in range(10)}

        verify_result = verify_archive(output)
        assert verify_result.ok is True


class TestInventorySortBySize:
    def test_submission_order_is_size_descending(self, tmp_path):
        """Large objects are submitted first to ThreadPoolExecutor."""
        objects = [
            {"key": "small/file", "size": 10, "etag": "etag1", "data": b"x" * 10},
            {"key": "large/file", "size": 100, "etag": "etag2", "data": b"y" * 100},
            {"key": "medium/file", "size": 50, "etag": "etag3", "data": b"z" * 50},
        ]
        r2 = _make_r2_mock(objects)
        inventory = build_inventory(r2)
        output = tmp_path / "backup"

        write_backup(r2, output, inventory)

        manifest = json.loads((output / "manifest.json").read_text())
        # member_name indices should still be original inventory order
        obj_by_key = {o["key"]: o for o in manifest["objects"]}
        # All objects present
        assert set(obj_by_key.keys()) == {"small/file", "large/file", "medium/file"}

    def test_member_name_indices_are_original_inventory_order(self, tmp_path):
        """member_name uses original inventory index, not submission order."""
        objects = [
            {"key": "a/file", "size": 10, "etag": "etag1", "data": b"x" * 10},
            {"key": "b/file", "size": 100, "etag": "etag2", "data": b"y" * 100},
        ]
        r2 = _make_r2_mock(objects)
        inventory = build_inventory(r2)
        output = tmp_path / "backup"

        write_backup(r2, output, inventory)

        manifest = json.loads((output / "manifest.json").read_text())
        # Find the object entries by key
        obj_by_key = {o["key"]: o for o in manifest["objects"]}
        # a/file was inventory index 0, b/file was index 1
        assert obj_by_key["a/file"]["member_name"] == "objects/000000000000.bin"
        assert obj_by_key["b/file"]["member_name"] == "objects/000000000001.bin"


class TestRetryTraceEmission:
    def test_retry_trace_called_on_timeout_error(self, tmp_path, caplog):
        """retry_trace is called with correct error_code when a retry fires."""
        import logging
        from botocore.exceptions import ClientError

        data = b"hello world"
        etag = hashlib.md5(data).hexdigest()
        objects = [{"key": "a/file", "size": 11, "etag": etag, "data": data}]
        r2 = _make_r2_mock(objects)

        call_count = [0]
        original_get = r2.get_object_stream.side_effect

        def _timeout_then_success(key):
            call_count[0] += 1
            if call_count[0] == 1:
                raise ClientError(
                    {"Error": {"Code": "RequestTimeout", "Message": "Timeout"}},
                    "GetObject",
                )
            return original_get(key)

        r2.get_object_stream.side_effect = _timeout_then_success

        inventory = build_inventory(r2)
        output = tmp_path / "backup"

        with caplog.at_level(
            logging.DEBUG, logger="stream_of_worship.admin.services.r2_backup"
        ):
            tracer = BackupTracer()
            result = write_backup(r2, output, inventory, tracer=tracer)

        assert result.object_count == 1

        retry_logs = [r for r in caplog.records if "download_retry" in r.message]
        assert len(retry_logs) == 1
        msg = retry_logs[0].message
        assert "error_code=RequestTimeout" in msg
        assert "attempt=0" in msg
        assert "key=a/file" in msg

    def test_timeout_retries_counter_incremented(self, tmp_path, caplog):
        """_timeout_retries is incremented for timeout-class errors."""
        import logging
        from botocore.exceptions import ClientError

        data = b"hello world"
        etag = hashlib.md5(data).hexdigest()
        objects = [{"key": "a/file", "size": 11, "etag": etag, "data": data}]
        r2 = _make_r2_mock(objects)

        call_count = [0]
        original_get = r2.get_object_stream.side_effect

        def _slow_down_then_success(key):
            call_count[0] += 1
            if call_count[0] == 1:
                raise ClientError(
                    {"Error": {"Code": "SlowDown", "Message": "Slow down"}},
                    "GetObject",
                )
            return original_get(key)

        r2.get_object_stream.side_effect = _slow_down_then_success

        inventory = build_inventory(r2)
        output = tmp_path / "backup"

        with caplog.at_level(
            logging.DEBUG, logger="stream_of_worship.admin.services.r2_backup"
        ):
            tracer = BackupTracer()
            write_backup(r2, output, inventory, tracer=tracer)

        assert tracer._timeout_retries == 1
        # _retries_total is 2: retry_trace increments once for the retry event,
        # and object_download_trace increments once for retries_taken on the
        # successful final attempt.
        assert tracer._retries_total == 2


class TestRangeGetDiagnostic:
    def test_range_get_diagnostic_structure(self):
        """range_get_throughput_diag returns correct structure and values."""
        from stream_of_worship.admin.services.r2_backup import range_get_throughput_diag
        import threading

        r2 = MagicMock()
        r2.bucket = "test-bucket"

        # Simulate a 40 MB object
        content_length = 40 * 1024 * 1024
        r2._client.head_object.return_value = {"ContentLength": content_length}

        # Each range read returns exactly range_size bytes
        def _get_object(**kwargs):
            range_header = kwargs.get("Range", "")
            import re
            m = re.match(r"bytes=(\d+)-(\d+)", range_header)
            assert m is not None
            start = int(m.group(1))
            end = int(m.group(2))
            size = end - start + 1
            return {"Body": io.BytesIO(b"x" * size)}

        r2._client.get_object.side_effect = _get_object

        # Thread-safe mock for time.monotonic that increments by 1.0 each call.
        # We pre-compute a sequence of values and use an index with a lock.
        mono_values = [
            0.0,   # single start
            1.0,   # single end
            1.0,   # multi outer start
            1.0, 2.0,  # range 0 start/end
            1.0, 2.0,  # range 1 start/end
            1.0, 2.0,  # range 2 start/end
            1.0, 2.0,  # range 3 start/end
            2.0,   # multi outer end
        ]
        mono_idx = [0]
        mono_lock = threading.Lock()

        def _mock_monotonic():
            with mono_lock:
                i = mono_idx[0]
                mono_idx[0] = i + 1
                return mono_values[i]

        with patch("time.monotonic", side_effect=_mock_monotonic):
            result = range_get_throughput_diag(r2, "test/key", num_ranges=4)

        assert result["content_length"] == content_length
        assert result["num_ranges"] == 4
        # Single connection: 10 MB in 1s = 10 MB/s
        assert result["single_conn_mbps"] == pytest.approx(10.0, rel=0.01)
        # Multi connection: 40 MB in 1s = 40 MB/s
        assert result["multi_conn_total_mbps"] == pytest.approx(40.0, rel=0.01)
        # Ratio should be ~4.0
        assert result["ratio"] == pytest.approx(4.0, rel=0.01)
        assert len(result["per_range_mbps"]) == 4
        for m in result["per_range_mbps"]:
            assert m == pytest.approx(10.0, rel=0.01)
