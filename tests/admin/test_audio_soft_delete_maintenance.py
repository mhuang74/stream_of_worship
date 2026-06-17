"""Tests for admin audio soft-delete and maintenance commands."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from stream_of_worship.admin.commands.audio import import_youtube_audio_for_song
from stream_of_worship.admin.config import AdminConfig
from stream_of_worship.admin.db.models import Recording, Song
from stream_of_worship.admin.main import app

runner = CliRunner()


def _recording(hash_prefix: str = "abc123def456", song_id: str = "song_1") -> Recording:
    return Recording(
        content_hash=f"{hash_prefix}{'0' * 52}"[:64],
        hash_prefix=hash_prefix,
        song_id=song_id,
        original_filename="song.mp3",
        file_size_bytes=100,
        imported_at="2024-01-01T00:00:00",
        r2_audio_url=f"s3://bucket/{hash_prefix}/audio.mp3",
    )


class FakeAudioDb:
    def __init__(self, recordings: list[Recording] | None = None):
        self.song = Song(
            id="song_1",
            title="Test Song",
            source_url="https://example.com/song",
            scraped_at="2024-01-01T00:00:00",
        )
        self.recordings = recordings if recordings is not None else [_recording()]
        self.deleted_hashes: list[str] = []
        self.inserted: list[Recording] = []
        self.replacements: list[tuple[str, Recording]] = []

    def get_song(self, song_id: str):
        return self.song if song_id == self.song.id else None

    def list_active_recordings_by_song(self, song_id: str):
        return [recording for recording in self.recordings if recording.song_id == song_id]

    def delete_recording(self, hash_prefix: str):
        self.deleted_hashes.append(hash_prefix)

    def insert_recording(self, recording: Recording):
        self.inserted.append(recording)

    def replace_recording_after_import(self, old_hash_prefix: str, recording: Recording):
        self.replacements.append((old_hash_prefix, recording))
        return 2


def test_audio_delete_soft_deletes_without_r2():
    db = FakeAudioDb()
    config = AdminConfig(database_url="postgresql://example")

    with (
        patch("stream_of_worship.admin.commands.audio.AdminConfig.load", return_value=config),
        patch("stream_of_worship.admin.commands.audio.get_db_client", return_value=db),
        patch("stream_of_worship.admin.commands.audio.R2Client") as r2_cls,
    ):
        result = runner.invoke(app, ["audio", "delete", "song_1", "--yes"])

    assert result.exit_code == 0
    assert db.deleted_hashes == ["abc123def456"]
    r2_cls.assert_not_called()
    assert "R2 assets were preserved" in result.output


def test_audio_delete_refuses_ambiguous_song_recordings():
    db = FakeAudioDb([_recording("abc123def456"), _recording("def123abc456")])
    config = AdminConfig(database_url="postgresql://example")

    with (
        patch("stream_of_worship.admin.commands.audio.AdminConfig.load", return_value=config),
        patch("stream_of_worship.admin.commands.audio.get_db_client", return_value=db),
    ):
        result = runner.invoke(app, ["audio", "delete", "song_1", "--yes"])

    assert result.exit_code == 1
    assert "Multiple active recordings" in result.output
    assert db.deleted_hashes == []


def test_download_force_replaces_after_new_recording_is_uploaded(tmp_path):
    old = _recording("abc123def456")
    db = FakeAudioDb([old])
    audio_path = tmp_path / "download.mp3"
    audio_path.write_bytes(b"new audio")

    downloader = MagicMock()
    downloader.preview_video.return_value = {
        "title": "Test Song",
        "duration": 180,
        "webpage_url": "https://youtu.be/new",
    }
    downloader.download_by_url.return_value = audio_path

    with (
        patch("stream_of_worship.admin.commands.audio.R2Client") as r2_cls,
        patch("stream_of_worship.admin.commands.audio.YouTubeDownloader", return_value=downloader),
        patch("stream_of_worship.admin.commands.audio.compute_file_hash", return_value="f" * 64),
        patch("stream_of_worship.admin.commands.audio.probe_duration", return_value=180.0),
    ):
        r2_cls.return_value.upload_audio.return_value = "s3://bucket/ffffffffffff/audio.mp3"
        imported = import_youtube_audio_for_song(
            song_id="song_1",
            youtube_url="https://youtu.be/new",
            config=AdminConfig(r2_endpoint_url="https://r2.example.com"),
            db_client=db,
            console=SimpleNamespace(print=lambda *args, **kwargs: None),
            force=True,
            skip_video_confirm=True,
        )

    assert imported is not None
    assert db.inserted == []
    assert db.replacements[0][0] == old.hash_prefix
    assert db.replacements[0][1].hash_prefix == "ffffffffffff"


def test_download_force_leaves_old_active_when_download_fails():
    old = _recording("abc123def456")
    db = FakeAudioDb([old])
    downloader = MagicMock()
    downloader.preview_video.return_value = {
        "title": "Test Song",
        "duration": 180,
        "webpage_url": "https://youtu.be/new",
    }
    downloader.download_by_url.side_effect = RuntimeError("download failed")

    with (
        patch("stream_of_worship.admin.commands.audio.R2Client"),
        patch("stream_of_worship.admin.commands.audio.YouTubeDownloader", return_value=downloader),
        pytest.raises(typer.Exit),
    ):
        import_youtube_audio_for_song(
            song_id="song_1",
            youtube_url="https://youtu.be/new",
            config=AdminConfig(r2_endpoint_url="https://r2.example.com"),
            db_client=db,
            console=SimpleNamespace(print=lambda *args, **kwargs: None),
            force=True,
            skip_video_confirm=True,
        )

    assert db.deleted_hashes == []
    assert db.inserted == []
    assert db.replacements == []


class FakeMaintenanceDb:
    def __init__(self):
        deleted = _recording("abc123def456")
        deleted.deleted_at = "2024-01-02T00:00:00"
        self.deleted_recording = deleted
        self.purged_recordings: list[str] = []

    def list_soft_deleted_songs_with_counts(self, limit=None):
        return []

    def list_soft_deleted_recordings_with_counts(self, limit=None):
        return [{"recording": self.deleted_recording, "songset_reference_count": 0}]

    def hard_delete_soft_deleted_recording(self, hash_prefix: str):
        self.purged_recordings.append(hash_prefix)
        return True

    def recording_row_exists(self, hash_prefix: str):
        return hash_prefix == "def123abc456"

    def count_recording_songset_references(self, hash_prefix: str):
        return 0


def test_maintenance_list_soft_deletes_ids():
    db = FakeMaintenanceDb()

    with (
        patch(
            "stream_of_worship.admin.commands.maintenance.AdminConfig.load",
            return_value=AdminConfig(),
        ),
        patch("stream_of_worship.admin.commands.maintenance.get_db_client", return_value=db),
    ):
        result = runner.invoke(app, ["maintenance", "list-soft-deletes", "--format", "ids"])

    assert result.exit_code == 0
    assert "recording:abc123def456" in result.output


def test_maintenance_purge_soft_deletes_confirm_deletes_r2_then_db():
    db = FakeMaintenanceDb()
    r2 = MagicMock()

    with (
        patch(
            "stream_of_worship.admin.commands.maintenance.AdminConfig.load",
            return_value=AdminConfig(),
        ),
        patch("stream_of_worship.admin.commands.maintenance.get_db_client", return_value=db),
        patch("stream_of_worship.admin.commands.maintenance.R2Client", return_value=r2) as r2_cls,
    ):
        r2_cls.validate_recording_hash_prefix.side_effect = lambda prefix: prefix
        result = runner.invoke(
            app,
            [
                "maintenance",
                "purge-soft-deletes",
                "--entity",
                "recordings",
                "--all",
                "--confirm",
            ],
        )

    assert result.exit_code == 0
    r2.delete_prefix.assert_called_once_with("abc123def456")
    assert db.purged_recordings == ["abc123def456"]


def test_maintenance_purge_r2_waste_refuses_existing_rows():
    db = FakeMaintenanceDb()
    r2 = MagicMock()
    r2.scan_recording_prefixes.return_value = [
        SimpleNamespace(
            prefix="def123abc456",
            object_count=1,
            total_bytes=100,
            last_modified=datetime(2024, 1, 1).isoformat(),
        )
    ]
    r2.list_prefix.return_value = SimpleNamespace(
        prefix="def123abc456",
        object_count=1,
        total_bytes=100,
        last_modified=datetime(2024, 1, 1).isoformat(),
    )

    with (
        patch(
            "stream_of_worship.admin.commands.maintenance.AdminConfig.load",
            return_value=AdminConfig(),
        ),
        patch("stream_of_worship.admin.commands.maintenance.get_db_client", return_value=db),
        patch("stream_of_worship.admin.commands.maintenance.R2Client", return_value=r2) as r2_cls,
    ):
        r2_cls.validate_recording_hash_prefix.side_effect = lambda prefix: prefix
        result = runner.invoke(
            app,
            [
                "maintenance",
                "purge-r2-waste",
                "--prefix",
                "def123abc456",
                "--confirm",
                "--format",
                "json",
            ],
        )

    assert result.exit_code == 0
    assert "recording-row-exists" in result.output
    r2.delete_prefix.assert_not_called()
