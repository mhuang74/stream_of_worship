"""Command-level tests for R2 backup/restore maintenance commands."""

import io
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from stream_of_worship.admin.commands.maintenance import _bytes_to_mb, _format_datetime
from stream_of_worship.admin.config import AdminConfig
from stream_of_worship.admin.main import app
from stream_of_worship.admin.services.r2_backup import (
    MANIFEST_VERSION,
    build_inventory,
    write_backup,
)

runner = CliRunner()


def _make_r2_mock(objects: list[dict] | None = None) -> MagicMock:
    """Create a mock R2Client for command tests."""
    objects = objects or []
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


def _create_valid_backup(tmp_path, objects: list[dict] | None = None) -> Path:
    """Helper to create a valid backup directory."""
    objects = objects or []
    r2 = _make_r2_mock(objects)
    inventory = build_inventory(r2)
    output = tmp_path / "backup"
    write_backup(r2, output, inventory)
    return output


class TestBackupR2Command:
    def test_backup_creates_new_backup_directory(self, tmp_path):
        """backup-r2 creates a new backup directory with manifest and chunks."""
        objects = [
            {"key": "a/audio.mp3", "size": 11, "etag": "etag1", "data": b"hello world"},
        ]
        r2 = _make_r2_mock(objects)
        config = AdminConfig(database_url="postgresql://example", r2_endpoint_url="https://r2.example.com")
        output = tmp_path / "my_backup"

        with (
            patch("stream_of_worship.admin.commands.maintenance.AdminConfig.load", return_value=config),
            patch("stream_of_worship.admin.commands.maintenance.get_db_client", return_value=MagicMock()),
            patch("stream_of_worship.admin.commands.maintenance.R2Client", return_value=r2),
        ):
            result = runner.invoke(
                app,
                ["maintenance", "backup-r2", "--output", str(output)],
            )

        assert result.exit_code == 0
        assert output.exists()
        assert (output / "manifest.json").exists()
        assert (output / "chunk-000000.tar").exists()

    def test_backup_refuses_existing_output(self, tmp_path):
        """backup-r2 refuses if --output already exists."""
        output = tmp_path / "existing"
        output.mkdir()

        r2 = _make_r2_mock([])
        config = AdminConfig(database_url="postgresql://example", r2_endpoint_url="https://r2.example.com")

        with (
            patch("stream_of_worship.admin.commands.maintenance.AdminConfig.load", return_value=config),
            patch("stream_of_worship.admin.commands.maintenance.get_db_client", return_value=MagicMock()),
            patch("stream_of_worship.admin.commands.maintenance.R2Client", return_value=r2),
        ):
            result = runner.invoke(
                app,
                ["maintenance", "backup-r2", "--output", str(output)],
            )

        assert result.exit_code == 1
        assert "already exists" in result.output

    def test_backup_refuses_existing_partial(self, tmp_path):
        """backup-r2 refuses if <output>.part already exists."""
        output = tmp_path / "backup"
        partial = output.with_suffix(output.suffix + ".part")
        partial.mkdir()

        r2 = _make_r2_mock([])
        config = AdminConfig(database_url="postgresql://example", r2_endpoint_url="https://r2.example.com")

        with (
            patch("stream_of_worship.admin.commands.maintenance.AdminConfig.load", return_value=config),
            patch("stream_of_worship.admin.commands.maintenance.get_db_client", return_value=MagicMock()),
            patch("stream_of_worship.admin.commands.maintenance.R2Client", return_value=r2),
        ):
            result = runner.invoke(
                app,
                ["maintenance", "backup-r2", "--output", str(output)],
            )

        assert result.exit_code == 1
        assert "Partial output directory already exists" in result.output

    def test_backup_chunk_size_parses_gib(self, tmp_path):
        """backup-r2 --chunk-size 10GiB parses successfully."""
        objects = [
            {"key": "a/file", "size": 5, "etag": "etag1", "data": b"hello"},
        ]
        r2 = _make_r2_mock(objects)
        config = AdminConfig(database_url="postgresql://example", r2_endpoint_url="https://r2.example.com")
        output = tmp_path / "backup"

        with (
            patch("stream_of_worship.admin.commands.maintenance.AdminConfig.load", return_value=config),
            patch("stream_of_worship.admin.commands.maintenance.get_db_client", return_value=MagicMock()),
            patch("stream_of_worship.admin.commands.maintenance.R2Client", return_value=r2),
        ):
            result = runner.invoke(
                app,
                ["maintenance", "backup-r2", "--output", str(output), "--chunk-size", "10GiB"],
            )

        assert result.exit_code == 0

    def test_backup_json_output_is_parseable(self, tmp_path):
        """backup-r2 --format json outputs parseable JSON."""
        objects = [
            {"key": "a/file", "size": 5, "etag": "etag1", "data": b"hello"},
        ]
        r2 = _make_r2_mock(objects)
        config = AdminConfig(database_url="postgresql://example", r2_endpoint_url="https://r2.example.com")
        output = tmp_path / "backup"

        with (
            patch("stream_of_worship.admin.commands.maintenance.AdminConfig.load", return_value=config),
            patch("stream_of_worship.admin.commands.maintenance.get_db_client", return_value=MagicMock()),
            patch("stream_of_worship.admin.commands.maintenance.R2Client", return_value=r2),
        ):
            result = runner.invoke(
                app,
                ["maintenance", "backup-r2", "--output", str(output), "--format", "json"],
            )

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["object_count"] == 1
        assert data["total_mb"] == 0   # 5 bytes rounds to 0 MB
        assert data["chunk_count"] == 1
        assert data["output_dir"] == str(output)
        # total_bytes no longer present in JSON output
        assert "total_bytes" not in data


class TestVerifyR2BackupCommand:
    def test_verify_succeeds_on_valid_backup(self, tmp_path):
        """verify-r2-backup succeeds on a valid backup."""
        backup_dir = _create_valid_backup(tmp_path, [
            {"key": "a/file", "size": 5, "etag": "etag1", "data": b"hello"},
        ])

        result = runner.invoke(
            app,
            ["maintenance", "verify-r2-backup", "--dir", str(backup_dir)],
        )

        assert result.exit_code == 0
        assert "Verification OK" in result.output

    def test_verify_fails_on_bad_backup(self, tmp_path):
        """verify-r2-backup fails on a corrupt backup."""
        backup_dir = _create_valid_backup(tmp_path, [
            {"key": "a/file", "size": 5, "etag": "etag1", "data": b"hello"},
        ])
        # Corrupt the tar
        (backup_dir / "chunk-000000.tar").write_bytes(b"corrupt")

        result = runner.invoke(
            app,
            ["maintenance", "verify-r2-backup", "--dir", str(backup_dir)],
        )

        assert result.exit_code == 1

    def test_verify_json_output(self, tmp_path):
        """verify-r2-backup --format json outputs parseable JSON."""
        backup_dir = _create_valid_backup(tmp_path, [
            {"key": "a/file", "size": 5, "etag": "etag1", "data": b"hello"},
        ])

        result = runner.invoke(
            app,
            ["maintenance", "verify-r2-backup", "--dir", str(backup_dir), "--format", "json"],
        )

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["ok"] is True
        assert data["object_count"] == 1
        assert "total_mb" in data
        assert "total_bytes" not in data

    def test_verify_no_config_required(self, tmp_path):
        """verify-r2-backup does not require config or R2 credentials."""
        backup_dir = _create_valid_backup(tmp_path, [])

        # No config patching - should work without any config
        result = runner.invoke(
            app,
            ["maintenance", "verify-r2-backup", "--dir", str(backup_dir)],
        )

        assert result.exit_code == 0


class TestRestoreR2Command:
    def test_restore_dry_run_shows_plan(self, tmp_path):
        """restore-r2 dry-run shows create/conflict actions."""
        backup_dir = _create_valid_backup(tmp_path, [
            {"key": "a/file", "size": 5, "etag": "etag1", "data": b"hello"},
        ])

        r2 = MagicMock()
        r2.head_object.return_value = None  # target doesn't exist -> create

        config = AdminConfig(database_url="postgresql://example", r2_endpoint_url="https://r2.example.com")

        with (
            patch("stream_of_worship.admin.commands.maintenance.AdminConfig.load", return_value=config),
            patch("stream_of_worship.admin.commands.maintenance.get_db_client", return_value=MagicMock()),
            patch("stream_of_worship.admin.commands.maintenance.R2Client", return_value=r2),
        ):
            result = runner.invoke(
                app,
                ["maintenance", "restore-r2", "--dir", str(backup_dir)],
            )

        assert result.exit_code == 0
        assert "create" in result.output
        assert "Dry run" in result.output
        r2.upload_fileobj.assert_not_called()

    def test_restore_confirm_aborts_on_conflict(self, tmp_path):
        """restore-r2 --confirm aborts on conflict without upload."""
        backup_dir = _create_valid_backup(tmp_path, [
            {"key": "a/file", "size": 5, "etag": "etag1", "data": b"hello"},
        ])

        r2 = MagicMock()
        r2.head_object.return_value = {"size": 5, "etag": "different"}  # conflict

        config = AdminConfig(database_url="postgresql://example", r2_endpoint_url="https://r2.example.com")

        with (
            patch("stream_of_worship.admin.commands.maintenance.AdminConfig.load", return_value=config),
            patch("stream_of_worship.admin.commands.maintenance.get_db_client", return_value=MagicMock()),
            patch("stream_of_worship.admin.commands.maintenance.R2Client", return_value=r2),
        ):
            result = runner.invoke(
                app,
                ["maintenance", "restore-r2", "--dir", str(backup_dir), "--confirm"],
            )

        assert result.exit_code == 1
        assert "conflict" in result.output.lower()
        r2.upload_fileobj.assert_not_called()

    def test_restore_confirm_skip_existing(self, tmp_path):
        """restore-r2 --confirm --skip-existing skips existing objects."""
        backup_dir = _create_valid_backup(tmp_path, [
            {"key": "a/file", "size": 5, "etag": "etag1", "data": b"hello"},
        ])

        r2 = MagicMock()
        r2.head_object.return_value = {"size": 5, "etag": "different"}

        config = AdminConfig(database_url="postgresql://example", r2_endpoint_url="https://r2.example.com")

        with (
            patch("stream_of_worship.admin.commands.maintenance.AdminConfig.load", return_value=config),
            patch("stream_of_worship.admin.commands.maintenance.get_db_client", return_value=MagicMock()),
            patch("stream_of_worship.admin.commands.maintenance.R2Client", return_value=r2),
        ):
            result = runner.invoke(
                app,
                [
                    "maintenance", "restore-r2", "--dir", str(backup_dir),
                    "--skip-existing", "--confirm",
                ],
            )

        assert result.exit_code == 0
        r2.upload_fileobj.assert_not_called()

    def test_restore_confirm_overwrite_existing(self, tmp_path):
        """restore-r2 --confirm --overwrite-existing uploads over existing."""
        backup_dir = _create_valid_backup(tmp_path, [
            {"key": "a/file", "size": 5, "etag": "etag1", "data": b"hello"},
        ])

        r2 = MagicMock()
        r2.head_object.return_value = {"size": 5, "etag": "different"}

        config = AdminConfig(database_url="postgresql://example", r2_endpoint_url="https://r2.example.com")

        with (
            patch("stream_of_worship.admin.commands.maintenance.AdminConfig.load", return_value=config),
            patch("stream_of_worship.admin.commands.maintenance.get_db_client", return_value=MagicMock()),
            patch("stream_of_worship.admin.commands.maintenance.R2Client", return_value=r2),
        ):
            result = runner.invoke(
                app,
                [
                    "maintenance", "restore-r2", "--dir", str(backup_dir),
                    "--overwrite-existing", "--confirm",
                ],
            )

        assert result.exit_code == 0
        r2.upload_fileobj.assert_called_once()

    def test_restore_mutually_exclusive_flags(self, tmp_path):
        """restore-r2 --skip-existing --overwrite-existing fails."""
        backup_dir = _create_valid_backup(tmp_path, [
            {"key": "a/file", "size": 5, "etag": "etag1", "data": b"hello"},
        ])

        r2 = MagicMock()
        config = AdminConfig(database_url="postgresql://example", r2_endpoint_url="https://r2.example.com")

        with (
            patch("stream_of_worship.admin.commands.maintenance.AdminConfig.load", return_value=config),
            patch("stream_of_worship.admin.commands.maintenance.get_db_client", return_value=MagicMock()),
            patch("stream_of_worship.admin.commands.maintenance.R2Client", return_value=r2),
        ):
            result = runner.invoke(
                app,
                [
                    "maintenance", "restore-r2", "--dir", str(backup_dir),
                    "--skip-existing", "--overwrite-existing",
                ],
            )

        assert result.exit_code == 1
        assert "mutually exclusive" in result.output

    def test_restore_json_output(self, tmp_path):
        """restore-r2 --format json outputs parseable JSON."""
        backup_dir = _create_valid_backup(tmp_path, [
            {"key": "a/file", "size": 5, "etag": "etag1", "data": b"hello"},
        ])

        r2 = MagicMock()
        r2.head_object.return_value = None

        config = AdminConfig(database_url="postgresql://example", r2_endpoint_url="https://r2.example.com")

        with (
            patch("stream_of_worship.admin.commands.maintenance.AdminConfig.load", return_value=config),
            patch("stream_of_worship.admin.commands.maintenance.get_db_client", return_value=MagicMock()),
            patch("stream_of_worship.admin.commands.maintenance.R2Client", return_value=r2),
        ):
            result = runner.invoke(
                app,
                ["maintenance", "restore-r2", "--dir", str(backup_dir), "--format", "json"],
            )

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["plan"][0]["action"] == "create"
        assert "size_mb" in data["plan"][0]
        assert "size" not in data["plan"][0]

    def test_restore_prefix_filtering(self, tmp_path):
        """restore-r2 --prefix filters objects."""
        backup_dir = _create_valid_backup(tmp_path, [
            {"key": "prefix_a/file", "size": 5, "etag": "etag1", "data": b"hello"},
            {"key": "prefix_b/file", "size": 5, "etag": "etag2", "data": b"world"},
        ])

        r2 = MagicMock()
        r2.head_object.return_value = None

        config = AdminConfig(database_url="postgresql://example", r2_endpoint_url="https://r2.example.com")

        with (
            patch("stream_of_worship.admin.commands.maintenance.AdminConfig.load", return_value=config),
            patch("stream_of_worship.admin.commands.maintenance.get_db_client", return_value=MagicMock()),
            patch("stream_of_worship.admin.commands.maintenance.R2Client", return_value=r2),
        ):
            result = runner.invoke(
                app,
                [
                    "maintenance", "restore-r2", "--dir", str(backup_dir),
                    "--prefix", "prefix_a", "--format", "json",
                ],
            )

        assert result.exit_code == 0
        json_start = result.output.index("{")
        data = json.loads(result.output[json_start:])
        assert len(data["plan"]) == 1
        assert data["plan"][0]["key"] == "prefix_a/file"
