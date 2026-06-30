"""Tests for the v4 audio batch command (step flags, force scoping, resume).

These tests exercise the CLI argument-validation paths that run before any
database or R2 access. They use the Typer CliRunner against the real app.
"""

from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from stream_of_worship.admin.main import app

runner = CliRunner()

WIDE_ENV = {"COLUMNS": "200"}


class TestBatchStepFlags:
    """Step flag selection and validation."""

    def test_no_step_flags_exits_1(self, tmp_path):
        """No step flags and no --all-steps exits 1 with usage."""
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\npath = "/nonexistent/db.sqlite"\n')
        result = runner.invoke(
            app,
            ["audio", "batch", "--config", str(config_path)],
            env=WIDE_ENV,
        )
        assert result.exit_code == 1
        assert "No step flags selected" in result.output

    def test_invalid_format_exits_1(self, tmp_path):
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\npath = "/nonexistent/db.sqlite"\n')
        result = runner.invoke(
            app,
            ["audio", "batch", "--analyze", "--format", "xml", "--config", str(config_path)],
            env=WIDE_ENV,
        )
        assert result.exit_code == 1
        assert "Invalid format" in result.output

    def test_invalid_analysis_tier_exits_1(self, tmp_path):
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\npath = "/nonexistent/db.sqlite"\n')
        result = runner.invoke(
            app,
            ["audio", "batch", "--analyze", "--analysis-tier", "medium", "--config", str(config_path)],
            env=WIDE_ENV,
        )
        assert result.exit_code == 1
        assert "Invalid analysis tier" in result.output

    def test_invalid_analysis_status_exits_1(self, tmp_path):
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\npath = "/nonexistent/db.sqlite"\n')
        result = runner.invoke(
            app,
            ["audio", "batch", "--analyze", "--analysis-status", "bogus", "--config", str(config_path)],
            env=WIDE_ENV,
        )
        assert result.exit_code == 1
        assert "Invalid analysis status" in result.output

    def test_partial_is_valid_analysis_status(self, tmp_path):
        """'partial' is accepted as a valid analysis status filter."""
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\npath = "/nonexistent/db.sqlite"\n')
        result = runner.invoke(
            app,
            ["audio", "batch", "--analyze", "--analysis-status", "partial", "--config", str(config_path)],
            env=WIDE_ENV,
        )
        # Should NOT fail on validation; it will fail later on DB load.
        assert "Invalid analysis status" not in result.output


class TestForceScoping:
    """--force validation per the v4 spec."""

    def test_force_all_steps_exits_1(self, tmp_path):
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\npath = "/nonexistent/db.sqlite"\n')
        result = runner.invoke(
            app,
            ["audio", "batch", "--all-steps", "--force", "--config", str(config_path)],
            env=WIDE_ENV,
        )
        assert result.exit_code == 1
        assert "--force with --all-steps" in result.output

    def test_force_no_step_flags_exits_1(self, tmp_path):
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\npath = "/nonexistent/db.sqlite"\n')
        result = runner.invoke(
            app,
            ["audio", "batch", "--force", "--config", str(config_path)],
            env=WIDE_ENV,
        )
        assert result.exit_code == 1

    def test_force_download_exits_1_with_hint(self, tmp_path):
        """--force --download is rejected with the two-step purge hint."""
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\npath = "/nonexistent/db.sqlite"\n')
        result = runner.invoke(
            app,
            ["audio", "batch", "--download", "--force", "--config", str(config_path)],
            env=WIDE_ENV,
        )
        assert result.exit_code == 1
        assert "--force --download is not supported" in result.output
        assert "purge-soft-deletes" in result.output

    def test_force_multiple_steps_exits_1(self, tmp_path):
        """--force with two step flags exits 1."""
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\npath = "/nonexistent/db.sqlite"\n')
        result = runner.invoke(
            app,
            ["audio", "batch", "--lrc", "--analyze", "--force", "--config", str(config_path)],
            env=WIDE_ENV,
        )
        assert result.exit_code == 1
        assert "exactly one step flag" in result.output


class TestResumeMutualExclusivity:
    """--resume is mutually exclusive with all selection flags and --force."""

    def test_resume_with_force_exits_1(self, tmp_path):
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\npath = "/nonexistent/db.sqlite"\n')
        result = runner.invoke(
            app,
            ["audio", "batch", "--resume", "/tmp/manifest.json", "--force", "--config", str(config_path)],
            env=WIDE_ENV,
        )
        assert result.exit_code == 1
        assert "--resume is mutually exclusive" in result.output

    def test_resume_with_analyze_exits_1(self, tmp_path):
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\npath = "/nonexistent/db.sqlite"\n')
        result = runner.invoke(
            app,
            ["audio", "batch", "--resume", "/tmp/manifest.json", "--analyze", "--config", str(config_path)],
            env=WIDE_ENV,
        )
        assert result.exit_code == 1
        assert "--resume is mutually exclusive" in result.output

    def test_resume_with_album_exits_1(self, tmp_path):
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\npath = "/nonexistent/db.sqlite"\n')
        result = runner.invoke(
            app,
            ["audio", "batch", "--resume", "/tmp/manifest.json", "--album", "foo", "--config", str(config_path)],
            env=WIDE_ENV,
        )
        assert result.exit_code == 1
        assert "--resume is mutually exclusive" in result.output


class TestRecordingModelAnalysis:
    """Tests for the Recording model analysis properties."""

    def test_has_analysis_partial_is_true(self):
        from stream_of_worship.admin.db.models import Recording

        recording = Recording(
            content_hash="abc",
            hash_prefix="abc",
            original_filename="test.mp3",
            file_size_bytes=100,
            imported_at="2024-01-01T00:00:00",
            analysis_status="partial",
        )
        assert recording.has_analysis is True
        assert recording.has_fast_analysis is True
        assert recording.has_full_analysis is False

    def test_has_analysis_completed_is_true(self):
        from stream_of_worship.admin.db.models import Recording

        recording = Recording(
            content_hash="abc",
            hash_prefix="abc",
            original_filename="test.mp3",
            file_size_bytes=100,
            imported_at="2024-01-01T00:00:00",
            analysis_status="completed",
        )
        assert recording.has_analysis is True
        assert recording.has_fast_analysis is True
        assert recording.has_full_analysis is True

    def test_has_analysis_pending_is_false(self):
        from stream_of_worship.admin.db.models import Recording

        recording = Recording(
            content_hash="abc",
            hash_prefix="abc",
            original_filename="test.mp3",
            file_size_bytes=100,
            imported_at="2024-01-01T00:00:00",
            analysis_status="pending",
        )
        assert recording.has_analysis is False
        assert recording.has_fast_analysis is False
        assert recording.has_full_analysis is False


class TestManifestHelpers:
    """Tests for manifest writer/loader helpers."""

    def test_write_and_load_manifest(self, tmp_path):
        from stream_of_worship.admin.commands.audio import _write_manifest, _load_manifest

        batch_id = "2026-06-30T0215_batch"
        results = {"song_1": {"analyze": "completed"}}
        entries = [
            {
                "song_id": "song_1",
                "hash_prefix": "feedface",
                "step": "analyze",
                "tier": "fast",
                "job_id": "job_a1b2",
                "status": "completed",
                "attempts": 1,
                "previous_job_id": None,
                "error_class": None,
                "error_message": None,
                "submitted_at": "2026-06-30T02:15:04Z",
                "completed_at": "2026-06-30T02:20:00Z",
            }
        ]
        path = _write_manifest(
            batch_id,
            results,
            tmp_path,
            ["analyze"],
            "fast",
            120,
            "2026-06-30T02:15:00Z",
            entries,
        )
        assert path is not None
        assert path.exists()

        loaded = _load_manifest(path)
        assert loaded is not None
        assert loaded["batch_id"] == batch_id
        assert loaded["selected_steps"] == ["analyze"]
        assert loaded["analysis_tier"] == "fast"
        assert len(loaded["songs"]) == 1
        assert loaded["songs"][0]["song_id"] == "song_1"
        assert loaded["songs"][0]["status"] == "completed"

    def test_manifest_dir_env_override(self, tmp_path, monkeypatch):
        from stream_of_worship.admin.commands.audio import _get_manifest_dir

        custom = tmp_path / "custom_manifests"
        monkeypatch.setenv("SOW_BATCH_MANIFEST_DIR", str(custom))
        assert _get_manifest_dir() == custom

    def test_manifest_dir_default(self, monkeypatch):
        from stream_of_worship.admin.commands.audio import _get_manifest_dir

        monkeypatch.delenv("SOW_BATCH_MANIFEST_DIR", raising=False)
        result = _get_manifest_dir()
        assert "sow-admin" in str(result)
        assert "batch" in str(result)
