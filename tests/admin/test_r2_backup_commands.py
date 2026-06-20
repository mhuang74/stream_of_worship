"""Command-level tests for R2 backup/restore maintenance commands."""

import hashlib
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

    def test_backup_concurrency_flag(self, tmp_path):
        """backup-r2 --concurrency 4 passes concurrency to write_backup."""
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
            patch("stream_of_worship.admin.commands.maintenance.write_backup", wraps=write_backup) as mock_write,
        ):
            result = runner.invoke(
                app,
                ["maintenance", "backup-r2", "--output", str(output), "--concurrency", "4"],
            )

        assert result.exit_code == 0
        mock_write.assert_called_once()
        assert mock_write.call_args.kwargs["concurrency"] == 4


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


def _make_minimal_inventory():
    """Create a minimal inventory for mocking build_inventory."""
    from stream_of_worship.admin.services.r2_backup import Inventory, InventoryObject

    return Inventory(
        objects=[
            InventoryObject(key="test/file", size=5, etag="etag1", last_modified="")
        ],
        started_at="2024-01-01T00:00:00",
        completed_at="2024-01-01T00:00:01",
    )


def _make_fake_config():
    """Create a fake AdminConfig for mocking."""
    from stream_of_worship.admin.config import AdminConfig

    return AdminConfig(
        database_url="postgresql://example",
        r2_endpoint_url="https://r2.example.com",
        r2_bucket="test-bucket",
        r2_region="auto",
    )


def _make_fake_r2():
    """Create a fake R2Client for mocking."""
    from unittest.mock import MagicMock

    r2 = MagicMock()
    r2.bucket = "test-bucket"
    r2.endpoint_url = "https://r2.example.com"
    r2.region = "auto"
    return r2


def test_backup_r2_debug_traces_flag_passes_tracer(monkeypatch, tmp_path):
    """--debug-traces constructs a BackupTracer and passes it to write_backup."""
    from stream_of_worship.admin.services.r2_backup import BackupResult

    captured = {}

    def _fake_write_backup(
        *,
        r2_client,
        output_dir,
        inventory,
        chunk_size_bytes,
        concurrency,
        on_progress,
        tracer,
    ):
        captured["tracer"] = tracer
        captured["concurrency"] = concurrency
        return BackupResult(
            output_dir=output_dir,
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
    result = runner.invoke(
        app,
        [
            "maintenance",
            "backup-r2",
            "--output",
            str(tmp_path / "out"),
            "--debug-traces",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["tracer"] is not None
    assert captured["tracer"].__class__.__name__ == "BackupTracer"


def test_backup_r2_default_no_tracer_passes_none(monkeypatch, tmp_path):
    """Without --debug-traces, tracer is None."""
    from stream_of_worship.admin.services.r2_backup import BackupResult

    captured = {}

    def _fake_write_backup(
        *,
        r2_client,
        output_dir,
        inventory,
        chunk_size_bytes,
        concurrency,
        on_progress,
        tracer,
    ):
        captured["tracer"] = tracer
        return BackupResult(
            output_dir=output_dir,
            object_count=0,
            total_bytes=0,
            chunk_count=0,
            manifest={"version": 4, "objects": []},
        )

    monkeypatch.setattr(
        "stream_of_worship.admin.commands.maintenance.write_backup",
        _fake_write_backup,
    )
    monkeypatch.setattr(
        "stream_of_worship.admin.commands.maintenance.build_inventory",
        lambda r2_client: _make_minimal_inventory(),
    )
    monkeypatch.setattr(
        "stream_of_worship.admin.commands.maintenance._load_clients",
        lambda config_path: (_make_fake_config(), None),
    )
    monkeypatch.setattr(
        "stream_of_worship.admin.commands.maintenance._load_r2",
        lambda config: _make_fake_r2(),
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "maintenance",
            "backup-r2",
            "--output",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["tracer"] is None
