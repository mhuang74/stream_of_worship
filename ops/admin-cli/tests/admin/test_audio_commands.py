"""Tests for audio CLI commands."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.db.models import Recording, Song
from stream_of_worship.admin.main import app
from stream_of_worship.admin.services.analysis import AnalysisServiceError, JobInfo
from stream_of_worship.db.postgres_schema import ALL_SCHEMA_STATEMENTS

runner = CliRunner()

WIDE_ENV = {"COLUMNS": "200"}


def _make_provider_and_schema(make_test_provider):
    """Create a provider, initialize schema, and return (provider, client)."""
    provider = make_test_provider()
    client = DatabaseClient(provider)
    client.initialize_schema()
    return provider, client


def _write_config(tmp_path, postgres_url, extra_sections=""):
    """Write a config TOML pointing at the testcontainers Postgres."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'[database]\nurl = "{postgres_url}"\n{extra_sections}')
    return config_path


def _drop_all_tables(make_test_provider):
    """Drop all tables for cleanup."""
    try:
        cleanup_provider = make_test_provider()
        with cleanup_provider.get_connection().cursor() as cur:
            cur.execute("""
                DROP TABLE IF EXISTS songset_share CASCADE;
                DROP TABLE IF EXISTS lyric_mark CASCADE;
                DROP TABLE IF EXISTS user_lrc_override CASCADE;
                DROP TABLE IF EXISTS user_settings CASCADE;
                DROP TABLE IF EXISTS songset_items CASCADE;
                DROP TABLE IF EXISTS songsets CASCADE;
                DROP TABLE IF EXISTS recordings CASCADE;
                DROP TABLE IF EXISTS songs CASCADE;
                DROP TABLE IF EXISTS "session" CASCADE;
                DROP TABLE IF EXISTS "account" CASCADE;
                DROP TABLE IF EXISTS "verification" CASCADE;
                DROP TABLE IF EXISTS "user" CASCADE;
                DROP FUNCTION IF EXISTS update_updated_at_column CASCADE;
                DROP FUNCTION IF EXISTS update_updatedat_column CASCADE;
            """)
        cleanup_provider.close()
    except Exception:
        pass


class TestAudioDownloadCommand:
    """Tests for 'audio download' command."""

    def test_download_without_config(self):
        """Fails cleanly when no config file exists."""
        with patch("stream_of_worship.admin.config.get_config_path") as mock_path:
            mock_path.side_effect = FileNotFoundError("No config")
            result = runner.invoke(app, ["audio", "download", "song_001"])

        assert result.exit_code == 1
        assert "Config file not found" in result.output

    def test_download_without_database(self, tmp_path, monkeypatch):
        """Fails when the database url is not configured."""
        monkeypatch.delenv("SOW_DATABASE_URL", raising=False)
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\n')

        result = runner.invoke(
            app, ["audio", "download", "song_001", "--config", str(config_path)]
        )

        assert result.exit_code != 0

    @pytest.mark.integration
    def test_download_song_not_found(self, setup_db):
        """Fails when the song ID does not exist in the catalog."""
        result = runner.invoke(
            app,
            ["audio", "download", "nonexistent", "--config", str(setup_db["config_path"])],
        )

        assert result.exit_code == 1
        assert "Song not found" in result.output

    @pytest.mark.integration
    @patch("stream_of_worship.admin.commands.audio.R2Client")
    def test_download_existing_recording(self, mock_r2_cls, setup_db):
        """Exits 0 with an informational message when a recording already exists."""
        db_client = setup_db["db_client"]
        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="existing.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
        )
        db_client.insert_recording(recording)

        mock_r2 = MagicMock()
        mock_r2_cls.return_value = mock_r2

        result = runner.invoke(
            app,
            ["audio", "download", "song_001", "--config", str(setup_db["config_path"])],
        )

        assert result.exit_code == 0
        assert "Recording already exists" in result.output
        assert "aaaaaaaaaaaa" in result.output
        assert "--force" in result.output

    @pytest.mark.integration
    @patch("stream_of_worship.admin.commands.audio.R2Client")
    def test_download_dry_run_shows_metadata(self, mock_r2_cls, setup_db):
        """Dry run displays song metadata and search query without downloading."""
        mock_r2 = MagicMock()
        mock_r2_cls.return_value = mock_r2

        result = runner.invoke(
            app,
            [
                "audio", "download", "song_001",
                "--config", str(setup_db["config_path"]),
                "--dry-run",
            ],
        )

        assert result.exit_code == 0
        assert "Dry run" in result.output
        assert "測試歌曲" in result.output
        assert "測試作曲家" in result.output
        assert "測試專輯" in result.output

    @pytest.mark.integration
    @patch("stream_of_worship.admin.commands.audio.R2Client")
    @patch("stream_of_worship.admin.commands.audio.compute_file_hash")
    @patch("stream_of_worship.admin.commands.audio.get_hash_prefix")
    @patch("stream_of_worship.admin.commands.audio.YouTubeDownloader")
    def test_download_success(
        self,
        mock_downloader_cls,
        mock_get_prefix,
        mock_compute_hash,
        mock_r2_cls,
        setup_db,
        tmp_path,
    ):
        """Full download flow creates a recording in the database."""
        fake_audio = tmp_path / "downloaded.mp3"
        fake_audio.write_bytes(b"fake audio content")

        mock_downloader = MagicMock()
        mock_downloader.build_search_query.return_value = "測試歌曲 測試作曲家 測試專輯"
        mock_downloader.preview_video.return_value = {
            "id": "test123",
            "title": "Test Video",
            "duration": 245,
            "webpage_url": "https://youtube.com/watch?v=test123",
        }
        mock_downloader.download.return_value = fake_audio
        mock_downloader_cls.return_value = mock_downloader

        mock_compute_hash.return_value = "b" * 64
        mock_get_prefix.return_value = "bbbbbbbbbbbb"

        mock_r2 = MagicMock()
        mock_r2.upload_audio.return_value = "s3://sow-audio/bbbbbbbbbbbb/audio.mp3"
        mock_r2_cls.return_value = mock_r2

        result = runner.invoke(
            app,
            ["audio", "download", "song_001", "--config", str(setup_db["config_path"]), "--yes"],
        )

        assert result.exit_code == 0
        assert "Video Preview" in result.output
        assert "Downloaded: downloaded.mp3" in result.output
        assert "bbbbbbbbbbbb" in result.output
        assert "Uploaded" in result.output
        assert "Recording saved" in result.output

        mock_downloader.preview_video.assert_called_once()

        recording = setup_db["db_client"].get_recording_by_song_id("song_001")
        assert recording is not None
        assert recording.hash_prefix == "bbbbbbbbbbbb"
        assert recording.content_hash == "b" * 64
        assert recording.song_id == "song_001"
        assert recording.original_filename == "downloaded.mp3"
        assert recording.file_size_bytes == len(b"fake audio content")
        assert recording.r2_audio_url == "s3://sow-audio/bbbbbbbbbbbb/audio.mp3"

    @pytest.mark.integration
    @patch("stream_of_worship.admin.commands.audio.R2Client")
    @patch("stream_of_worship.admin.commands.audio.compute_file_hash")
    @patch("stream_of_worship.admin.commands.audio.get_hash_prefix")
    @patch("stream_of_worship.admin.commands.audio.YouTubeDownloader")
    def test_download_youtube_failure(
        self,
        mock_downloader_cls,
        mock_get_prefix,
        mock_compute_hash,
        mock_r2_cls,
        setup_db,
    ):
        """YouTube download errors are reported cleanly."""
        mock_downloader = MagicMock()
        mock_downloader.build_search_query.return_value = "query"
        mock_downloader.preview_video.return_value = {
            "id": "test123",
            "title": "Test Video",
            "duration": 245,
            "webpage_url": "https://youtube.com/watch?v=test123",
        }
        mock_downloader.download.side_effect = RuntimeError("Network error")
        mock_downloader_cls.return_value = mock_downloader

        mock_r2 = MagicMock()
        mock_r2_cls.return_value = mock_r2

        result = runner.invoke(
            app,
            ["audio", "download", "song_001", "--config", str(setup_db["config_path"]), "--yes"],
        )

        assert result.exit_code == 1
        assert "Download failed" in result.output

    @pytest.mark.integration
    @patch("stream_of_worship.admin.commands.audio.R2Client")
    @patch("stream_of_worship.admin.commands.audio.compute_file_hash")
    @patch("stream_of_worship.admin.commands.audio.get_hash_prefix")
    @patch("stream_of_worship.admin.commands.audio.YouTubeDownloader")
    def test_download_r2_credentials_missing(
        self,
        mock_downloader_cls,
        mock_get_prefix,
        mock_compute_hash,
        mock_r2_cls,
        setup_db,
        tmp_path,
    ):
        """Missing R2 credentials are reported as a configuration error."""
        fake_audio = tmp_path / "audio.mp3"
        fake_audio.write_bytes(b"data")

        mock_downloader = MagicMock()
        mock_downloader.build_search_query.return_value = "query"
        mock_downloader.download.return_value = fake_audio
        mock_downloader_cls.return_value = mock_downloader

        mock_compute_hash.return_value = "c" * 64
        mock_get_prefix.return_value = "cccccccccccc"

        mock_r2_cls.side_effect = ValueError("R2 credentials not set")

        result = runner.invoke(
            app,
            ["audio", "download", "song_001", "--config", str(setup_db["config_path"])],
        )

        assert result.exit_code == 1
        assert "R2 configuration error" in result.output

    @pytest.mark.integration
    @patch("stream_of_worship.admin.commands.audio.R2Client")
    @patch("stream_of_worship.admin.commands.audio.compute_file_hash")
    @patch("stream_of_worship.admin.commands.audio.get_hash_prefix")
    @patch("stream_of_worship.admin.commands.audio.YouTubeDownloader")
    def test_download_r2_upload_failure(
        self,
        mock_downloader_cls,
        mock_get_prefix,
        mock_compute_hash,
        mock_r2_cls,
        setup_db,
        tmp_path,
    ):
        """R2 upload errors (non-ValueError) are reported cleanly."""
        fake_audio = tmp_path / "audio.mp3"
        fake_audio.write_bytes(b"data")

        mock_downloader = MagicMock()
        mock_downloader.build_search_query.return_value = "query"
        mock_downloader.preview_video.return_value = {
            "id": "test123",
            "title": "Test Video",
            "duration": 245,
            "webpage_url": "https://youtube.com/watch?v=test123",
        }
        mock_downloader.download.return_value = fake_audio
        mock_downloader_cls.return_value = mock_downloader

        mock_compute_hash.return_value = "d" * 64
        mock_get_prefix.return_value = "dddddddddddd"

        mock_r2 = MagicMock()
        mock_r2.upload_audio.side_effect = Exception("connection timeout")
        mock_r2_cls.return_value = mock_r2

        result = runner.invoke(
            app,
            ["audio", "download", "song_001", "--config", str(setup_db["config_path"]), "--yes"],
        )

        assert result.exit_code == 1
        assert "Upload failed" in result.output


class TestAudioListCommand:
    """Tests for 'audio list' command."""

    def test_list_without_config(self):
        """Fails cleanly when no config file exists."""
        with patch("stream_of_worship.admin.config.get_config_path") as mock_path:
            mock_path.side_effect = FileNotFoundError("No config")
            result = runner.invoke(app, ["audio", "list"])

        assert result.exit_code == 1
        assert "Config file not found" in result.output

    def test_list_without_database(self, tmp_path, monkeypatch):
        """Fails when the database url is not configured."""
        monkeypatch.delenv("SOW_DATABASE_URL", raising=False)
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\n')

        result = runner.invoke(
            app, ["audio", "list", "--config", str(config_path)]
        )

        assert result.exit_code != 0

    def test_list_rejects_invalid_visibility(self, tmp_path, monkeypatch):
        """Invalid visibility value is rejected before DB access."""
        monkeypatch.delenv("SOW_DATABASE_URL", raising=False)
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\n')

        result = runner.invoke(
            app, ["audio", "list", "--config", str(config_path), "--visibility", "bogus"],
        )

        assert result.exit_code == 1
        assert "Invalid visibility" in result.output

    def test_list_accepts_visibility_none(self, tmp_path, monkeypatch):
        """`--visibility none` passes validation (fails later at DB access, not validation)."""
        monkeypatch.delenv("SOW_DATABASE_URL", raising=False)
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\n')

        result = runner.invoke(
            app, ["audio", "list", "--config", str(config_path), "--visibility", "none"],
        )

        # Validation passed; failure (if any) is from DB, not from visibility filter.
        assert "Invalid visibility" not in result.output

    @pytest.mark.integration
    def test_list_empty_database(self, make_test_provider, postgres_url, tmp_path):
        """Shows a message when no recordings exist."""
        provider = make_test_provider()
        client = DatabaseClient(provider)
        client.initialize_schema()

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            app, ["audio", "list", "--config", str(config_path)]
        )

        assert result.exit_code == 0
        assert "No recordings found" in result.output

        _drop_all_tables(make_test_provider)

    @pytest.mark.integration
    def test_list_all_recordings(self, make_test_provider, postgres_url, tmp_path):
        """Table format shows all recordings."""
        provider = make_test_provider()
        client = DatabaseClient(provider)
        client.initialize_schema()

        songs = [
            Song(id="song_001", title="第一首歌", source_url="https://example.com/1",
                 scraped_at="2024-01-01T00:00:00"),
            Song(id="song_002", title="第二首歌", source_url="https://example.com/2",
                 scraped_at="2024-01-01T00:00:00"),
        ]
        for song in songs:
            client.insert_song(song)

        recordings = [
            Recording(content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa",
                      song_id="song_001", original_filename="song1.mp3",
                      file_size_bytes=1024000, imported_at="2024-01-15T10:30:00",
                      analysis_status="completed"),
            Recording(content_hash="b" * 64, hash_prefix="bbbbbbbbbbbb",
                      song_id="song_002", original_filename="song2.mp3",
                      file_size_bytes=2048000, imported_at="2024-01-16T10:30:00",
                      analysis_status="pending"),
        ]
        for rec in recordings:
            client.insert_recording(rec)

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            app, ["audio", "list", "--config", str(config_path)], env=WIDE_ENV,
        )

        assert result.exit_code == 0
        assert "aaaaaaaaaaaa" in result.output
        assert "bbbbbbbbbbbb" in result.output
        assert "song_001" in result.output
        assert "song_002" in result.output
        assert "2 total" in result.output

        _drop_all_tables(make_test_provider)

    @pytest.mark.integration
    def test_list_with_status_filter(self, make_test_provider, postgres_url, tmp_path):
        """Status filter returns only matching recordings."""
        provider = make_test_provider()
        client = DatabaseClient(provider)
        client.initialize_schema()

        for sid, title in [("song_001", "第一首歌"), ("song_002", "第二首歌")]:
            client.insert_song(Song(id=sid, title=title, source_url=f"https://example.com/{sid}",
                                    scraped_at="2024-01-01T00:00:00"))

        client.insert_recording(Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="song1.mp3", file_size_bytes=1024000,
            imported_at="2024-01-15T10:30:00", analysis_status="completed"))
        client.insert_recording(Recording(
            content_hash="b" * 64, hash_prefix="bbbbbbbbbbbb", song_id="song_002",
            original_filename="song2.mp3", file_size_bytes=2048000,
            imported_at="2024-01-16T10:30:00", analysis_status="pending"))

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            app, ["audio", "list", "--config", str(config_path), "--status", "completed"],
            env=WIDE_ENV,
        )

        assert result.exit_code == 0
        assert "aaaaaaaaaaaa" in result.output
        assert "bbbbbbbbbbbb" not in result.output

        _drop_all_tables(make_test_provider)

    @pytest.mark.integration
    def test_list_with_visibility_none_filter(self, make_test_provider, postgres_url, tmp_path):
        """`--visibility none` returns only recordings with NULL visibility_status."""
        provider = make_test_provider()
        client = DatabaseClient(provider)
        client.initialize_schema()

        for sid, title in [("song_001", "第一首歌"), ("song_002", "第二首歌")]:
            client.insert_song(Song(id=sid, title=title, source_url=f"https://example.com/{sid}",
                                    scraped_at="2024-01-01T00:00:00"))

        client.insert_recording(Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="song1.mp3", file_size_bytes=1024000,
            imported_at="2024-01-15T10:30:00", visibility_status="published"))
        client.insert_recording(Recording(
            content_hash="b" * 64, hash_prefix="bbbbbbbbbbbb", song_id="song_002",
            original_filename="song2.mp3", file_size_bytes=2048000,
            imported_at="2024-01-16T10:30:00", visibility_status=None))

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            app, ["audio", "list", "--config", str(config_path),
                  "--visibility", "none", "--format", "ids"],
        )

        assert result.exit_code == 0
        assert "song_002" in result.output
        assert "song_001" not in result.output

        _drop_all_tables(make_test_provider)

    @pytest.mark.integration
    def test_list_with_visibility_none_excludes_set_status(
        self, make_test_provider, postgres_url, tmp_path
    ):
        """`--visibility published` excludes recordings with NULL visibility_status."""
        provider = make_test_provider()
        client = DatabaseClient(provider)
        client.initialize_schema()

        for sid, title in [("song_001", "第一首歌"), ("song_002", "第二首歌")]:
            client.insert_song(Song(id=sid, title=title, source_url=f"https://example.com/{sid}",
                                    scraped_at="2024-01-01T00:00:00"))

        client.insert_recording(Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="song1.mp3", file_size_bytes=1024000,
            imported_at="2024-01-15T10:30:00", visibility_status="published"))
        client.insert_recording(Recording(
            content_hash="b" * 64, hash_prefix="bbbbbbbbbbbb", song_id="song_002",
            original_filename="song2.mp3", file_size_bytes=2048000,
            imported_at="2024-01-16T10:30:00", visibility_status=None))

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            app, ["audio", "list", "--config", str(config_path),
                  "--visibility", "published", "--format", "ids"],
        )

        assert result.exit_code == 0
        assert "song_001" in result.output
        assert "song_002" not in result.output

        _drop_all_tables(make_test_provider)

    @pytest.mark.integration
    def test_list_ids_format(self, make_test_provider, postgres_url, tmp_path):
        """ids format outputs one song_id per line."""
        provider = make_test_provider()
        client = DatabaseClient(provider)
        client.initialize_schema()

        for sid, title in [("song_001", "第一首歌"), ("song_002", "第二首歌")]:
            client.insert_song(Song(id=sid, title=title, source_url=f"https://example.com/{sid}",
                                    scraped_at="2024-01-01T00:00:00"))

        client.insert_recording(Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="song1.mp3", file_size_bytes=1024000,
            imported_at="2024-01-15T10:30:00"))
        client.insert_recording(Recording(
            content_hash="b" * 64, hash_prefix="bbbbbbbbbbbb", song_id="song_002",
            original_filename="song2.mp3", file_size_bytes=2048000,
            imported_at="2024-01-16T10:30:00"))

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            app, ["audio", "list", "--config", str(config_path), "--format", "ids"],
        )

        assert result.exit_code == 0
        assert "song_001" in result.output
        assert "song_002" in result.output

        _drop_all_tables(make_test_provider)

    @pytest.mark.integration
    def test_list_with_limit(self, make_test_provider, postgres_url, tmp_path):
        """Limit parameter restricts number of returned recordings."""
        provider = make_test_provider()
        client = DatabaseClient(provider)
        client.initialize_schema()

        for sid, title in [("song_001", "第一首歌"), ("song_002", "第二首歌")]:
            client.insert_song(Song(id=sid, title=title, source_url=f"https://example.com/{sid}",
                                    scraped_at="2024-01-01T00:00:00"))

        client.insert_recording(Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="song1.mp3", file_size_bytes=1024000,
            imported_at="2024-01-15T10:30:00"))
        client.insert_recording(Recording(
            content_hash="b" * 64, hash_prefix="bbbbbbbbbbbb", song_id="song_002",
            original_filename="song2.mp3", file_size_bytes=2048000,
            imported_at="2024-01-16T10:30:00"))

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            app, ["audio", "list", "--config", str(config_path), "--limit", "1"],
            env=WIDE_ENV,
        )

        assert result.exit_code == 0
        assert "1 total" in result.output

        _drop_all_tables(make_test_provider)

    @pytest.mark.integration
    def test_list_shows_song_titles(self, make_test_provider, postgres_url, tmp_path):
        """Song titles are resolved and displayed in the table."""
        provider = make_test_provider()
        client = DatabaseClient(provider)
        client.initialize_schema()

        for sid, title in [("song_001", "第一首歌"), ("song_002", "第二首歌")]:
            client.insert_song(Song(id=sid, title=title, source_url=f"https://example.com/{sid}",
                                    scraped_at="2024-01-01T00:00:00"))

        client.insert_recording(Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="song1.mp3", file_size_bytes=1024000,
            imported_at="2024-01-15T10:30:00"))
        client.insert_recording(Recording(
            content_hash="b" * 64, hash_prefix="bbbbbbbbbbbb", song_id="song_002",
            original_filename="song2.mp3", file_size_bytes=2048000,
            imported_at="2024-01-16T10:30:00"))

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            app, ["audio", "list", "--config", str(config_path)], env=WIDE_ENV,
        )

        assert result.exit_code == 0
        assert "第一首歌" in result.output
        assert "第二首歌" in result.output

        _drop_all_tables(make_test_provider)

    @pytest.mark.integration
    def test_list_shows_album_column(self, make_test_provider, postgres_url, tmp_path):
        """Album column is present in the table."""
        provider = make_test_provider()
        client = DatabaseClient(provider)
        client.initialize_schema()

        for sid, title in [("song_001", "第一首歌"), ("song_002", "第二首歌")]:
            client.insert_song(Song(id=sid, title=title, source_url=f"https://example.com/{sid}",
                                    scraped_at="2024-01-01T00:00:00"))

        client.insert_recording(Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="song1.mp3", file_size_bytes=1024000,
            imported_at="2024-01-15T10:30:00"))
        client.insert_recording(Recording(
            content_hash="b" * 64, hash_prefix="bbbbbbbbbbbb", song_id="song_002",
            original_filename="song2.mp3", file_size_bytes=2048000,
            imported_at="2024-01-16T10:30:00"))

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            app, ["audio", "list", "--config", str(config_path)], env=WIDE_ENV,
        )

        assert result.exit_code == 0
        assert "Album" in result.output

        _drop_all_tables(make_test_provider)

    @pytest.mark.integration
    def test_list_invalid_sort(self, make_test_provider, postgres_url, tmp_path):
        """Invalid sort option shows error."""
        provider = make_test_provider()
        client = DatabaseClient(provider)
        client.initialize_schema()

        client.insert_song(Song(id="song_001", title="Song", source_url="https://example.com/1",
                                scraped_at="2024-01-01T00:00:00"))
        client.insert_recording(Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="song1.mp3", file_size_bytes=1024,
            imported_at="2024-01-15T10:30:00"))

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            app, ["audio", "list", "--config", str(config_path), "--sort", "invalid"],
        )

        assert result.exit_code == 1
        assert "Invalid sort option" in result.output

        _drop_all_tables(make_test_provider)

    @pytest.mark.integration
    def test_list_album_filter(self, make_test_provider, postgres_url, tmp_path):
        """Album filter returns only matching recordings."""
        provider = make_test_provider()
        client = DatabaseClient(provider)
        client.initialize_schema()

        client.insert_song(Song(id="song_001", title="Song A", source_url="https://example.com/1",
                                scraped_at="2024-01-01T00:00:00", album_name="Album Alpha"))
        client.insert_song(Song(id="song_002", title="Song B", source_url="https://example.com/2",
                                scraped_at="2024-01-01T00:00:00", album_name="Album Beta"))

        client.insert_recording(Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="a.mp3", file_size_bytes=1024,
            imported_at="2024-01-15T10:30:00"))
        client.insert_recording(Recording(
            content_hash="b" * 64, hash_prefix="bbbbbbbbbbbb", song_id="song_002",
            original_filename="b.mp3", file_size_bytes=2048,
            imported_at="2024-01-16T10:30:00"))

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            app, ["audio", "list", "--config", str(config_path), "--album", "Alpha"],
        )

        assert result.exit_code == 0
        assert "song_001" in result.output
        assert "song_002" not in result.output

        _drop_all_tables(make_test_provider)

    @pytest.mark.integration
    def test_list_sort_by_title(self, make_test_provider, postgres_url, tmp_path):
        """Sort by title orders recordings by song title."""
        provider = make_test_provider()
        client = DatabaseClient(provider)
        client.initialize_schema()

        client.insert_song(Song(id="song_z", title="Zebra Song", source_url="https://example.com/z",
                                scraped_at="2024-01-01T00:00:00", album_name="Album Z"))
        client.insert_song(Song(id="song_a", title="Apple Song", source_url="https://example.com/a",
                                scraped_at="2024-01-01T00:00:00", album_name="Album A"))

        client.insert_recording(Recording(
            content_hash="z" * 64, hash_prefix="zzzzzzzzzzzz", song_id="song_z",
            original_filename="z.mp3", file_size_bytes=1024,
            imported_at="2024-01-15T10:30:00"))
        client.insert_recording(Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_a",
            original_filename="a.mp3", file_size_bytes=2048,
            imported_at="2024-01-16T10:30:00"))

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            app, ["audio", "list", "--config", str(config_path), "--sort", "title", "--format", "ids"],
        )

        assert result.exit_code == 0
        ids = result.output.strip().split("\n")
        assert ids == ["song_a", "song_z"]

        _drop_all_tables(make_test_provider)

    @pytest.mark.integration
    def test_list_sort_by_imported(self, make_test_provider, postgres_url, tmp_path):
        """Sort by imported uses DB default order (imported_at DESC)."""
        provider = make_test_provider()
        client = DatabaseClient(provider)
        client.initialize_schema()

        for sid, title in [("song_001", "第一首歌"), ("song_002", "第二首歌")]:
            client.insert_song(Song(id=sid, title=title, source_url=f"https://example.com/{sid}",
                                    scraped_at="2024-01-01T00:00:00"))

        client.insert_recording(Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="song1.mp3", file_size_bytes=1024000,
            imported_at="2024-01-15T10:30:00"))
        client.insert_recording(Recording(
            content_hash="b" * 64, hash_prefix="bbbbbbbbbbbb", song_id="song_002",
            original_filename="song2.mp3", file_size_bytes=2048000,
            imported_at="2024-01-16T10:30:00"))

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            app, ["audio", "list", "--config", str(config_path), "--sort", "imported", "--format", "ids"],
        )

        assert result.exit_code == 0
        ids = result.output.strip().split("\n")
        assert ids[0] == "song_002"
        assert ids[1] == "song_001"

        _drop_all_tables(make_test_provider)

    @pytest.mark.integration
    def test_list_sort_by_updated(self, make_test_provider, postgres_url, tmp_path):
        """Sort by updated orders recordings by updated_at DESC, showing Updated column."""
        provider = make_test_provider()
        client = DatabaseClient(provider)
        client.initialize_schema()

        for sid, title in [("song_001", "第一首歌"), ("song_002", "第二首歌")]:
            client.insert_song(Song(id=sid, title=title, source_url=f"https://example.com/{sid}",
                                    scraped_at="2024-01-01T00:00:00"))

        client.insert_recording(Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="song1.mp3", file_size_bytes=1024000,
            imported_at="2024-01-15T10:30:00"))
        client.insert_recording(Recording(
            content_hash="b" * 64, hash_prefix="bbbbbbbbbbbb", song_id="song_002",
            original_filename="song2.mp3", file_size_bytes=2048000,
            imported_at="2024-01-16T10:30:00"))

        # Make recording A newer by updating its updated_at via direct SQL
        with provider.get_connection().cursor() as cur:
            cur.execute(
                "UPDATE recordings SET updated_at = NOW() + INTERVAL '1 day' WHERE hash_prefix = 'aaaaaaaaaaaa'"
            )

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            app, ["audio", "list", "--config", str(config_path), "--sort", "updated"],
            env=WIDE_ENV,
        )

        assert result.exit_code == 0
        # A should appear before B in output (A has newer updated_at)
        pos_a = result.output.index("aaaaaaaaaaaa")
        pos_b = result.output.index("bbbbbbbbbbbb")
        assert pos_a < pos_b
        # Updated column header should be present
        assert "Updated" in result.output
        # Timestamps should appear in output
        assert "2024" in result.output

        _drop_all_tables(make_test_provider)

    @pytest.mark.integration
    def test_list_sort_by_updated_ids_format(self, make_test_provider, postgres_url, tmp_path):
        """Sort by updated with ids format outputs correct order."""
        provider = make_test_provider()
        client = DatabaseClient(provider)
        client.initialize_schema()

        for sid, title in [("song_001", "第一首歌"), ("song_002", "第二首歌")]:
            client.insert_song(Song(id=sid, title=title, source_url=f"https://example.com/{sid}",
                                    scraped_at="2024-01-01T00:00:00"))

        client.insert_recording(Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="song1.mp3", file_size_bytes=1024000,
            imported_at="2024-01-15T10:30:00"))
        client.insert_recording(Recording(
            content_hash="b" * 64, hash_prefix="bbbbbbbbbbbb", song_id="song_002",
            original_filename="song2.mp3", file_size_bytes=2048000,
            imported_at="2024-01-16T10:30:00"))

        # Make recording A newer
        with provider.get_connection().cursor() as cur:
            cur.execute(
                "UPDATE recordings SET updated_at = NOW() + INTERVAL '1 day' WHERE hash_prefix = 'aaaaaaaaaaaa'"
            )

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            app, ["audio", "list", "--config", str(config_path), "--sort", "updated", "--format", "ids"],
        )

        assert result.exit_code == 0
        ids = result.output.strip().split("\n")
        assert ids == ["song_001", "song_002"]

        _drop_all_tables(make_test_provider)

    def test_list_sort_updated_validation(self, tmp_path, monkeypatch):
        """`--sort updated` passes CLI validation; invalid values are rejected."""
        monkeypatch.delenv("SOW_DATABASE_URL", raising=False)
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\n')

        # --sort updated should pass validation (fails at DB, not validation)
        result = runner.invoke(
            app, ["audio", "list", "--config", str(config_path), "--sort", "updated"],
        )
        assert "Invalid sort option" not in result.output

        # Invalid sort should be rejected
        result = runner.invoke(
            app, ["audio", "list", "--config", str(config_path), "--sort", "bogus"],
        )
        assert result.exit_code == 1
        assert "Invalid sort option" in result.output

        _drop_all_tables(monkeypatch)


class TestAudioShowCommand:
    """Tests for 'audio show' command."""

    def test_show_without_config(self):
        """Fails cleanly when no config file exists."""
        with patch("stream_of_worship.admin.config.get_config_path") as mock_path:
            mock_path.side_effect = FileNotFoundError("No config")
            result = runner.invoke(app, ["audio", "show", "abc123def456"])

        assert result.exit_code == 1
        assert "Config file not found" in result.output

    def test_show_without_database(self, tmp_path, monkeypatch):
        """Fails when the database url is not configured."""
        monkeypatch.delenv("SOW_DATABASE_URL", raising=False)
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\n')

        result = runner.invoke(
            app, ["audio", "show", "abc123", "--config", str(config_path)]
        )

        assert result.exit_code != 0

    @pytest.mark.integration
    def test_show_no_recording_for_song(self, setup_db):
        """Reports an error when song has no recording."""
        result = runner.invoke(
            app,
            ["audio", "show", "song_without_recording", "--config", str(setup_db["config_path"])],
        )

        assert result.exit_code == 1
        assert "No recording found" in result.output

    @pytest.mark.integration
    def test_show_displays_basic_fields(self, make_test_provider, postgres_url, tmp_path):
        """All basic metadata fields are rendered."""
        provider = make_test_provider()
        client = DatabaseClient(provider)
        client.initialize_schema()

        song = Song(
            id="song_001", title="測試歌曲", source_url="https://example.com/1",
            scraped_at="2024-01-01T00:00:00", composer="測試作曲家",
        )
        client.insert_song(song)

        recording = Recording(
            content_hash="d" * 64, hash_prefix="dddddddddddd", song_id="song_001",
            original_filename="test_song.mp3", file_size_bytes=5242880,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/dddddddddddd/audio.mp3",
            analysis_status="completed", duration_seconds=245.3,
            tempo_bpm=128.5, musical_key="G", musical_mode="major",
            key_confidence=0.87, loudness_db=-8.2,
        )
        client.insert_recording(recording)

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            app, ["audio", "show", "song_001", "--config", str(config_path)],
        )

        assert result.exit_code == 0
        assert "song_001" in result.output
        assert "dddddddddddd" in result.output
        assert "d" * 64 in result.output
        assert "test_song.mp3" in result.output
        assert "測試歌曲" in result.output
        assert "s3://sow-audio/dddddddddddd/audio.mp3" in result.output

        _drop_all_tables(make_test_provider)

    @pytest.mark.integration
    def test_show_displays_analysis_results(self, make_test_provider, postgres_url, tmp_path):
        """Analysis section is shown when status is completed."""
        provider = make_test_provider()
        client = DatabaseClient(provider)
        client.initialize_schema()

        song = Song(
            id="song_001", title="測試歌曲", source_url="https://example.com/1",
            scraped_at="2024-01-01T00:00:00", composer="測試作曲家",
        )
        client.insert_song(song)

        recording = Recording(
            content_hash="d" * 64, hash_prefix="dddddddddddd", song_id="song_001",
            original_filename="test_song.mp3", file_size_bytes=5242880,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/dddddddddddd/audio.mp3",
            analysis_status="completed", duration_seconds=245.3,
            tempo_bpm=128.5, musical_key="G", musical_mode="major",
            key_confidence=0.87, loudness_db=-8.2,
        )
        client.insert_recording(recording)

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            app, ["audio", "show", "song_001", "--config", str(config_path)],
        )

        assert result.exit_code == 0
        assert "Analysis Results" in result.output
        assert "128.5" in result.output
        assert "major" in result.output
        assert "0.87" in result.output
        assert "-8.2" in result.output

        _drop_all_tables(make_test_provider)

    @pytest.mark.integration
    def test_show_pending_recording_no_analysis_section(self, make_test_provider, postgres_url, tmp_path):
        """Analysis Results section is absent for pending recordings."""
        provider = make_test_provider()
        client = DatabaseClient(provider)
        client.initialize_schema()

        song = Song(
            id="song_pending", title="Pending Song", source_url="https://example.com/pending",
            scraped_at="2024-01-01T00:00:00",
        )
        client.insert_song(song)

        recording = Recording(
            content_hash="e" * 64, hash_prefix="eeeeeeeeeeee", song_id="song_pending",
            original_filename="pending.mp3", file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00", analysis_status="pending",
        )
        client.insert_recording(recording)

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            app, ["audio", "show", "song_pending", "--config", str(config_path)]
        )

        assert result.exit_code == 0
        assert "song_pending" in result.output
        assert "eeeeeeeeeeee" in result.output
        assert "pending" in result.output
        assert "Analysis Results" not in result.output

        _drop_all_tables(make_test_provider)

    @pytest.mark.integration
    def test_show_recording_without_linked_song(self, make_test_provider, postgres_url, tmp_path):
        """Recording with no song_id cannot be looked up by song_id."""
        provider = make_test_provider()
        client = DatabaseClient(provider)
        client.initialize_schema()

        recording = Recording(
            content_hash="f" * 64, hash_prefix="ffffffffffff",
            original_filename="orphan.mp3", file_size_bytes=500,
            imported_at="2024-02-01T12:00:00", analysis_status="pending",
        )
        client.insert_recording(recording)

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            app, ["audio", "show", "nonexistent_song", "--config", str(config_path)]
        )

        assert result.exit_code == 1
        assert "No recording found" in result.output

        _drop_all_tables(make_test_provider)


@pytest.mark.integration
class TestAnalyzeCommand:
    """Tests for 'audio analyze' command.

    All test methods insert a recording into the DB (either to assert
    'already analyzed' or to submit analysis).
    """

    def test_analyze_without_config(self):
        """Fails cleanly when no config file exists."""
        with patch("stream_of_worship.admin.config.get_config_path") as mock_path:
            mock_path.side_effect = FileNotFoundError("No config")
            result = runner.invoke(app, ["audio", "analyze", "abc123"])

        assert result.exit_code == 1
        assert "Config file not found" in result.output

    def test_analyze_without_database(self, tmp_path, monkeypatch):
        """Fails when the database url is not configured."""
        monkeypatch.delenv("SOW_DATABASE_URL", raising=False)
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\n')

        result = runner.invoke(
            app, ["audio", "analyze", "abc123", "--config", str(config_path)]
        )

        assert result.exit_code != 0

    def test_analyze_no_recording_for_song(self, setup_db):
        """Error when song has no recording."""
        result = runner.invoke(
            app,
            ["audio", "analyze", "song_001", "--config", str(setup_db["config_path"])],
        )

        assert result.exit_code == 1
        assert "No recording found" in result.output

    def test_analyze_song_not_found(self, setup_db):
        """Error when song doesn't exist."""
        result = runner.invoke(
            app,
            ["audio", "analyze", "nonexistent_song", "--config", str(setup_db["config_path"])],
        )

        assert result.exit_code == 1
        assert "No recording found" in result.output

    def test_analyze_no_r2_audio_url(self, setup_db):
        """Error when recording lacks audio URL."""
        db_client = setup_db["db_client"]
        recording = Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="test.mp3", file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00", r2_audio_url=None,
        )
        db_client.insert_recording(recording)

        result = runner.invoke(
            app,
            ["audio", "analyze", "song_001", "--config", str(setup_db["config_path"])],
        )

        assert result.exit_code == 1
        assert "has no audio URL" in result.output

    def test_analyze_already_completed_no_force(self, setup_db):
        """Exit 0 with message when already done."""
        db_client = setup_db["db_client"]
        recording = Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="test.mp3", file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3", analysis_status="completed",
        )
        db_client.insert_recording(recording)

        result = runner.invoke(
            app,
            ["audio", "analyze", "song_001", "--config", str(setup_db["config_path"])],
        )

        assert result.exit_code == 0
        assert "already analyzed" in result.output

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_analyze_already_completed_with_force(self, mock_client_cls, setup_db, monkeypatch):
        """Re-submits with --force."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        db_client = setup_db["db_client"]
        recording = Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="test.mp3", file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3", analysis_status="completed",
        )
        db_client.insert_recording(recording)

        mock_client = MagicMock()
        mock_client.submit_fast_analysis.return_value = JobInfo(
            job_id="job-123", status="queued", job_type="fast_analyze", progress=0.0,
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            ["audio", "analyze", "song_001", "--config", str(setup_db["config_path"]), "--force"],
        )

        assert result.exit_code == 0
        assert "Analysis submitted" in result.output
        mock_client.submit_fast_analysis.assert_called_once()

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_analyze_already_processing_no_wait(self, mock_client_cls, setup_db, monkeypatch):
        """Exit 0 with existing job info."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        db_client = setup_db["db_client"]
        recording = Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="test.mp3", file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
            analysis_status="processing", analysis_job_id="existing-job-123",
        )
        db_client.insert_recording(recording)

        mock_client = MagicMock()
        mock_client.get_job.return_value = JobInfo(
            job_id="existing-job-123", status="processing", job_type="fast_analyze",
            progress=0.0,
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            ["audio", "analyze", "song_001", "--config", str(setup_db["config_path"])],
        )

        assert result.exit_code == 0
        assert "already in progress" in result.output
        assert "existing-job-123" in result.output

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_analyze_already_processing_with_wait(self, mock_client_cls, setup_db, monkeypatch):
        """Polls existing job."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        db_client = setup_db["db_client"]
        recording = Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="test.mp3", file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
            analysis_status="processing", analysis_job_id="existing-job-123",
        )
        db_client.insert_recording(recording)

        mock_client = MagicMock()
        mock_client.get_job.return_value = JobInfo(
            job_id="existing-job-123", status="processing", job_type="fast_analyze",
            progress=0.0,
        )
        mock_client.wait_for_completion.return_value = JobInfo(
            job_id="existing-job-123", status="completed", job_type="fast_analyze", progress=1.0,
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            ["audio", "analyze", "song_001", "--config", str(setup_db["config_path"]), "--wait"],
        )

        assert result.exit_code == 0
        mock_client.wait_for_completion.assert_called_once()

    def test_analyze_missing_api_key(self, setup_db):
        """Error when SOW_ANALYSIS_API_KEY not set."""
        db_client = setup_db["db_client"]
        recording = Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="test.mp3", file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

        result = runner.invoke(
            app,
            ["audio", "analyze", "song_001", "--config", str(setup_db["config_path"])],
        )

        assert result.exit_code == 1
        assert "not configured" in result.output

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_analyze_service_unavailable(self, mock_client_cls, setup_db, monkeypatch):
        """Error when service unreachable."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        db_client = setup_db["db_client"]
        recording = Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="test.mp3", file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

        mock_client = MagicMock()
        mock_client.submit_fast_analysis.side_effect = AnalysisServiceError("Cannot connect to analysis service")
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            ["audio", "analyze", "song_001", "--config", str(setup_db["config_path"])],
        )

        assert result.exit_code == 1
        assert "Failed to submit" in result.output

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_analyze_fire_and_forget_success(self, mock_client_cls, setup_db, monkeypatch):
        """Submits, updates DB to 'processing'."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        db_client = setup_db["db_client"]
        recording = Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="test.mp3", file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

        mock_client = MagicMock()
        mock_client.submit_fast_analysis.return_value = JobInfo(
            job_id="job-abc-123", status="queued", job_type="fast_analyze", progress=0.0,
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            ["audio", "analyze", "song_001", "--config", str(setup_db["config_path"])],
        )

        assert result.exit_code == 0
        assert "Analysis submitted" in result.output
        assert "job-abc-123" in result.output

        updated = db_client.get_recording_by_hash("aaaaaaaaaaaa")
        assert updated.analysis_status == "processing"
        assert updated.analysis_job_id == "job-abc-123"

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_analyze_by_song_id(self, mock_client_cls, setup_db, monkeypatch):
        """Analyzes using song_id."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        db_client = setup_db["db_client"]
        recording = Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="test.mp3", file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

        mock_client = MagicMock()
        mock_client.submit_fast_analysis.return_value = JobInfo(
            job_id="job-123", status="queued", job_type="fast_analyze", progress=0.0,
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            ["audio", "analyze", "song_001", "--config", str(setup_db["config_path"])],
        )

        assert result.exit_code == 0
        mock_client.submit_fast_analysis.assert_called_once()

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_analyze_wait_mode_completed(self, mock_client_cls, setup_db, monkeypatch):
        """Polls, stores results to DB."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        db_client = setup_db["db_client"]
        recording = Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="test.mp3", file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

        mock_client = MagicMock()
        from stream_of_worship.admin.services.analysis import AnalysisResult
        mock_client.submit_analysis.return_value = JobInfo(
            job_id="job-123", status="queued", job_type="analysis", progress=0.0,
        )
        mock_client.wait_for_completion.return_value = JobInfo(
            job_id="job-123", status="completed", job_type="analysis", progress=1.0,
            result=AnalysisResult(
                duration_seconds=245.5, tempo_bpm=128.0, musical_key="G",
                musical_mode="major", key_confidence=0.95, loudness_db=-8.5,
            ),
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            ["audio", "analyze", "song_001", "--analysis-tier", "full", "--config", str(setup_db["config_path"]), "--wait"],
        )

        assert result.exit_code == 0
        assert "Analysis completed" in result.output

        updated = db_client.get_recording_by_hash("aaaaaaaaaaaa")
        assert updated.analysis_status == "completed"
        assert updated.duration_seconds == 245.5
        assert updated.tempo_bpm == 128.0
        assert updated.musical_key == "G"

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_analyze_wait_mode_failed(self, mock_client_cls, setup_db, monkeypatch):
        """Updates DB to 'failed' on failure."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        db_client = setup_db["db_client"]
        recording = Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="test.mp3", file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

        mock_client = MagicMock()
        mock_client.submit_fast_analysis.return_value = JobInfo(
            job_id="job-123", status="queued", job_type="fast_analyze", progress=0.0,
        )
        mock_client.wait_for_completion.return_value = JobInfo(
            job_id="job-123", status="failed", job_type="fast_analyze", progress=0.0,
            error_message="Analysis pipeline error",
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            ["audio", "analyze", "song_001", "--config", str(setup_db["config_path"]), "--wait"],
        )

        assert result.exit_code == 1
        assert "Analysis failed" in result.output

        updated = db_client.get_recording_by_hash("aaaaaaaaaaaa")
        assert updated.analysis_status == "failed"

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_analyze_wait_mode_timeout(self, mock_client_cls, setup_db, monkeypatch):
        """Error on poll timeout."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        db_client = setup_db["db_client"]
        recording = Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="test.mp3", file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

        mock_client = MagicMock()
        mock_client.submit_fast_analysis.return_value = JobInfo(
            job_id="job-123", status="queued", job_type="fast_analyze", progress=0.0,
        )
        mock_client.wait_for_completion.side_effect = AnalysisServiceError("Timed out waiting for job")
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            ["audio", "analyze", "song_001", "--config", str(setup_db["config_path"]), "--wait"],
        )

        assert result.exit_code == 1
        assert "Timed out" in result.output

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_analyze_no_stems_flag(self, mock_client_cls, setup_db, monkeypatch):
        """Passes generate_stems=False."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        db_client = setup_db["db_client"]
        recording = Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="test.mp3", file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

        mock_client = MagicMock()
        mock_client.submit_analysis.return_value = JobInfo(
            job_id="job-123", status="queued", job_type="analysis", progress=0.0,
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            ["audio", "analyze", "song_001", "--analysis-tier", "full", "--config", str(setup_db["config_path"]), "--no-stems"],
        )

        assert result.exit_code == 0
        call_kwargs = mock_client.submit_analysis.call_args[1]
        assert call_kwargs["generate_stems"] is False

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_analyze_default_tier_is_fast(self, mock_client_cls, setup_db, monkeypatch):
        """Default tier is fast — submit_fast_analysis called, not submit_analysis."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        db_client = setup_db["db_client"]
        recording = Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="test.mp3", file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

        mock_client = MagicMock()
        mock_client.submit_fast_analysis.return_value = JobInfo(
            job_id="job-fast-001", status="queued", job_type="fast_analyze", progress=0.0,
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            ["audio", "analyze", "song_001", "--config", str(setup_db["config_path"])],
        )

        assert result.exit_code == 0
        mock_client.submit_fast_analysis.assert_called_once()
        mock_client.submit_analysis.assert_not_called()

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_analyze_explicit_fast_tier(self, mock_client_cls, setup_db, monkeypatch):
        """Explicit --analysis-tier fast calls submit_fast_analysis."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        db_client = setup_db["db_client"]
        recording = Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="test.mp3", file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

        mock_client = MagicMock()
        mock_client.submit_fast_analysis.return_value = JobInfo(
            job_id="job-fast-002", status="queued", job_type="fast_analyze", progress=0.0,
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            ["audio", "analyze", "song_001", "--analysis-tier", "fast", "--config", str(setup_db["config_path"])],
        )

        assert result.exit_code == 0
        mock_client.submit_fast_analysis.assert_called_once()

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_analyze_full_tier(self, mock_client_cls, setup_db, monkeypatch):
        """--analysis-tier full calls submit_analysis with generate_stems=True."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        db_client = setup_db["db_client"]
        recording = Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="test.mp3", file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

        mock_client = MagicMock()
        mock_client.submit_analysis.return_value = JobInfo(
            job_id="job-full-001", status="queued", job_type="analysis", progress=0.0,
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            ["audio", "analyze", "song_001", "--analysis-tier", "full", "--config", str(setup_db["config_path"])],
        )

        assert result.exit_code == 0
        mock_client.submit_analysis.assert_called_once()
        call_kwargs = mock_client.submit_analysis.call_args[1]
        assert call_kwargs["generate_stems"] is True

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_analyze_full_tier_no_stems(self, mock_client_cls, setup_db, monkeypatch):
        """--analysis-tier full --no-stems calls submit_analysis with generate_stems=False."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        db_client = setup_db["db_client"]
        recording = Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="test.mp3", file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

        mock_client = MagicMock()
        mock_client.submit_analysis.return_value = JobInfo(
            job_id="job-full-002", status="queued", job_type="analysis", progress=0.0,
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            ["audio", "analyze", "song_001", "--analysis-tier", "full", "--no-stems", "--config", str(setup_db["config_path"])],
        )

        assert result.exit_code == 0
        mock_client.submit_analysis.assert_called_once()
        call_kwargs = mock_client.submit_analysis.call_args[1]
        assert call_kwargs["generate_stems"] is False

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_analyze_fast_tier_no_stems_warned(self, mock_client_cls, setup_db, monkeypatch):
        """--no-stems with fast tier is warned and ignored; submit_fast_analysis still called."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        db_client = setup_db["db_client"]
        recording = Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="test.mp3", file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

        mock_client = MagicMock()
        mock_client.submit_fast_analysis.return_value = JobInfo(
            job_id="job-fast-003", status="queued", job_type="fast_analyze", progress=0.0,
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            ["audio", "analyze", "song_001", "--analysis-tier", "fast", "--no-stems", "--config", str(setup_db["config_path"])],
        )

        assert result.exit_code == 0
        assert "ignored" in result.output
        mock_client.submit_fast_analysis.assert_called_once()

    def test_analyze_invalid_tier(self, setup_db):
        """Invalid tier value exits 1 with error message."""
        result = runner.invoke(
            app,
            ["audio", "analyze", "song_001", "--analysis-tier", "bogus", "--config", str(setup_db["config_path"])],
        )

        assert result.exit_code == 1
        assert "Invalid analysis tier" in result.output

    def test_analyze_fast_skips_partial(self, setup_db):
        """Fast tier skips when analysis_status is 'partial'."""
        db_client = setup_db["db_client"]
        recording = Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="test.mp3", file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3", analysis_status="partial",
        )
        db_client.insert_recording(recording)

        result = runner.invoke(
            app,
            ["audio", "analyze", "song_001", "--config", str(setup_db["config_path"])],
        )

        assert result.exit_code == 0
        assert "already analyzed" in result.output

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_analyze_full_does_not_skip_partial(self, mock_client_cls, setup_db, monkeypatch):
        """Full tier does NOT skip on 'partial' status; submits and updates to 'completed'."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        db_client = setup_db["db_client"]
        recording = Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="test.mp3", file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3", analysis_status="partial",
        )
        db_client.insert_recording(recording)

        from stream_of_worship.admin.services.analysis import AnalysisResult
        mock_client = MagicMock()
        mock_client.submit_analysis.return_value = JobInfo(
            job_id="job-full-003", status="queued", job_type="analysis", progress=0.0,
        )
        mock_client.wait_for_completion.return_value = JobInfo(
            job_id="job-full-003", status="completed", job_type="analysis", progress=1.0,
            result=AnalysisResult(
                duration_seconds=200.0, tempo_bpm=120.0, musical_key="C",
                musical_mode="major", key_confidence=0.9, loudness_db=-10.0,
            ),
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            ["audio", "analyze", "song_001", "--analysis-tier", "full", "--config", str(setup_db["config_path"]), "--wait"],
        )

        assert result.exit_code == 0
        mock_client.submit_analysis.assert_called_once()
        updated = db_client.get_recording_by_hash("aaaaaaaaaaaa")
        assert updated.analysis_status == "completed"

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_analyze_fast_force_overrides_partial(self, mock_client_cls, setup_db, monkeypatch):
        """--force with fast tier overrides 'partial' skip and submits fast job."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        db_client = setup_db["db_client"]
        recording = Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="test.mp3", file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3", analysis_status="partial",
        )
        db_client.insert_recording(recording)

        mock_client = MagicMock()
        mock_client.submit_fast_analysis.return_value = JobInfo(
            job_id="job-fast-004", status="queued", job_type="fast_analyze", progress=0.0,
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            ["audio", "analyze", "song_001", "--analysis-tier", "fast", "--force", "--config", str(setup_db["config_path"])],
        )

        assert result.exit_code == 0
        mock_client.submit_fast_analysis.assert_called_once()

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_analyze_fast_wait_sets_partial(self, mock_client_cls, setup_db, monkeypatch):
        """Fast tier --wait sets analysis_status='partial', not 'completed'."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        db_client = setup_db["db_client"]
        recording = Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="test.mp3", file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

        from stream_of_worship.admin.services.analysis import AnalysisResult
        mock_client = MagicMock()
        mock_client.submit_fast_analysis.return_value = JobInfo(
            job_id="job-fast-005", status="queued", job_type="fast_analyze", progress=0.0,
        )
        mock_client.wait_for_completion.return_value = JobInfo(
            job_id="job-fast-005", status="completed", job_type="fast_analyze", progress=1.0,
            result=AnalysisResult(
                duration_seconds=180.0, tempo_bpm=100.0, musical_key="D",
                musical_mode="minor", key_confidence=0.88, loudness_db=-12.0,
            ),
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            ["audio", "analyze", "song_001", "--config", str(setup_db["config_path"]), "--wait"],
        )

        assert result.exit_code == 0
        assert "Analysis completed" in result.output
        updated = db_client.get_recording_by_hash("aaaaaaaaaaaa")
        assert updated.analysis_status == "partial"
        assert updated.tempo_bpm == 100.0
        assert updated.musical_key == "D"

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_analyze_fast_wait_preserves_completed(self, mock_client_cls, setup_db, monkeypatch):
        """Fast tier --force --wait on already-completed recording keeps 'completed' status."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        db_client = setup_db["db_client"]
        recording = Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="test.mp3", file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3", analysis_status="completed",
        )
        db_client.insert_recording(recording)

        from stream_of_worship.admin.services.analysis import AnalysisResult
        mock_client = MagicMock()
        mock_client.submit_fast_analysis.return_value = JobInfo(
            job_id="job-fast-006", status="queued", job_type="fast_analyze", progress=0.0,
        )
        mock_client.wait_for_completion.return_value = JobInfo(
            job_id="job-fast-006", status="completed", job_type="fast_analyze", progress=1.0,
            result=AnalysisResult(
                duration_seconds=190.0, tempo_bpm=110.0, musical_key="E",
                musical_mode="major", key_confidence=0.92, loudness_db=-9.0,
            ),
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            ["audio", "analyze", "song_001", "--force", "--config", str(setup_db["config_path"]), "--wait"],
        )

        assert result.exit_code == 0
        updated = db_client.get_recording_by_hash("aaaaaaaaaaaa")
        assert updated.analysis_status == "completed"

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_analyze_full_wait_sets_completed(self, mock_client_cls, setup_db, monkeypatch):
        """Full tier --wait sets analysis_status='completed' and writes all fields."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        db_client = setup_db["db_client"]
        recording = Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="test.mp3", file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

        from stream_of_worship.admin.services.analysis import AnalysisResult
        mock_client = MagicMock()
        mock_client.submit_analysis.return_value = JobInfo(
            job_id="job-full-004", status="queued", job_type="analysis", progress=0.0,
        )
        mock_client.wait_for_completion.return_value = JobInfo(
            job_id="job-full-004", status="completed", job_type="analysis", progress=1.0,
            result=AnalysisResult(
                duration_seconds=210.0, tempo_bpm=130.0, musical_key="F",
                musical_mode="major", key_confidence=0.93, loudness_db=-7.0,
                beats=[1.0, 2.0], downbeats=[1.0], sections=[{"start": 0.0}],
                embeddings_shape=[1, 128],
            ),
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            ["audio", "analyze", "song_001", "--analysis-tier", "full", "--config", str(setup_db["config_path"]), "--wait"],
        )

        assert result.exit_code == 0
        updated = db_client.get_recording_by_hash("aaaaaaaaaaaa")
        assert updated.analysis_status == "completed"
        assert updated.tempo_bpm == 130.0
        assert updated.musical_key == "F"

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_analyze_tier_mismatch_in_flight_job(self, mock_client_cls, setup_db, monkeypatch):
        """Tier mismatch on in-flight job submits new job instead of reusing."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        db_client = setup_db["db_client"]
        recording = Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="test.mp3", file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
            analysis_status="processing", analysis_job_id="existing-fast-job",
        )
        db_client.insert_recording(recording)

        mock_client = MagicMock()
        # Existing job is fast, but we request full
        mock_client.get_job.return_value = JobInfo(
            job_id="existing-fast-job", status="processing", job_type="fast_analyze",
            progress=0.0,
        )
        mock_client.submit_analysis.return_value = JobInfo(
            job_id="new-full-job", status="queued", job_type="analysis", progress=0.0,
        )
        mock_client.wait_for_completion.return_value = JobInfo(
            job_id="new-full-job", status="completed", job_type="analysis", progress=1.0,
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            ["audio", "analyze", "song_001", "--analysis-tier", "full", "--config", str(setup_db["config_path"]), "--wait"],
        )

        assert result.exit_code == 0
        assert "Submitting new job" in result.output
        mock_client.submit_analysis.assert_called_once()
        # Should NOT have reused the existing fast job
        assert mock_client.wait_for_completion.call_args[0][0] == "new-full-job"


class TestStatusCommand:
    """Tests for 'audio status' command."""

    def test_status_without_config(self):
        """Fails cleanly when no config file exists."""
        with patch("stream_of_worship.admin.config.get_config_path") as mock_path:
            mock_path.side_effect = FileNotFoundError("No config")
            result = runner.invoke(app, ["audio", "status"])

        assert result.exit_code == 1
        assert "Config file not found" in result.output

    def test_status_without_database(self, tmp_path, monkeypatch):
        """Fails when the database url is not configured."""
        monkeypatch.delenv("SOW_DATABASE_URL", raising=False)
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\n')

        result = runner.invoke(
            app, ["audio", "status", "--config", str(config_path)]
        )

        assert result.exit_code != 0

    @pytest.mark.integration
    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_status_with_job_id_success(self, mock_client_cls, setup_db, monkeypatch):
        """Displays job in Rich Panel."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        mock_client = MagicMock()
        mock_client.get_job.return_value = JobInfo(
            job_id="job-abc-123", status="completed", job_type="analysis",
            progress=1.0, stage="complete",
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            ["audio", "status", "job-abc-123", "--config", str(setup_db["config_path"])],
        )

        assert result.exit_code == 0
        assert "job-abc-123" in result.output
        assert "completed" in result.output

    @pytest.mark.integration
    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_status_with_job_id_not_found(self, mock_client_cls, setup_db, monkeypatch):
        """Error 404 handling."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        mock_client = MagicMock()
        mock_client.get_job.side_effect = AnalysisServiceError("Job not found", status_code=404)
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            ["audio", "status", "nonexistent-job", "--config", str(setup_db["config_path"])],
        )

        assert result.exit_code == 1
        assert "Job not found" in result.output

    @pytest.mark.integration
    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_status_with_job_id_missing_api_key(self, mock_client_cls, setup_db, monkeypatch):
        """Error 401 handling."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        mock_client = MagicMock()
        mock_client.get_job.side_effect = AnalysisServiceError("Authentication failed", status_code=401)
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            ["audio", "status", "some-job", "--config", str(setup_db["config_path"])],
        )

        assert result.exit_code == 1
        assert "Authentication failed" in result.output

    @pytest.mark.integration
    def test_status_no_args_all_completed(self, setup_db):
        """'All recordings processed' message."""
        db_client = setup_db["db_client"]
        recording = Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="test.mp3", file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
            analysis_status="completed", lrc_status="completed",
        )
        db_client.insert_recording(recording)

        result = runner.invoke(
            app,
            ["audio", "status", "--config", str(setup_db["config_path"])],
        )

        assert result.exit_code == 0
        assert "All recordings are fully processed" in result.output

    @pytest.mark.integration
    def test_status_no_args_pending(self, setup_db):
        """Shows pending recordings table."""
        db_client = setup_db["db_client"]
        recording = Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="test.mp3", file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
            analysis_status="pending", lrc_status="pending",
        )
        db_client.insert_recording(recording)

        result = runner.invoke(
            app,
            ["audio", "status", "--config", str(setup_db["config_path"])],
        )

        assert result.exit_code == 0
        assert "Pending Recordings" in result.output
        assert "song_001" in result.output

    @pytest.mark.integration
    def test_status_empty_database(self, make_test_provider, postgres_url, tmp_path):
        """Empty DB handling."""
        provider = make_test_provider()
        client = DatabaseClient(provider)
        client.initialize_schema()

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            app, ["audio", "status", "--config", str(config_path)],
        )

        assert result.exit_code == 0
        assert "All recordings are fully processed" in result.output

        _drop_all_tables(make_test_provider)


@pytest.mark.integration
class TestDownloadCommandNewFeatures:
    """Tests for new download command features (--force, --url, preview).

    All tests are DB-bound (seed a song, invoke download command).
    """

    @pytest.fixture
    def setup(self, make_test_provider, postgres_url, tmp_path):
        """Create a temp database seeded with one song."""
        provider = make_test_provider()
        client = DatabaseClient(provider)
        client.initialize_schema()

        song = Song(
            id="song_001", title="將天敞開", source_url="https://example.com/1",
            scraped_at="2024-01-01T00:00:00", composer="游智婷", album_name="敬拜讚美15",
        )
        client.insert_song(song)

        config_path = tmp_path / "config.toml"
        config_path.write_text(f'''[database]
url = "{postgres_url}"

[r2]
bucket = "test-bucket"
endpoint_url = "https://test.r2.dev"
region = "auto"
''')

        yield {
            "db_client": client,
            "config_path": config_path,
            "song": song,
            "tmp_path": tmp_path,
        }

        _drop_all_tables(make_test_provider)

    @patch("stream_of_worship.admin.commands.audio.R2Client")
    @patch("stream_of_worship.admin.commands.audio.YouTubeDownloader")
    def test_download_with_force_shows_deletion_message(
        self, mock_yt_class, mock_r2_class, setup, monkeypatch
    ):
        """--force shows deletion message for existing recording."""
        monkeypatch.setenv("SOW_R2_ACCESS_KEY_ID", "test-key")
        monkeypatch.setenv("SOW_R2_SECRET_ACCESS_KEY", "test-secret")

        db_client = setup["db_client"]
        recording = Recording(
            content_hash="old" * 24, hash_prefix="oldoldoldold", song_id="song_001",
            original_filename="old.mp3", file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://bucket/oldoldoldold/audio.mp3",
        )
        db_client.insert_recording(recording)

        mock_r2 = MagicMock()
        mock_r2.audio_exists.return_value = True
        mock_r2.upload_audio.return_value = "s3://bucket/newhash/audio.mp3"
        mock_r2_class.return_value = mock_r2

        mock_yt = MagicMock()
        mock_yt.build_search_query.return_value = "將天敞開 游智婷 敬拜讚美15"
        mock_yt.preview_video.return_value = {
            "id": "abc123", "title": "Test Video", "duration": 245,
            "webpage_url": "https://youtube.com/watch?v=abc123",
        }
        mock_yt.download.return_value = setup["tmp_path"] / "Test Video.mp3"
        mock_yt_class.return_value = mock_yt

        mp3_path = setup["tmp_path"] / "Test Video.mp3"
        mp3_path.write_bytes(b"fake audio")

        result = runner.invoke(
            app,
            ["audio", "download", "song_001", "--config", str(setup["config_path"]),
             "--yes", "--force"],
        )

        assert "Deleting existing recording" in result.output

    @patch("stream_of_worship.admin.commands.audio.R2Client")
    @patch("stream_of_worship.admin.commands.audio.YouTubeDownloader")
    def test_download_with_url_uses_direct_url(
        self, mock_yt_class, mock_r2_class, setup, monkeypatch
    ):
        """--url directly downloads from provided URL."""
        monkeypatch.setenv("SOW_R2_ACCESS_KEY_ID", "test-key")
        monkeypatch.setenv("SOW_R2_SECRET_ACCESS_KEY", "test-secret")

        mock_r2 = MagicMock()
        mock_r2.audio_exists.return_value = False
        mock_r2.upload_audio.return_value = "s3://bucket/hash/audio.mp3"
        mock_r2_class.return_value = mock_r2

        mock_yt = MagicMock()
        mock_yt.preview_video.return_value = {
            "id": "custom123", "title": "Custom Video", "duration": 245,
            "webpage_url": "https://youtube.com/watch?v=custom123",
        }
        mock_yt.download_by_url.return_value = setup["tmp_path"] / "Custom Video.mp3"
        mock_yt_class.return_value = mock_yt

        mp3_path = setup["tmp_path"] / "Custom Video.mp3"
        mp3_path.write_bytes(b"fake audio")

        result = runner.invoke(
            app,
            ["audio", "download", "song_001", "--config", str(setup["config_path"]),
             "--yes", "--url", "https://youtube.com/watch?v=custom123"],
        )

        assert result.exit_code == 0
        mock_yt.download_by_url.assert_called_once_with("https://youtube.com/watch?v=custom123")
        assert mock_yt.download.call_count == 0

    @patch("stream_of_worship.admin.commands.audio.R2Client")
    @patch("stream_of_worship.admin.commands.audio.YouTubeDownloader")
    def test_download_shows_duration_warning(
        self, mock_yt_class, mock_r2_class, setup, monkeypatch
    ):
        """Shows warning for videos over 7 minutes."""
        monkeypatch.setenv("SOW_R2_ACCESS_KEY_ID", "test-key")
        monkeypatch.setenv("SOW_R2_SECRET_ACCESS_KEY", "test-secret")

        mock_r2 = MagicMock()
        mock_r2.audio_exists.return_value = False
        mock_r2.upload_audio.return_value = "s3://bucket/hash/audio.mp3"
        mock_r2_class.return_value = mock_r2

        mock_yt = MagicMock()
        mock_yt.build_search_query.return_value = "將天敞開 游智婷 敬拜讚美15"
        mock_yt.preview_video.return_value = {
            "id": "long123", "title": "Long Video", "duration": 500,
            "webpage_url": "https://youtube.com/watch?v=long123",
        }
        mock_yt.download.return_value = setup["tmp_path"] / "Long Video.mp3"
        mock_yt_class.return_value = mock_yt

        mp3_path = setup["tmp_path"] / "Long Video.mp3"
        mp3_path.write_bytes(b"fake audio")

        result = runner.invoke(
            app,
            ["audio", "download", "song_001", "--config", str(setup["config_path"]), "--yes"],
        )

        assert result.exit_code == 0
        assert "8:20" in result.output

    @patch("stream_of_worship.admin.commands.audio.R2Client")
    @patch("stream_of_worship.admin.commands.audio.YouTubeDownloader")
    @patch("stream_of_worship.admin.commands.audio._submit_analysis_job")
    def test_download_with_analyze_flag(
        self, mock_submit_analysis, mock_yt_class, mock_r2_class, setup, monkeypatch
    ):
        """--analyze flag submits analysis job after download."""
        monkeypatch.setenv("SOW_R2_ACCESS_KEY_ID", "test-key")
        monkeypatch.setenv("SOW_R2_SECRET_ACCESS_KEY", "test-secret")
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-analysis-key")

        mock_r2 = MagicMock()
        mock_r2.audio_exists.return_value = False
        mock_r2.upload_audio.return_value = "s3://bucket/hash123/audio.mp3"
        mock_r2_class.return_value = mock_r2

        mock_yt = MagicMock()
        mock_yt.build_search_query.return_value = "將天敞開 游智婷 敬拜讚美15"
        mock_yt.preview_video.return_value = {
            "id": "test123", "title": "Test Video", "duration": 300,
            "webpage_url": "https://youtube.com/watch?v=test123",
        }
        mock_yt.download.return_value = setup["tmp_path"] / "Test Video.mp3"
        mock_yt_class.return_value = mock_yt

        mp3_path = setup["tmp_path"] / "Test Video.mp3"
        mp3_path.write_bytes(b"fake audio")

        result = runner.invoke(
            app,
            ["audio", "download", "song_001", "--config", str(setup["config_path"]),
             "--yes", "--analyze"],
        )

        assert result.exit_code == 0
        assert "Submitting for analysis" in result.output
        mock_submit_analysis.assert_called_once()
        call_kwargs = mock_submit_analysis.call_args[1]
        assert "recording" in call_kwargs

    @patch("stream_of_worship.admin.commands.audio.R2Client")
    @patch("stream_of_worship.admin.commands.audio.YouTubeDownloader")
    @patch("stream_of_worship.admin.commands.audio._submit_lrc_job")
    def test_download_with_lrc_flag(
        self, mock_submit_lrc, mock_yt_class, mock_r2_class, setup, monkeypatch
    ):
        """--lrc flag submits LRC job after download."""
        monkeypatch.setenv("SOW_R2_ACCESS_KEY_ID", "test-key")
        monkeypatch.setenv("SOW_R2_SECRET_ACCESS_KEY", "test-secret")
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-analysis-key")

        db_client = setup["db_client"]
        song = db_client.get_song("song_001")
        song.lyrics_raw = "這是歌詞\n第二行歌詞"
        db_client.insert_song(song)

        mock_r2 = MagicMock()
        mock_r2.audio_exists.return_value = False
        mock_r2.upload_audio.return_value = "s3://bucket/hash456/audio.mp3"
        mock_r2_class.return_value = mock_r2

        mock_yt = MagicMock()
        mock_yt.build_search_query.return_value = "將天敞開 游智婷 敬拜讚美15"
        mock_yt.preview_video.return_value = {
            "id": "test456", "title": "Test Video", "duration": 300,
            "webpage_url": "https://youtube.com/watch?v=test456",
        }
        mock_yt.download.return_value = setup["tmp_path"] / "Test Video.mp3"
        mock_yt_class.return_value = mock_yt

        mp3_path = setup["tmp_path"] / "Test Video.mp3"
        mp3_path.write_bytes(b"fake audio")

        result = runner.invoke(
            app,
            ["audio", "download", "song_001", "--config", str(setup["config_path"]),
             "--yes", "--lrc"],
        )

        assert result.exit_code == 0
        assert "Submitting for LRC generation" in result.output
        mock_submit_lrc.assert_called_once()
        call_kwargs = mock_submit_lrc.call_args[1]
        assert call_kwargs["song_id"] == "song_001"
        assert "recording" in call_kwargs

    @patch("stream_of_worship.admin.commands.audio.R2Client")
    @patch("stream_of_worship.admin.commands.audio.YouTubeDownloader")
    @patch("stream_of_worship.admin.commands.audio._submit_analysis_job")
    @patch("stream_of_worship.admin.commands.audio._submit_lrc_job")
    def test_download_with_all_flag(
        self, mock_submit_lrc, mock_submit_analysis, mock_yt_class, mock_r2_class, setup, monkeypatch
    ):
        """--all flag triggers both analysis and LRC submission."""
        monkeypatch.setenv("SOW_R2_ACCESS_KEY_ID", "test-key")
        monkeypatch.setenv("SOW_R2_SECRET_ACCESS_KEY", "test-secret")
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-analysis-key")

        db_client = setup["db_client"]
        song = db_client.get_song("song_001")
        song.lyrics_raw = "這是歌詞\n第二行歌詞"
        db_client.insert_song(song)

        mock_r2 = MagicMock()
        mock_r2.audio_exists.return_value = False
        mock_r2.upload_audio.return_value = "s3://bucket/hash789/audio.mp3"
        mock_r2_class.return_value = mock_r2

        mock_yt = MagicMock()
        mock_yt.build_search_query.return_value = "將天敞開 游智婷 敬拜讚美15"
        mock_yt.preview_video.return_value = {
            "id": "test789", "title": "Test Video", "duration": 300,
            "webpage_url": "https://youtube.com/watch?v=test789",
        }
        mock_yt.download.return_value = setup["tmp_path"] / "Test Video.mp3"
        mock_yt_class.return_value = mock_yt

        mp3_path = setup["tmp_path"] / "Test Video.mp3"
        mp3_path.write_bytes(b"fake audio")

        result = runner.invoke(
            app,
            ["audio", "download", "song_001", "--config", str(setup["config_path"]),
             "--yes", "--all"],
        )

        assert result.exit_code == 0
        assert "Submitting for analysis" in result.output
        assert "Submitting for LRC generation" in result.output
        mock_submit_analysis.assert_called_once()
        mock_submit_lrc.assert_called_once()

    @patch("stream_of_worship.admin.commands.audio.R2Client")
    @patch("stream_of_worship.admin.commands.audio.YouTubeDownloader")
    def test_download_without_analysis_flags_only_downloads(
        self, mock_yt_class, mock_r2_class, setup, monkeypatch
    ):
        """Without --analyze/-lrc/--all, download does NOT submit analysis or LRC."""
        monkeypatch.setenv("SOW_R2_ACCESS_KEY_ID", "test-key")
        monkeypatch.setenv("SOW_R2_SECRET_ACCESS_KEY", "test-secret")
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-analysis-key")

        mock_r2 = MagicMock()
        mock_r2.audio_exists.return_value = False
        mock_r2.upload_audio.return_value = "s3://bucket/simple/audio.mp3"
        mock_r2_class.return_value = mock_r2

        mock_yt = MagicMock()
        mock_yt.build_search_query.return_value = "將天敞開 游智婷 敬拜讚美15"
        mock_yt.preview_video.return_value = {
            "id": "simple", "title": "Test Video", "duration": 300,
            "webpage_url": "https://youtube.com/watch?v=simple",
        }
        mock_yt.download.return_value = setup["tmp_path"] / "Test Video.mp3"
        mock_yt_class.return_value = mock_yt

        mp3_path = setup["tmp_path"] / "Test Video.mp3"
        mp3_path.write_bytes(b"fake audio")

        result = runner.invoke(
            app,
            ["audio", "download", "song_001", "--config", str(setup["config_path"]), "--yes"],
        )

        assert result.exit_code == 0
        assert "Submitting for analysis" not in result.output
        assert "Submitting for LRC" not in result.output
        assert "Recording saved" in result.output


@pytest.mark.integration
class TestDeleteCommand:
    """Tests for 'audio delete' command.

    All tests are DB-bound (seed a song + recording, invoke delete command).
    """

    @pytest.fixture
    def setup(self, make_test_provider, postgres_url, tmp_path):
        """Create a temp database seeded with song and recording."""
        provider = make_test_provider()
        client = DatabaseClient(provider)
        client.initialize_schema()

        song = Song(
            id="song_001", title="測試歌曲", source_url="https://example.com/1",
            scraped_at="2024-01-01T00:00:00",
        )
        client.insert_song(song)

        recording = Recording(
            content_hash="a" * 64, hash_prefix="aaaaaaaaaaaa", song_id="song_001",
            original_filename="test.mp3", file_size_bytes=1000000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://bucket/aaaaaaaaaaaa/audio.mp3",
        )
        client.insert_recording(recording)

        config_path = tmp_path / "config.toml"
        config_path.write_text(f'''[database]
url = "{postgres_url}"

[r2]
bucket = "test-bucket"
endpoint_url = "https://test.r2.dev"
region = "auto"
''')

        yield {
            "db_client": client,
            "config_path": config_path,
            "song": song,
            "recording": recording,
        }

        _drop_all_tables(make_test_provider)

    @patch("stream_of_worship.admin.commands.audio.R2Client")
    def test_delete_without_confirmation(self, mock_r2_class, setup, monkeypatch):
        """Prompts for confirmation without --yes."""
        monkeypatch.setenv("SOW_R2_ACCESS_KEY_ID", "test-key")
        monkeypatch.setenv("SOW_R2_SECRET_ACCESS_KEY", "test-secret")

        mock_r2 = MagicMock()
        mock_r2_class.return_value = mock_r2

        result = runner.invoke(
            app,
            ["audio", "delete", "song_001", "--config", str(setup["config_path"])],
            input="y",
        )

        assert result.exit_code == 0
        assert "Delete this recording" in result.output
        assert setup["db_client"].get_recording_by_song_id("song_001") is None

    @patch("stream_of_worship.admin.commands.audio.R2Client")
    def test_delete_with_yes_flag(self, mock_r2_class, setup, monkeypatch):
        """Skips confirmation with --yes flag."""
        monkeypatch.setenv("SOW_R2_ACCESS_KEY_ID", "test-key")
        monkeypatch.setenv("SOW_R2_SECRET_ACCESS_KEY", "test-secret")

        mock_r2 = MagicMock()
        mock_r2_class.return_value = mock_r2

        result = runner.invoke(
            app,
            ["audio", "delete", "song_001", "--config", str(setup["config_path"]), "--yes"],
        )

        assert result.exit_code == 0
        assert "deleted successfully" in result.output
        assert setup["db_client"].get_recording_by_song_id("song_001") is None

    def test_delete_removes_from_database(self, setup, monkeypatch):
        """Removes recording from database."""
        monkeypatch.setenv("SOW_R2_ACCESS_KEY_ID", "test-key")
        monkeypatch.setenv("SOW_R2_SECRET_ACCESS_KEY", "test-secret")

        result = runner.invoke(
            app,
            ["audio", "delete", "song_001", "--config", str(setup["config_path"]), "--yes"],
        )

        assert result.exit_code == 0
        assert setup["db_client"].get_recording_by_song_id("song_001") is None

    def test_delete_nonexistent_recording(self, make_test_provider, postgres_url, tmp_path):
        """Error when recording doesn't exist."""
        provider = make_test_provider()
        client = DatabaseClient(provider)
        client.initialize_schema()

        song = Song(
            id="song_001", title="測試", source_url="https://example.com",
            scraped_at="2024-01-01T00:00:00",
        )
        client.insert_song(song)

        config_path = tmp_path / "config.toml"
        config_path.write_text(f'''[database]
url = "{postgres_url}"

[r2]
bucket = "test-bucket"
endpoint_url = "https://test.r2.dev"
region = "auto"
''')

        result = runner.invoke(
            app,
            ["audio", "delete", "song_001", "--config", str(config_path), "--yes"],
        )

        assert result.exit_code == 1
        assert "No recording found" in result.output

        _drop_all_tables(make_test_provider)
