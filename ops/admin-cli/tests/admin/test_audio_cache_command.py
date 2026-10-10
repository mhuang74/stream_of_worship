"""Tests for the `sow-admin audio cache` batch filters and stem behavior."""

from pathlib import Path
from unittest.mock import MagicMock

from typer.testing import CliRunner

from stream_of_worship.admin.commands import audio as audio_commands
from stream_of_worship.admin.config import AdminConfig
from stream_of_worship.admin.db.models import Recording
from stream_of_worship.admin.main import app

runner = CliRunner()


def _fake_config() -> AdminConfig:
    return AdminConfig(
        database_url="postgresql://example.invalid/sow",
        r2_bucket="test-bucket",
        r2_endpoint_url="https://test.r2.dev",
        r2_region="auto",
    )


class FakeDbClient:
    """Fake db client with cache-target seeding."""

    def __init__(self, targets=None, recordings=None, songs=None):
        # targets: list of (hash_prefix, song_id, title)
        self.targets = list(targets or [])
        self.recordings = dict(recordings or {})
        self.songs = dict(songs or {})
        self.target_calls: list[dict] = []

    def list_cache_target_recordings(
        self, visibility=None, rating_stored=None, reason=None, limit=None
    ):
        self.target_calls.append(
            {
                "visibility": visibility,
                "rating_stored": rating_stored,
                "reason": reason,
                "limit": limit,
            }
        )
        return list(self.targets)

    def get_recording_by_song_id(self, song_id):
        return self.recordings.get(song_id)

    def get_song(self, song_id, include_deleted=False):
        return self.songs.get(song_id)


def _recording(hash_prefix: str, song_id: str) -> Recording:
    return Recording(
        content_hash=hash_prefix + "f" * 52,
        hash_prefix=hash_prefix,
        original_filename="test.mp3",
        file_size_bytes=1000,
        imported_at="2026-01-01T00:00:00",
        song_id=song_id,
    )


def _make_r2_patch(monkeypatch, tmp_cache: Path):
    """Patch R2Client in audio_commands so instances report dry stems existing."""
    real_r2 = audio_commands.R2Client

    class FakeR2:
        def __init__(self, bucket, endpoint_url, region):
            pass

        def file_exists(self, s3_key: str) -> bool:
            return s3_key.endswith("/stems/vocals_dry.flac")

        def download_file(self, s3_key, dest_path):
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_bytes(b"x")
            return dest_path

    monkeypatch.setattr(audio_commands, "R2Client", FakeR2)
    audio_commands_r2_backup = real_r2  # keep reference alive
    return audio_commands_r2_backup


def test_cache_dry_run_lists_targets(monkeypatch):
    targets = [
        ("hash111111", "song_a", "Song A"),
        ("hash222222", "song_b", "Song B"),
    ]
    db = FakeDbClient(targets=targets)

    monkeypatch.setattr(
        audio_commands,
        "AdminConfig",
        type("C", (), {"load": staticmethod(lambda path=None: _fake_config())}),
    )
    monkeypatch.setattr(audio_commands, "get_db_client", lambda config: db)

    result = runner.invoke(
        app, ["audio", "cache", "--visibility", "published", "--rating", "poor", "--dry-run"]
    )

    assert result.exit_code == 0, result.output
    assert "Song A" in result.output
    assert "Song B" in result.output
    assert "hash111111" in result.output
    assert "hash222222" in result.output
    # rating must be mapped to the stored value happy|sad
    assert db.target_calls[0]["rating_stored"] == "sad"
    assert db.target_calls[0]["visibility"] == "published"


def test_cache_batch_downloads_clean_vocals(monkeypatch, tmp_path):
    targets = [("hash111111", "song_a", "Song A")]
    db = FakeDbClient(targets=targets, songs={"song_a": None})

    monkeypatch.setattr(
        audio_commands,
        "AdminConfig",
        type("C", (), {"load": staticmethod(lambda path=None: _fake_config())}),
    )
    monkeypatch.setattr(audio_commands, "get_db_client", lambda config: db)

    # Route cache dir to tmp via SOW_CACHE_DIR (get_cache_dir honors it)
    monkeypatch.setenv("SOW_CACHE_DIR", str(tmp_path))

    class FakeR2:
        def __init__(self, bucket, endpoint_url, region):
            pass

        def file_exists(self, s3_key: str) -> bool:
            return s3_key.endswith("/stems/vocals_dry.flac")

        def download_file(self, s3_key, dest_path):
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_bytes(b"x")
            return dest_path

    monkeypatch.setattr(audio_commands, "R2Client", FakeR2)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)

    result = runner.invoke(app, ["audio", "cache", "--visibility", "review", "--rating", "poor"])

    assert result.exit_code == 0, result.output
    # alias must exist within the SOW_CACHE_DIR-resolved admin root
    alias = tmp_path / "hash111111" / "stems" / "clean_vocals.flac"
    assert alias.exists(), result.output
    assert alias.read_bytes() == b"x"
    assert "source: vocals_dry" in result.output


def test_cache_rejects_song_id_with_filters(monkeypatch):
    db = FakeDbClient(targets=[("hash111111", "song_a", "Song A")])

    monkeypatch.setattr(
        audio_commands,
        "AdminConfig",
        type("C", (), {"load": staticmethod(lambda path=None: _fake_config())}),
    )
    monkeypatch.setattr(audio_commands, "get_db_client", lambda config: db)

    result = runner.invoke(app, ["audio", "cache", "song_a", "--visibility", "published"])

    assert result.exit_code == 1
    assert "cannot be combined" in result.output


def test_cache_empty_filters_result(monkeypatch, tmp_path):
    db = FakeDbClient(targets=[])

    monkeypatch.setattr(
        audio_commands,
        "AdminConfig",
        type("C", (), {"load": staticmethod(lambda path=None: _fake_config())}),
    )
    monkeypatch.setattr(audio_commands, "get_db_client", lambda config: db)
    monkeypatch.setenv("SOW_CACHE_DIR", str(tmp_path))

    result = runner.invoke(app, ["audio", "cache", "--visibility", "published", "--rating", "poor"])

    assert result.exit_code == 0
    assert "No recordings matched filters." in result.output
