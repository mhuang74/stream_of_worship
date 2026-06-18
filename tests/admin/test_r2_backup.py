"""Tests for R2 backup and restore service."""

import hashlib
import io
import json
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from stream_of_worship.admin.services.r2_backup import (
    DEFAULT_CHUNK_SIZE_BYTES,
    MANIFEST_VERSION,
    MIN_CHUNK_SIZE_BYTES,
    BackupError,
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
        expected = hashlib.sha256(data).hexdigest()
        assert reader.sha256_hex == expected

    def test_partial_reads(self):
        data = b"hello world"
        source = io.BytesIO(data)
        reader = HashingReader(source)
        chunk1 = reader.read(5)
        chunk2 = reader.read(6)
        assert chunk1 == b"hello"
        assert chunk2 == b" world"
        assert reader.bytes_read == len(data)
        expected = hashlib.sha256(data).hexdigest()
        assert reader.sha256_hex == expected

    def test_empty_read(self):
        source = io.BytesIO(b"")
        reader = HashingReader(source)
        result = reader.read()
        assert result == b""
        assert reader.bytes_read == 0
        expected = hashlib.sha256(b"").hexdigest()
        assert reader.sha256_hex == expected

    def test_close(self):
        source = MagicMock()
        reader = HashingReader(source)
        reader.close()
        source.close.assert_called_once()


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
        assert MANIFEST_VERSION == 3

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

    obj_list = [
        {"key": o["key"], "size": o["size"], "etag": o["etag"], "last_modified": o.get("last_modified", "")}
        for o in objects
    ]
    r2.iter_objects.return_value = iter(obj_list)

    obj_map = {o["key"]: o for o in objects}

    def _get_object_stream(key):
        o = obj_map[key]
        body = io.BytesIO(o["data"])
        return {
            "body": body,
            "content_length": len(o["data"]),
            "etag": o["etag"],
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
        return {
            "size": len(o["data"]),
            "etag": o["etag"],
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
        assert manifest["version"] == 3
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
                "etag": "etag1",
                "last_modified": "",
                "content_type": None,
                "cache_control": None,
                "content_disposition": None,
                "content_encoding": None,
                "metadata": {},
            }

        r2.get_object_stream.side_effect = _get_object_stream
        r2.head_object.return_value = {
            "size": 100,
            "etag": "etag1",
            "last_modified": "",
            "content_type": None,
            "cache_control": None,
            "content_disposition": None,
            "content_encoding": None,
            "metadata": {},
        }

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
        objects = [{"key": "a/file", "size": 11, "etag": "etag1", "data": data}]
        r2 = _make_r2_mock(objects)

        # First head returns different etag, second returns matching
        call_count = [0]
        original_head = r2.head_object.side_effect

        def _flaky_head(key):
            call_count[0] += 1
            if call_count[0] == 1:
                return {
                    "size": 11,
                    "etag": "changed_etag",
                    "last_modified": "",
                    "content_type": None,
                    "cache_control": None,
                    "content_disposition": None,
                    "content_encoding": None,
                    "metadata": {},
                }
            return original_head(key)

        r2.head_object.side_effect = _flaky_head

        inventory = build_inventory(r2)
        output = tmp_path / "backup"
        result = write_backup(r2, output, inventory)

        assert result.object_count == 1
        assert output.exists()

    def test_backup_changed_object_exceeds_retries_fails(self, tmp_path):
        """Object that keeps changing fails after retry budget."""
        data = b"hello world"
        objects = [{"key": "a/file", "size": 11, "etag": "etag1", "data": data}]
        r2 = _make_r2_mock(objects)

        # head always returns different etag
        r2.head_object.side_effect = lambda key: {
            "size": 11,
            "etag": "always_different",
            "last_modified": "",
            "content_type": None,
            "cache_control": None,
            "content_disposition": None,
            "content_encoding": None,
            "metadata": {},
        }

        inventory = build_inventory(r2)
        output = tmp_path / "backup"

        with pytest.raises(BackupError, match="ETag changed"):
            write_backup(r2, output, inventory)

        assert not output.exists()

    def test_backup_deleted_object_during_backup_fails(self, tmp_path):
        """Object that disappears during backup fails and cleans up."""
        data = b"hello world"
        objects = [{"key": "a/file", "size": 11, "etag": "etag1", "data": data}]
        r2 = _make_r2_mock(objects)

        # head returns None (object deleted) - need to reset side_effect
        r2.head_object.side_effect = None
        r2.head_object.return_value = None

        inventory = build_inventory(r2)
        output = tmp_path / "backup"

        with pytest.raises(BackupError, match="disappeared"):
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
        result = write_backup(r2, output, inventory)

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

        with pytest.raises(RuntimeError):
            write_backup(r2, output, inventory)

        assert not output.exists()
        partial = output.with_suffix(output.suffix + ".part")
        assert not partial.exists()


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
            "version": 3,
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
