"""Tests for audio CLI commands."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
import typer
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
                DROP TABLE IF EXISTS theme_anchors CASCADE;
                DROP TABLE IF EXISTS song_line_embedding CASCADE;
                DROP TABLE IF EXISTS song_embedding CASCADE;
                DROP TABLE IF EXISTS recordings CASCADE;
                DROP TABLE IF EXISTS songs CASCADE;
                DROP TABLE IF EXISTS "session" CASCADE;
                DROP TABLE IF EXISTS "account" CASCADE;
                DROP TABLE IF EXISTS "verification" CASCADE;
                DROP TABLE IF EXISTS "user" CASCADE;
                DROP FUNCTION IF EXISTS update_updated_at_column CASCADE;
                DROP FUNCTION IF EXISTS update_updatedat_column CASCADE;
                DROP EXTENSION IF EXISTS vector CASCADE;
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
        config_path.write_text("[database]\n")

        result = runner.invoke(app, ["audio", "download", "song_001", "--config", str(config_path)])

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
                "audio",
                "download",
                "song_001",
                "--config",
                str(setup_db["config_path"]),
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
        config_path.write_text("[database]\n")

        result = runner.invoke(app, ["audio", "list", "--config", str(config_path)])

        assert result.exit_code != 0

    def test_list_rejects_invalid_visibility(self, tmp_path, monkeypatch):
        """Invalid visibility value is rejected before DB access."""
        monkeypatch.delenv("SOW_DATABASE_URL", raising=False)
        config_path = tmp_path / "config.toml"
        config_path.write_text("[database]\n")

        result = runner.invoke(
            app,
            ["audio", "list", "--config", str(config_path), "--visibility", "bogus"],
        )

        assert result.exit_code == 1
        assert "Invalid visibility" in result.output

    def test_list_accepts_visibility_none(self, tmp_path, monkeypatch):
        """`--visibility none` passes validation (fails later at DB access, not validation)."""
        monkeypatch.delenv("SOW_DATABASE_URL", raising=False)
        config_path = tmp_path / "config.toml"
        config_path.write_text("[database]\n")

        result = runner.invoke(
            app,
            ["audio", "list", "--config", str(config_path), "--visibility", "none"],
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

        result = runner.invoke(app, ["audio", "list", "--config", str(config_path)])

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
            Song(
                id="song_001",
                title="第一首歌",
                source_url="https://example.com/1",
                scraped_at="2024-01-01T00:00:00",
            ),
            Song(
                id="song_002",
                title="第二首歌",
                source_url="https://example.com/2",
                scraped_at="2024-01-01T00:00:00",
            ),
        ]
        for song in songs:
            client.insert_song(song)

        recordings = [
            Recording(
                content_hash="a" * 64,
                hash_prefix="aaaaaaaaaaaa",
                song_id="song_001",
                original_filename="song1.mp3",
                file_size_bytes=1024000,
                imported_at="2024-01-15T10:30:00",
                analysis_status="completed",
            ),
            Recording(
                content_hash="b" * 64,
                hash_prefix="bbbbbbbbbbbb",
                song_id="song_002",
                original_filename="song2.mp3",
                file_size_bytes=2048000,
                imported_at="2024-01-16T10:30:00",
                analysis_status="pending",
            ),
        ]
        for rec in recordings:
            client.insert_recording(rec)

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            app,
            ["audio", "list", "--config", str(config_path)],
            env=WIDE_ENV,
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
            client.insert_song(
                Song(
                    id=sid,
                    title=title,
                    source_url=f"https://example.com/{sid}",
                    scraped_at="2024-01-01T00:00:00",
                )
            )

        client.insert_recording(
            Recording(
                content_hash="a" * 64,
                hash_prefix="aaaaaaaaaaaa",
                song_id="song_001",
                original_filename="song1.mp3",
                file_size_bytes=1024000,
                imported_at="2024-01-15T10:30:00",
                analysis_status="completed",
            )
        )
        client.insert_recording(
            Recording(
                content_hash="b" * 64,
                hash_prefix="bbbbbbbbbbbb",
                song_id="song_002",
                original_filename="song2.mp3",
                file_size_bytes=2048000,
                imported_at="2024-01-16T10:30:00",
                analysis_status="pending",
            )
        )

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            app,
            ["audio", "list", "--config", str(config_path), "--status", "completed"],
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
            client.insert_song(
                Song(
                    id=sid,
                    title=title,
                    source_url=f"https://example.com/{sid}",
                    scraped_at="2024-01-01T00:00:00",
                )
            )

        client.insert_recording(
            Recording(
                content_hash="a" * 64,
                hash_prefix="aaaaaaaaaaaa",
                song_id="song_001",
                original_filename="song1.mp3",
                file_size_bytes=1024000,
                imported_at="2024-01-15T10:30:00",
                visibility_status="published",
            )
        )
        client.insert_recording(
            Recording(
                content_hash="b" * 64,
                hash_prefix="bbbbbbbbbbbb",
                song_id="song_002",
                original_filename="song2.mp3",
                file_size_bytes=2048000,
                imported_at="2024-01-16T10:30:00",
                visibility_status=None,
            )
        )

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            app,
            [
                "audio",
                "list",
                "--config",
                str(config_path),
                "--visibility",
                "none",
                "--format",
                "ids",
            ],
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
            client.insert_song(
                Song(
                    id=sid,
                    title=title,
                    source_url=f"https://example.com/{sid}",
                    scraped_at="2024-01-01T00:00:00",
                )
            )

        client.insert_recording(
            Recording(
                content_hash="a" * 64,
                hash_prefix="aaaaaaaaaaaa",
                song_id="song_001",
                original_filename="song1.mp3",
                file_size_bytes=1024000,
                imported_at="2024-01-15T10:30:00",
                visibility_status="published",
            )
        )
        client.insert_recording(
            Recording(
                content_hash="b" * 64,
                hash_prefix="bbbbbbbbbbbb",
                song_id="song_002",
                original_filename="song2.mp3",
                file_size_bytes=2048000,
                imported_at="2024-01-16T10:30:00",
                visibility_status=None,
            )
        )

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            app,
            [
                "audio",
                "list",
                "--config",
                str(config_path),
                "--visibility",
                "published",
                "--format",
                "ids",
            ],
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
            client.insert_song(
                Song(
                    id=sid,
                    title=title,
                    source_url=f"https://example.com/{sid}",
                    scraped_at="2024-01-01T00:00:00",
                )
            )

        client.insert_recording(
            Recording(
                content_hash="a" * 64,
                hash_prefix="aaaaaaaaaaaa",
                song_id="song_001",
                original_filename="song1.mp3",
                file_size_bytes=1024000,
                imported_at="2024-01-15T10:30:00",
            )
        )
        client.insert_recording(
            Recording(
                content_hash="b" * 64,
                hash_prefix="bbbbbbbbbbbb",
                song_id="song_002",
                original_filename="song2.mp3",
                file_size_bytes=2048000,
                imported_at="2024-01-16T10:30:00",
            )
        )

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            app,
            ["audio", "list", "--config", str(config_path), "--format", "ids"],
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
            client.insert_song(
                Song(
                    id=sid,
                    title=title,
                    source_url=f"https://example.com/{sid}",
                    scraped_at="2024-01-01T00:00:00",
                )
            )

        client.insert_recording(
            Recording(
                content_hash="a" * 64,
                hash_prefix="aaaaaaaaaaaa",
                song_id="song_001",
                original_filename="song1.mp3",
                file_size_bytes=1024000,
                imported_at="2024-01-15T10:30:00",
            )
        )
        client.insert_recording(
            Recording(
                content_hash="b" * 64,
                hash_prefix="bbbbbbbbbbbb",
                song_id="song_002",
                original_filename="song2.mp3",
                file_size_bytes=2048000,
                imported_at="2024-01-16T10:30:00",
            )
        )

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            app,
            ["audio", "list", "--config", str(config_path), "--limit", "1"],
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
            client.insert_song(
                Song(
                    id=sid,
                    title=title,
                    source_url=f"https://example.com/{sid}",
                    scraped_at="2024-01-01T00:00:00",
                )
            )

        client.insert_recording(
            Recording(
                content_hash="a" * 64,
                hash_prefix="aaaaaaaaaaaa",
                song_id="song_001",
                original_filename="song1.mp3",
                file_size_bytes=1024000,
                imported_at="2024-01-15T10:30:00",
            )
        )
        client.insert_recording(
            Recording(
                content_hash="b" * 64,
                hash_prefix="bbbbbbbbbbbb",
                song_id="song_002",
                original_filename="song2.mp3",
                file_size_bytes=2048000,
                imported_at="2024-01-16T10:30:00",
            )
        )

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            app,
            ["audio", "list", "--config", str(config_path)],
            env=WIDE_ENV,
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
            client.insert_song(
                Song(
                    id=sid,
                    title=title,
                    source_url=f"https://example.com/{sid}",
                    scraped_at="2024-01-01T00:00:00",
                )
            )

        client.insert_recording(
            Recording(
                content_hash="a" * 64,
                hash_prefix="aaaaaaaaaaaa",
                song_id="song_001",
                original_filename="song1.mp3",
                file_size_bytes=1024000,
                imported_at="2024-01-15T10:30:00",
            )
        )
        client.insert_recording(
            Recording(
                content_hash="b" * 64,
                hash_prefix="bbbbbbbbbbbb",
                song_id="song_002",
                original_filename="song2.mp3",
                file_size_bytes=2048000,
                imported_at="2024-01-16T10:30:00",
            )
        )

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            app,
            ["audio", "list", "--config", str(config_path)],
            env=WIDE_ENV,
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

        client.insert_song(
            Song(
                id="song_001",
                title="Song",
                source_url="https://example.com/1",
                scraped_at="2024-01-01T00:00:00",
            )
        )
        client.insert_recording(
            Recording(
                content_hash="a" * 64,
                hash_prefix="aaaaaaaaaaaa",
                song_id="song_001",
                original_filename="song1.mp3",
                file_size_bytes=1024,
                imported_at="2024-01-15T10:30:00",
            )
        )

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            app,
            ["audio", "list", "--config", str(config_path), "--sort", "invalid"],
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

        client.insert_song(
            Song(
                id="song_001",
                title="Song A",
                source_url="https://example.com/1",
                scraped_at="2024-01-01T00:00:00",
                album_name="Album Alpha",
            )
        )
        client.insert_song(
            Song(
                id="song_002",
                title="Song B",
                source_url="https://example.com/2",
                scraped_at="2024-01-01T00:00:00",
                album_name="Album Beta",
            )
        )

        client.insert_recording(
            Recording(
                content_hash="a" * 64,
                hash_prefix="aaaaaaaaaaaa",
                song_id="song_001",
                original_filename="a.mp3",
                file_size_bytes=1024,
                imported_at="2024-01-15T10:30:00",
            )
        )
        client.insert_recording(
            Recording(
                content_hash="b" * 64,
                hash_prefix="bbbbbbbbbbbb",
                song_id="song_002",
                original_filename="b.mp3",
                file_size_bytes=2048,
                imported_at="2024-01-16T10:30:00",
            )
        )

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            app,
            ["audio", "list", "--config", str(config_path), "--album", "Alpha"],
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

        client.insert_song(
            Song(
                id="song_z",
                title="Zebra Song",
                source_url="https://example.com/z",
                scraped_at="2024-01-01T00:00:00",
                album_name="Album Z",
            )
        )
        client.insert_song(
            Song(
                id="song_a",
                title="Apple Song",
                source_url="https://example.com/a",
                scraped_at="2024-01-01T00:00:00",
                album_name="Album A",
            )
        )

        client.insert_recording(
            Recording(
                content_hash="z" * 64,
                hash_prefix="zzzzzzzzzzzz",
                song_id="song_z",
                original_filename="z.mp3",
                file_size_bytes=1024,
                imported_at="2024-01-15T10:30:00",
            )
        )
        client.insert_recording(
            Recording(
                content_hash="a" * 64,
                hash_prefix="aaaaaaaaaaaa",
                song_id="song_a",
                original_filename="a.mp3",
                file_size_bytes=2048,
                imported_at="2024-01-16T10:30:00",
            )
        )

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            app,
            ["audio", "list", "--config", str(config_path), "--sort", "title", "--format", "ids"],
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
            client.insert_song(
                Song(
                    id=sid,
                    title=title,
                    source_url=f"https://example.com/{sid}",
                    scraped_at="2024-01-01T00:00:00",
                )
            )

        client.insert_recording(
            Recording(
                content_hash="a" * 64,
                hash_prefix="aaaaaaaaaaaa",
                song_id="song_001",
                original_filename="song1.mp3",
                file_size_bytes=1024000,
                imported_at="2024-01-15T10:30:00",
            )
        )
        client.insert_recording(
            Recording(
                content_hash="b" * 64,
                hash_prefix="bbbbbbbbbbbb",
                song_id="song_002",
                original_filename="song2.mp3",
                file_size_bytes=2048000,
                imported_at="2024-01-16T10:30:00",
            )
        )

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            app,
            [
                "audio",
                "list",
                "--config",
                str(config_path),
                "--sort",
                "imported",
                "--format",
                "ids",
            ],
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
            client.insert_song(
                Song(
                    id=sid,
                    title=title,
                    source_url=f"https://example.com/{sid}",
                    scraped_at="2024-01-01T00:00:00",
                )
            )

        client.insert_recording(
            Recording(
                content_hash="a" * 64,
                hash_prefix="aaaaaaaaaaaa",
                song_id="song_001",
                original_filename="song1.mp3",
                file_size_bytes=1024000,
                imported_at="2024-01-15T10:30:00",
            )
        )
        client.insert_recording(
            Recording(
                content_hash="b" * 64,
                hash_prefix="bbbbbbbbbbbb",
                song_id="song_002",
                original_filename="song2.mp3",
                file_size_bytes=2048000,
                imported_at="2024-01-16T10:30:00",
            )
        )

        # Make recording A newer by updating its updated_at via direct SQL
        with provider.get_connection().cursor() as cur:
            cur.execute(
                "UPDATE recordings SET updated_at = NOW() + INTERVAL '1 day' WHERE hash_prefix = 'aaaaaaaaaaaa'"
            )

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            app,
            ["audio", "list", "--config", str(config_path), "--sort", "updated"],
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
            client.insert_song(
                Song(
                    id=sid,
                    title=title,
                    source_url=f"https://example.com/{sid}",
                    scraped_at="2024-01-01T00:00:00",
                )
            )

        client.insert_recording(
            Recording(
                content_hash="a" * 64,
                hash_prefix="aaaaaaaaaaaa",
                song_id="song_001",
                original_filename="song1.mp3",
                file_size_bytes=1024000,
                imported_at="2024-01-15T10:30:00",
            )
        )
        client.insert_recording(
            Recording(
                content_hash="b" * 64,
                hash_prefix="bbbbbbbbbbbb",
                song_id="song_002",
                original_filename="song2.mp3",
                file_size_bytes=2048000,
                imported_at="2024-01-16T10:30:00",
            )
        )

        # Make recording A newer
        with provider.get_connection().cursor() as cur:
            cur.execute(
                "UPDATE recordings SET updated_at = NOW() + INTERVAL '1 day' WHERE hash_prefix = 'aaaaaaaaaaaa'"
            )

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            app,
            ["audio", "list", "--config", str(config_path), "--sort", "updated", "--format", "ids"],
        )

        assert result.exit_code == 0
        ids = result.output.strip().split("\n")
        assert ids == ["song_001", "song_002"]

        _drop_all_tables(make_test_provider)

    def test_list_sort_updated_validation(self, tmp_path, monkeypatch):
        """`--sort updated` passes CLI validation; invalid values are rejected."""
        monkeypatch.delenv("SOW_DATABASE_URL", raising=False)
        config_path = tmp_path / "config.toml"
        config_path.write_text("[database]\n")

        # --sort updated should pass validation (fails at DB, not validation)
        result = runner.invoke(
            app,
            ["audio", "list", "--config", str(config_path), "--sort", "updated"],
        )
        assert "Invalid sort option" not in result.output

        # Invalid sort should be rejected
        result = runner.invoke(
            app,
            ["audio", "list", "--config", str(config_path), "--sort", "bogus"],
        )
        assert result.exit_code == 1
        assert "Invalid sort option" in result.output

        _drop_all_tables(monkeypatch)

    def test_list_column_caps_values(self):
        """Column caps match the measured envelope (see _list_column_caps docstring)."""
        from stream_of_worship.admin.commands.audio import _list_column_caps

        assert _list_column_caps(100) == (12, 11, 12, None)
        assert _list_column_caps(105) == (14, 12, 12, None)
        assert _list_column_caps(111) == (18, 12, 12, None)
        assert _list_column_caps(130) == (18, 31, 16, None)
        assert _list_column_caps(140) == (18, 36, 16, None)
        assert _list_column_caps(150) == (18, 36, 16, 14)
        assert _list_column_caps(200) == (18, 36, 16, 64)

    def test_list_column_caps_extra_col_disables_filename(self):
        """`--sort updated` (extra Updated column) disables the Filename column."""
        from stream_of_worship.admin.commands.audio import _list_column_caps

        assert _list_column_caps(140, extra_col=True) == (18, 24, 16, None)
        assert _list_column_caps(200, extra_col=True) == (18, 36, 16, None)
        assert _list_column_caps(111, extra_col=True) == (11, 11, 12, None)

    def test_list_column_caps_monotonic(self):
        """Text cap never decreases as pane width grows; id cap never decreases
        within a text-cap rung."""
        from stream_of_worship.admin.commands.audio import _list_column_caps

        prev_text, prev_id = 0, 0
        for width in range(100, 201):
            text_cap, id_cap, _updated_cap, _fn = _list_column_caps(width)
            assert text_cap >= prev_text
            assert id_cap >= prev_id if text_cap == prev_text else True
            prev_text, prev_id = text_cap, id_cap

    def test_list_column_caps_extra_col_disables_filename(self):
        """`--sort updated` (extra Updated column) disables the Filename column."""
        from stream_of_worship.admin.commands.audio import _list_column_caps

        assert _list_column_caps(140, extra_col=True) == (18, 24, 16, None)
        assert _list_column_caps(200, extra_col=True) == (18, 36, 16, None)
        assert _list_column_caps(111, extra_col=True) == (11, 11, 12, None)

    def test_list_column_caps_monotonic(self):
        """Text and id caps never decrease as pane width grows."""
        from stream_of_worship.admin.commands.audio import _list_column_caps

        prev_text, prev_id = 0, 0
        for width in range(100, 201):
            text_cap, id_cap, _updated_cap, _fn = _list_column_caps(width)
            assert text_cap >= prev_text
            assert id_cap >= prev_id
            prev_text, prev_id = text_cap, id_cap

    def test_display_truncate_cjk_aware(self):
        """Truncation measures display cells, not character count."""
        from stream_of_worship.admin.commands.audio import _display_truncate

        long_name = "【主啊，我要跟隨祢 Lord】官方歌詞版MV.mp3"
        result = _display_truncate(long_name, 40)
        assert result.endswith("…")
        assert len(result) < len(long_name)
        from rich.cells import cell_len

        assert cell_len(result) <= 40
        assert _display_truncate("short.mp3", 40) == "short.mp3"

    @pytest.mark.integration
    def test_list_narrow_width_renders_cjk_columns(
        self, make_test_provider, postgres_url, tmp_path, monkeypatch
    ):
        """At 120 columns Album/Song Title render instead of collapsing."""
        provider = make_test_provider()
        client = DatabaseClient(provider)
        client.initialize_schema()

        for sid, title in [("song_001", "第一首歌"), ("song_002", "第二首歌")]:
            client.insert_song(
                Song(
                    id=sid,
                    title=title,
                    source_url=f"https://example.com/{sid}",
                    scraped_at="2024-01-01T00:00:00",
                )
            )

        client.insert_recording(
            Recording(
                content_hash="a" * 64,
                hash_prefix="aaaaaaaaaaaa",
                song_id="song_001",
                original_filename="song1.mp3",
                file_size_bytes=1024000,
                imported_at="2024-01-15T10:30:00",
            )
        )
        client.insert_recording(
            Recording(
                content_hash="b" * 64,
                hash_prefix="bbbbbbbbbbbb",
                song_id="song_002",
                original_filename="song2.mp3",
                file_size_bytes=2048000,
                imported_at="2024-01-16T10:30:00",
            )
        )

        config_path = _write_config(tmp_path, postgres_url)

        monkeypatch.setenv("COLUMNS", "120")
        result = runner.invoke(app, ["audio", "list", "--config", str(config_path)])

        assert result.exit_code == 0
        assert "第一首歌" in result.output
        assert "第二首歌" in result.output
        assert "Album" in result.output

        _drop_all_tables(make_test_provider)


class TestAudioShowCommand:
    """Tests for 'audio show' command."""

    def test_show_filename_truncation_unit(self):
        """_display_truncate caps CJK filenames to the display-width cap."""
        from stream_of_worship.admin.commands.audio import _display_truncate

        name = "【主啊，我要跟隨祢 Lord】官方歌詞版MV.mp3"
        result = _display_truncate(name, 40)
        from rich.cells import cell_len

        assert cell_len(result) <= 40
        assert result.endswith("…")
        assert _display_truncate("short.mp3", 40) == "short.mp3"

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
        config_path.write_text("[database]\n")

        result = runner.invoke(app, ["audio", "show", "abc123", "--config", str(config_path)])

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
            id="song_001",
            title="測試歌曲",
            source_url="https://example.com/1",
            scraped_at="2024-01-01T00:00:00",
            composer="測試作曲家",
        )
        client.insert_song(song)

        recording = Recording(
            content_hash="d" * 64,
            hash_prefix="dddddddddddd",
            song_id="song_001",
            original_filename="test_song.mp3",
            file_size_bytes=5242880,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/dddddddddddd/audio.mp3",
            analysis_status="completed",
            duration_seconds=245.3,
            tempo_bpm=128.5,
            musical_key="G",
            musical_mode="major",
            key_confidence=0.87,
            loudness_db=-8.2,
        )
        client.insert_recording(recording)

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            app,
            ["audio", "show", "song_001", "--config", str(config_path)],
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
            id="song_001",
            title="測試歌曲",
            source_url="https://example.com/1",
            scraped_at="2024-01-01T00:00:00",
            composer="測試作曲家",
        )
        client.insert_song(song)

        recording = Recording(
            content_hash="d" * 64,
            hash_prefix="dddddddddddd",
            song_id="song_001",
            original_filename="test_song.mp3",
            file_size_bytes=5242880,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/dddddddddddd/audio.mp3",
            analysis_status="completed",
            duration_seconds=245.3,
            tempo_bpm=128.5,
            musical_key="G",
            musical_mode="major",
            key_confidence=0.87,
            loudness_db=-8.2,
        )
        client.insert_recording(recording)

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            app,
            ["audio", "show", "song_001", "--config", str(config_path)],
        )

        assert result.exit_code == 0
        assert "Analysis Results" in result.output
        assert "128.5" in result.output
        assert "major" in result.output
        assert "0.87" in result.output
        assert "-8.2" in result.output

        _drop_all_tables(make_test_provider)

    @pytest.mark.integration
    def test_show_pending_recording_no_analysis_section(
        self, make_test_provider, postgres_url, tmp_path
    ):
        """Analysis Results section is absent for pending recordings."""
        provider = make_test_provider()
        client = DatabaseClient(provider)
        client.initialize_schema()

        song = Song(
            id="song_pending",
            title="Pending Song",
            source_url="https://example.com/pending",
            scraped_at="2024-01-01T00:00:00",
        )
        client.insert_song(song)

        recording = Recording(
            content_hash="e" * 64,
            hash_prefix="eeeeeeeeeeee",
            song_id="song_pending",
            original_filename="pending.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            analysis_status="pending",
        )
        client.insert_recording(recording)

        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(app, ["audio", "show", "song_pending", "--config", str(config_path)])

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
            content_hash="f" * 64,
            hash_prefix="ffffffffffff",
            original_filename="orphan.mp3",
            file_size_bytes=500,
            imported_at="2024-02-01T12:00:00",
            analysis_status="pending",
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
        config_path.write_text("[database]\n")

        result = runner.invoke(app, ["audio", "analyze", "abc123", "--config", str(config_path)])

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
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url=None,
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
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
            analysis_status="completed",
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
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
            analysis_status="completed",
        )
        db_client.insert_recording(recording)

        mock_client = MagicMock()
        mock_client.submit_fast_analysis.return_value = JobInfo(
            job_id="job-123",
            status="queued",
            job_type="fast_analyze",
            progress=0.0,
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
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
            analysis_status="processing",
            analysis_job_id="existing-job-123",
        )
        db_client.insert_recording(recording)

        mock_client = MagicMock()
        mock_client.get_job.return_value = JobInfo(
            job_id="existing-job-123",
            status="processing",
            job_type="fast_analyze",
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
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
            analysis_status="processing",
            analysis_job_id="existing-job-123",
        )
        db_client.insert_recording(recording)

        mock_client = MagicMock()
        mock_client.get_job.return_value = JobInfo(
            job_id="existing-job-123",
            status="processing",
            job_type="fast_analyze",
            progress=0.0,
        )
        mock_client.wait_for_completion.return_value = JobInfo(
            job_id="existing-job-123",
            status="completed",
            job_type="fast_analyze",
            progress=1.0,
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
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
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
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

        mock_client = MagicMock()
        mock_client.submit_fast_analysis.side_effect = AnalysisServiceError(
            "Cannot connect to analysis service"
        )
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
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

        mock_client = MagicMock()
        mock_client.submit_fast_analysis.return_value = JobInfo(
            job_id="job-abc-123",
            status="queued",
            job_type="fast_analyze",
            progress=0.0,
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
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

        mock_client = MagicMock()
        mock_client.submit_fast_analysis.return_value = JobInfo(
            job_id="job-123",
            status="queued",
            job_type="fast_analyze",
            progress=0.0,
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
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

        mock_client = MagicMock()
        from stream_of_worship.admin.services.analysis import AnalysisResult

        mock_client.submit_analysis.return_value = JobInfo(
            job_id="job-123",
            status="queued",
            job_type="analysis",
            progress=0.0,
        )
        mock_client.wait_for_completion.return_value = JobInfo(
            job_id="job-123",
            status="completed",
            job_type="analysis",
            progress=1.0,
            result=AnalysisResult(
                duration_seconds=245.5,
                tempo_bpm=128.0,
                musical_key="G",
                musical_mode="major",
                key_confidence=0.95,
                loudness_db=-8.5,
            ),
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            [
                "audio",
                "analyze",
                "song_001",
                "--analysis-tier",
                "full",
                "--config",
                str(setup_db["config_path"]),
                "--wait",
            ],
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
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

        mock_client = MagicMock()
        mock_client.submit_fast_analysis.return_value = JobInfo(
            job_id="job-123",
            status="queued",
            job_type="fast_analyze",
            progress=0.0,
        )
        mock_client.wait_for_completion.return_value = JobInfo(
            job_id="job-123",
            status="failed",
            job_type="fast_analyze",
            progress=0.0,
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
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

        mock_client = MagicMock()
        mock_client.submit_fast_analysis.return_value = JobInfo(
            job_id="job-123",
            status="queued",
            job_type="fast_analyze",
            progress=0.0,
        )
        mock_client.wait_for_completion.side_effect = AnalysisServiceError(
            "Timed out waiting for job"
        )
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
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

        mock_client = MagicMock()
        mock_client.submit_analysis.return_value = JobInfo(
            job_id="job-123",
            status="queued",
            job_type="analysis",
            progress=0.0,
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            [
                "audio",
                "analyze",
                "song_001",
                "--analysis-tier",
                "full",
                "--config",
                str(setup_db["config_path"]),
                "--no-stems",
            ],
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
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

        mock_client = MagicMock()
        mock_client.submit_fast_analysis.return_value = JobInfo(
            job_id="job-fast-001",
            status="queued",
            job_type="fast_analyze",
            progress=0.0,
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
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

        mock_client = MagicMock()
        mock_client.submit_fast_analysis.return_value = JobInfo(
            job_id="job-fast-002",
            status="queued",
            job_type="fast_analyze",
            progress=0.0,
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            [
                "audio",
                "analyze",
                "song_001",
                "--analysis-tier",
                "fast",
                "--config",
                str(setup_db["config_path"]),
            ],
        )

        assert result.exit_code == 0
        mock_client.submit_fast_analysis.assert_called_once()

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_analyze_full_tier(self, mock_client_cls, setup_db, monkeypatch):
        """--analysis-tier full calls submit_analysis with generate_stems=True."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        db_client = setup_db["db_client"]
        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

        mock_client = MagicMock()
        mock_client.submit_analysis.return_value = JobInfo(
            job_id="job-full-001",
            status="queued",
            job_type="analysis",
            progress=0.0,
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            [
                "audio",
                "analyze",
                "song_001",
                "--analysis-tier",
                "full",
                "--config",
                str(setup_db["config_path"]),
            ],
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
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

        mock_client = MagicMock()
        mock_client.submit_analysis.return_value = JobInfo(
            job_id="job-full-002",
            status="queued",
            job_type="analysis",
            progress=0.0,
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            [
                "audio",
                "analyze",
                "song_001",
                "--analysis-tier",
                "full",
                "--no-stems",
                "--config",
                str(setup_db["config_path"]),
            ],
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
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

        mock_client = MagicMock()
        mock_client.submit_fast_analysis.return_value = JobInfo(
            job_id="job-fast-003",
            status="queued",
            job_type="fast_analyze",
            progress=0.0,
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            [
                "audio",
                "analyze",
                "song_001",
                "--analysis-tier",
                "fast",
                "--no-stems",
                "--config",
                str(setup_db["config_path"]),
            ],
        )

        assert result.exit_code == 0
        assert "ignored" in result.output
        mock_client.submit_fast_analysis.assert_called_once()

    def test_analyze_invalid_tier(self, setup_db):
        """Invalid tier value exits 1 with error message."""
        result = runner.invoke(
            app,
            [
                "audio",
                "analyze",
                "song_001",
                "--analysis-tier",
                "bogus",
                "--config",
                str(setup_db["config_path"]),
            ],
        )

        assert result.exit_code == 1
        assert "Invalid analysis tier" in result.output

    def test_analyze_fast_skips_partial(self, setup_db):
        """Fast tier skips when analysis_status is 'partial'."""
        db_client = setup_db["db_client"]
        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
            analysis_status="partial",
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
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
            analysis_status="partial",
        )
        db_client.insert_recording(recording)

        from stream_of_worship.admin.services.analysis import AnalysisResult

        mock_client = MagicMock()
        mock_client.submit_analysis.return_value = JobInfo(
            job_id="job-full-003",
            status="queued",
            job_type="analysis",
            progress=0.0,
        )
        mock_client.wait_for_completion.return_value = JobInfo(
            job_id="job-full-003",
            status="completed",
            job_type="analysis",
            progress=1.0,
            result=AnalysisResult(
                duration_seconds=200.0,
                tempo_bpm=120.0,
                musical_key="C",
                musical_mode="major",
                key_confidence=0.9,
                loudness_db=-10.0,
            ),
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            [
                "audio",
                "analyze",
                "song_001",
                "--analysis-tier",
                "full",
                "--config",
                str(setup_db["config_path"]),
                "--wait",
            ],
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
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
            analysis_status="partial",
        )
        db_client.insert_recording(recording)

        mock_client = MagicMock()
        mock_client.submit_fast_analysis.return_value = JobInfo(
            job_id="job-fast-004",
            status="queued",
            job_type="fast_analyze",
            progress=0.0,
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            [
                "audio",
                "analyze",
                "song_001",
                "--analysis-tier",
                "fast",
                "--force",
                "--config",
                str(setup_db["config_path"]),
            ],
        )

        assert result.exit_code == 0
        mock_client.submit_fast_analysis.assert_called_once()

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_analyze_fast_wait_sets_partial(self, mock_client_cls, setup_db, monkeypatch):
        """Fast tier --wait sets analysis_status='partial', not 'completed'."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        db_client = setup_db["db_client"]
        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

        from stream_of_worship.admin.services.analysis import AnalysisResult

        mock_client = MagicMock()
        mock_client.submit_fast_analysis.return_value = JobInfo(
            job_id="job-fast-005",
            status="queued",
            job_type="fast_analyze",
            progress=0.0,
        )
        mock_client.wait_for_completion.return_value = JobInfo(
            job_id="job-fast-005",
            status="completed",
            job_type="fast_analyze",
            progress=1.0,
            result=AnalysisResult(
                duration_seconds=180.0,
                tempo_bpm=100.0,
                musical_key="D",
                musical_mode="minor",
                key_confidence=0.88,
                loudness_db=-12.0,
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
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
            analysis_status="completed",
        )
        db_client.insert_recording(recording)

        from stream_of_worship.admin.services.analysis import AnalysisResult

        mock_client = MagicMock()
        mock_client.submit_fast_analysis.return_value = JobInfo(
            job_id="job-fast-006",
            status="queued",
            job_type="fast_analyze",
            progress=0.0,
        )
        mock_client.wait_for_completion.return_value = JobInfo(
            job_id="job-fast-006",
            status="completed",
            job_type="fast_analyze",
            progress=1.0,
            result=AnalysisResult(
                duration_seconds=190.0,
                tempo_bpm=110.0,
                musical_key="E",
                musical_mode="major",
                key_confidence=0.92,
                loudness_db=-9.0,
            ),
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            [
                "audio",
                "analyze",
                "song_001",
                "--force",
                "--config",
                str(setup_db["config_path"]),
                "--wait",
            ],
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
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

        from stream_of_worship.admin.services.analysis import AnalysisResult

        mock_client = MagicMock()
        mock_client.submit_analysis.return_value = JobInfo(
            job_id="job-full-004",
            status="queued",
            job_type="analysis",
            progress=0.0,
        )
        mock_client.wait_for_completion.return_value = JobInfo(
            job_id="job-full-004",
            status="completed",
            job_type="analysis",
            progress=1.0,
            result=AnalysisResult(
                duration_seconds=210.0,
                tempo_bpm=130.0,
                musical_key="F",
                musical_mode="major",
                key_confidence=0.93,
                loudness_db=-7.0,
                beats=[1.0, 2.0],
                downbeats=[1.0],
                sections=[{"start": 0.0}],
                embeddings_shape=[1, 128],
            ),
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            [
                "audio",
                "analyze",
                "song_001",
                "--analysis-tier",
                "full",
                "--config",
                str(setup_db["config_path"]),
                "--wait",
            ],
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
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
            analysis_status="processing",
            analysis_job_id="existing-fast-job",
        )
        db_client.insert_recording(recording)

        mock_client = MagicMock()
        # Existing job is fast, but we request full
        mock_client.get_job.return_value = JobInfo(
            job_id="existing-fast-job",
            status="processing",
            job_type="fast_analyze",
            progress=0.0,
        )
        mock_client.submit_analysis.return_value = JobInfo(
            job_id="new-full-job",
            status="queued",
            job_type="analysis",
            progress=0.0,
        )
        mock_client.wait_for_completion.return_value = JobInfo(
            job_id="new-full-job",
            status="completed",
            job_type="analysis",
            progress=1.0,
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            [
                "audio",
                "analyze",
                "song_001",
                "--analysis-tier",
                "full",
                "--config",
                str(setup_db["config_path"]),
                "--wait",
            ],
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
        config_path.write_text("[database]\n")

        result = runner.invoke(app, ["audio", "status", "--config", str(config_path)])

        assert result.exit_code != 0

    @pytest.mark.integration
    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_status_with_job_id_success(self, mock_client_cls, setup_db, monkeypatch):
        """Displays job in Rich Panel."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        mock_client = MagicMock()
        mock_client.get_job.return_value = JobInfo(
            job_id="job-abc-123",
            status="completed",
            job_type="analysis",
            progress=1.0,
            stage="complete",
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
        mock_client.get_job.side_effect = AnalysisServiceError(
            "Authentication failed", status_code=401
        )
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
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
            analysis_status="completed",
            lrc_status="completed",
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
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/test/audio.mp3",
            analysis_status="pending",
            lrc_status="pending",
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
            app,
            ["audio", "status", "--config", str(config_path)],
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
            id="song_001",
            title="將天敞開",
            source_url="https://example.com/1",
            scraped_at="2024-01-01T00:00:00",
            composer="游智婷",
            album_name="敬拜讚美15",
        )
        client.insert_song(song)

        config_path = tmp_path / "config.toml"
        config_path.write_text(f"""[database]
url = "{postgres_url}"

[r2]
bucket = "test-bucket"
endpoint_url = "https://test.r2.dev"
region = "auto"
""")

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
            content_hash="old" * 24,
            hash_prefix="oldoldoldold",
            song_id="song_001",
            original_filename="old.mp3",
            file_size_bytes=1000,
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
            "id": "abc123",
            "title": "Test Video",
            "duration": 245,
            "webpage_url": "https://youtube.com/watch?v=abc123",
        }
        mock_yt.download.return_value = setup["tmp_path"] / "Test Video.mp3"
        mock_yt_class.return_value = mock_yt

        mp3_path = setup["tmp_path"] / "Test Video.mp3"
        mp3_path.write_bytes(b"fake audio")

        result = runner.invoke(
            app,
            [
                "audio",
                "download",
                "song_001",
                "--config",
                str(setup["config_path"]),
                "--yes",
                "--force",
            ],
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
            "id": "custom123",
            "title": "Custom Video",
            "duration": 245,
            "webpage_url": "https://youtube.com/watch?v=custom123",
        }
        mock_yt.download_by_url.return_value = setup["tmp_path"] / "Custom Video.mp3"
        mock_yt_class.return_value = mock_yt

        mp3_path = setup["tmp_path"] / "Custom Video.mp3"
        mp3_path.write_bytes(b"fake audio")

        result = runner.invoke(
            app,
            [
                "audio",
                "download",
                "song_001",
                "--config",
                str(setup["config_path"]),
                "--yes",
                "--url",
                "https://youtube.com/watch?v=custom123",
            ],
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
            "id": "long123",
            "title": "Long Video",
            "duration": 500,
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
            "id": "test123",
            "title": "Test Video",
            "duration": 300,
            "webpage_url": "https://youtube.com/watch?v=test123",
        }
        mock_yt.download.return_value = setup["tmp_path"] / "Test Video.mp3"
        mock_yt_class.return_value = mock_yt

        mp3_path = setup["tmp_path"] / "Test Video.mp3"
        mp3_path.write_bytes(b"fake audio")

        result = runner.invoke(
            app,
            [
                "audio",
                "download",
                "song_001",
                "--config",
                str(setup["config_path"]),
                "--yes",
                "--analyze",
            ],
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
            "id": "test456",
            "title": "Test Video",
            "duration": 300,
            "webpage_url": "https://youtube.com/watch?v=test456",
        }
        mock_yt.download.return_value = setup["tmp_path"] / "Test Video.mp3"
        mock_yt_class.return_value = mock_yt

        mp3_path = setup["tmp_path"] / "Test Video.mp3"
        mp3_path.write_bytes(b"fake audio")

        result = runner.invoke(
            app,
            [
                "audio",
                "download",
                "song_001",
                "--config",
                str(setup["config_path"]),
                "--yes",
                "--lrc",
            ],
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
        self,
        mock_submit_lrc,
        mock_submit_analysis,
        mock_yt_class,
        mock_r2_class,
        setup,
        monkeypatch,
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
            "id": "test789",
            "title": "Test Video",
            "duration": 300,
            "webpage_url": "https://youtube.com/watch?v=test789",
        }
        mock_yt.download.return_value = setup["tmp_path"] / "Test Video.mp3"
        mock_yt_class.return_value = mock_yt

        mp3_path = setup["tmp_path"] / "Test Video.mp3"
        mp3_path.write_bytes(b"fake audio")

        result = runner.invoke(
            app,
            [
                "audio",
                "download",
                "song_001",
                "--config",
                str(setup["config_path"]),
                "--yes",
                "--all",
            ],
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
            "id": "simple",
            "title": "Test Video",
            "duration": 300,
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


class TestAudioDownloadStdinBatch:
    """Tests for 'audio download --stdin' batch mode."""

    def test_no_args_no_stdin_errors(self):
        """No song_id and no --stdin → validation error before DB access."""
        result = runner.invoke(app, ["audio", "download"])
        assert result.exit_code == 1
        assert "Either provide a song_id argument or use --stdin flag" in result.output

    def test_song_id_and_stdin_mutually_exclusive(self):
        """Cannot pass both a positional song_id and --stdin."""
        result = runner.invoke(app, ["audio", "download", "song_001", "--stdin"])
        assert result.exit_code == 1
        assert "Cannot use both song_id argument and --stdin flag" in result.output

    def test_stdin_with_url_errors(self):
        """--url is not supported with --stdin (batch mode)."""
        result = runner.invoke(
            app,
            ["audio", "download", "--stdin", "--url", "https://youtube.com/watch?v=x"],
        )
        assert result.exit_code == 1
        assert "--url is not supported with --stdin" in result.output

    def test_stdin_backfill_lyrics_with_url_errors(self):
        """--url is rejected with --stdin --backfill-lyrics (single URL can't drive batch)."""
        result = runner.invoke(
            app,
            [
                "audio",
                "download",
                "--stdin",
                "--backfill-lyrics",
                "--url",
                "https://youtube.com/watch?v=x",
            ],
        )
        assert result.exit_code == 1
        assert "--url is not supported with --stdin (batch mode)" in result.output

    def test_stdin_empty_input_exits_zero(self):
        """Empty stdin → informative message and exit 0."""
        fake_config = MagicMock()
        fake_db = MagicMock()
        with (
            patch(
                "stream_of_worship.admin.commands.audio.AdminConfig.load",
                return_value=fake_config,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.get_db_client",
                return_value=fake_db,
            ),
        ):
            result = runner.invoke(app, ["audio", "download", "--stdin", "--yes"], input="")
        assert result.exit_code == 0
        assert "No song IDs provided via stdin" in result.output

    def test_stdin_dry_run_shows_preview(self):
        """--dry-run prints preview summary without invoking downloads."""
        fake_config = MagicMock()
        song1 = MagicMock(title="Song One")
        song2 = MagicMock(title="Song Two")
        fake_db = MagicMock()
        fake_db.get_song.side_effect = lambda sid: {
            "song_001": song1,
            "song_002": song2,
        }.get(sid)
        fake_db.list_active_recordings_by_song.return_value = []
        with (
            patch(
                "stream_of_worship.admin.commands.audio.AdminConfig.load",
                return_value=fake_config,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.get_db_client",
                return_value=fake_db,
            ),
        ):
            result = runner.invoke(
                app,
                ["audio", "download", "--stdin", "--dry-run"],
                input="song_001\nsong_002\n",
                env=WIDE_ENV,
            )
        assert result.exit_code == 0
        assert "2 to download" in result.output
        assert "0 skipped (already present)" in result.output
        assert "Song One" in result.output
        assert "Song Two" in result.output

    def test_stdin_dry_run_songs_not_found_reported(self):
        """--dry-run reports unknown song IDs in the summary."""
        fake_config = MagicMock()
        song1 = MagicMock(title="Song One")
        fake_db = MagicMock()
        fake_db.get_song.side_effect = lambda sid: {"song_001": song1}.get(sid)
        fake_db.list_active_recordings_by_song.return_value = []
        with (
            patch(
                "stream_of_worship.admin.commands.audio.AdminConfig.load",
                return_value=fake_config,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.get_db_client",
                return_value=fake_db,
            ),
        ):
            result = runner.invoke(
                app,
                ["audio", "download", "--stdin", "--dry-run"],
                input="song_001\nunknown_id\n",
                env=WIDE_ENV,
            )
        assert result.exit_code == 0
        assert "1 to download" in result.output
        assert "1 not found" in result.output
        assert "unknown_id" in result.output

    def test_stdin_skips_already_present_without_force(self):
        """Songs with existing recordings are skipped (no --force)."""
        fake_config = MagicMock()
        song1 = MagicMock(title="Song One")
        song2 = MagicMock(title="Song Two")
        existing_recording = MagicMock(hash_prefix="abcabcabcabc")
        fake_db = MagicMock()
        fake_db.get_song.side_effect = lambda sid: {
            "song_001": song1,
            "song_002": song2,
        }.get(sid)
        fake_db.list_active_recordings_by_song.side_effect = lambda sid: (
            [existing_recording] if sid == "song_001" else []
        )
        with (
            patch(
                "stream_of_worship.admin.commands.audio.AdminConfig.load",
                return_value=fake_config,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.get_db_client",
                return_value=fake_db,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.import_youtube_audio_for_song"
            ) as mock_import,
        ):
            result = runner.invoke(
                app,
                ["audio", "download", "--stdin", "--yes"],
                input="song_001\nsong_002\n",
                env=WIDE_ENV,
            )
        assert result.exit_code == 0
        mock_import.assert_called_once()
        assert mock_import.call_args.kwargs.get("song_id") == "song_002"
        assert "Recording already exists" in result.output
        assert "1 downloaded" in result.output
        assert "1 skipped (already present)" in result.output

    def test_stdin_force_replaces_already_present(self):
        """--force in batch mode queues existing recordings for replacement."""
        fake_config = MagicMock()
        song1 = MagicMock(title="Song One")
        existing_recording = MagicMock(hash_prefix="abcabcabcabc")
        fake_db = MagicMock()
        fake_db.get_song.return_value = song1
        fake_db.list_active_recordings_by_song.return_value = [existing_recording]
        with (
            patch(
                "stream_of_worship.admin.commands.audio.AdminConfig.load",
                return_value=fake_config,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.get_db_client",
                return_value=fake_db,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.import_youtube_audio_for_song"
            ) as mock_import,
        ):
            result = runner.invoke(
                app,
                ["audio", "download", "--stdin", "--yes", "--force"],
                input="song_001\n",
                env=WIDE_ENV,
            )
        assert result.exit_code == 0
        mock_import.assert_called_once()
        assert mock_import.call_args.kwargs.get("force") is True
        assert "existing recordings will be replaced" in result.output
        assert "1 downloaded" in result.output

    def test_stdin_continues_after_failure(self):
        """A per-song typer.Exit(1) does not abort the remainder of the batch."""
        fake_config = MagicMock()
        song1 = MagicMock(title="Song One")
        song2 = MagicMock(title="Song Two")
        fake_db = MagicMock()
        fake_db.get_song.side_effect = lambda sid: {
            "song_001": song1,
            "song_002": song2,
        }.get(sid)
        fake_db.list_active_recordings_by_song.return_value = []
        with (
            patch(
                "stream_of_worship.admin.commands.audio.AdminConfig.load",
                return_value=fake_config,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.get_db_client",
                return_value=fake_db,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.import_youtube_audio_for_song"
            ) as mock_import,
        ):
            mock_import.side_effect = [typer.Exit(1), None]
            result = runner.invoke(
                app,
                ["audio", "download", "--stdin", "--yes"],
                input="song_001\nsong_002\n",
                env=WIDE_ENV,
            )
        assert result.exit_code == 0
        assert mock_import.call_count == 2
        assert "1 downloaded" in result.output
        assert "1 failed" in result.output

    def test_stdin_bypasses_per_song_video_confirmation(self):
        """Batch mode always passes skip_video_confirm=True to import helper."""
        fake_config = MagicMock()
        song1 = MagicMock(title="Song One")
        fake_db = MagicMock()
        fake_db.get_song.return_value = song1
        fake_db.list_active_recordings_by_song.return_value = []
        with (
            patch(
                "stream_of_worship.admin.commands.audio.AdminConfig.load",
                return_value=fake_config,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.get_db_client",
                return_value=fake_db,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.import_youtube_audio_for_song"
            ) as mock_import,
            patch(
                "stream_of_worship.admin.commands.audio._prompt_confirmation",
                return_value=True,
            ),
        ):
            result = runner.invoke(
                app,
                ["audio", "download", "--stdin"],  # no --yes — batch-level confirm still needed
                input="song_001\n",
                env=WIDE_ENV,
            )
        assert result.exit_code == 0
        mock_import.assert_called_once()
        assert mock_import.call_args.kwargs.get("skip_video_confirm") is True

    def test_stdin_batch_confirmation_rejection_cancels(self):
        """Rejecting the batch confirmation prompt cancels the run."""
        fake_config = MagicMock()
        song1 = MagicMock(title="Song One")
        fake_db = MagicMock()
        fake_db.get_song.return_value = song1
        fake_db.list_active_recordings_by_song.return_value = []
        with (
            patch(
                "stream_of_worship.admin.commands.audio.AdminConfig.load",
                return_value=fake_config,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.get_db_client",
                return_value=fake_db,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.import_youtube_audio_for_song"
            ) as mock_import,
            patch(
                "stream_of_worship.admin.commands.audio._prompt_confirmation",
                return_value=False,
            ),
        ):
            result = runner.invoke(
                app,
                ["audio", "download", "--stdin"],
                input="song_001\n",
                env=WIDE_ENV,
            )
        assert result.exit_code == 0
        assert "Batch download cancelled" in result.output
        mock_import.assert_not_called()

    def test_stdin_backfill_lyrics_routes_to_batch_helper(self):
        """--stdin --backfill-lyrics invokes _backfill_lyrics_batch (not the download path)."""
        fake_config = MagicMock()
        fake_db = MagicMock()
        with (
            patch(
                "stream_of_worship.admin.commands.audio.AdminConfig.load",
                return_value=fake_config,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.get_db_client",
                return_value=fake_db,
            ),
            patch("stream_of_worship.admin.commands.audio._backfill_lyrics_batch") as mock_batch,
            patch(
                "stream_of_worship.admin.commands.audio._backfill_lyrics_for_song"
            ) as mock_single,
            patch(
                "stream_of_worship.admin.commands.audio.import_youtube_audio_for_song"
            ) as mock_import,
        ):
            result = runner.invoke(
                app,
                ["audio", "download", "--stdin", "--backfill-lyrics", "--yes"],
                input="song_001\nsong_002\n",
                env=WIDE_ENV,
            )
        assert result.exit_code == 0
        mock_batch.assert_called_once()
        assert mock_batch.call_args.kwargs.get("skip_confirm") is True
        mock_single.assert_not_called()
        mock_import.assert_not_called()

    def test_stdin_backfill_lyrics_empty_input_exits_zero(self):
        """Empty stdin with --backfill-lyrics → informative message and exit 0."""
        fake_config = MagicMock()
        fake_db = MagicMock()
        with (
            patch(
                "stream_of_worship.admin.commands.audio.AdminConfig.load",
                return_value=fake_config,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.get_db_client",
                return_value=fake_db,
            ),
            patch(
                "stream_of_worship.admin.commands.audio._backfill_lyrics_for_song"
            ) as mock_single,
        ):
            result = runner.invoke(
                app,
                ["audio", "download", "--stdin", "--backfill-lyrics", "--yes"],
                input="",
                env=WIDE_ENV,
            )
        assert result.exit_code == 0
        assert "No song IDs provided via stdin" in result.output
        mock_single.assert_not_called()

    def test_backfill_lyrics_single_song_ignored_stdin_flag(self):
        """Single-song --backfill-lyrics (no --stdin) ignores batch helper."""
        fake_config = MagicMock()
        fake_db = MagicMock()
        with (
            patch(
                "stream_of_worship.admin.commands.audio.AdminConfig.load",
                return_value=fake_config,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.get_db_client",
                return_value=fake_db,
            ),
            patch("stream_of_worship.admin.commands.audio._backfill_lyrics_batch") as mock_batch,
            patch(
                "stream_of_worship.admin.commands.audio._backfill_lyrics_for_song"
            ) as mock_single,
        ):
            runner.invoke(
                app,
                ["audio", "download", "song_001", "--backfill-lyrics", "--yes"],
                env=WIDE_ENV,
            )
        mock_single.assert_called_once()
        assert mock_single.call_args.kwargs.get("song_id") == "song_001"
        mock_batch.assert_not_called()

    def test_stdin_backfill_lyrics_confirmed_runs_per_song(self):
        """With --yes, _backfill_lyrics_batch calls _backfill_lyrics_for_song per id."""
        fake_config = MagicMock()
        fake_db = MagicMock()
        with (
            patch(
                "stream_of_worship.admin.commands.audio.AdminConfig.load",
                return_value=fake_config,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.get_db_client",
                return_value=fake_db,
            ),
            patch(
                "stream_of_worship.admin.commands.audio._backfill_lyrics_for_song",
                return_value=True,
            ) as mock_single,
        ):
            result = runner.invoke(
                app,
                ["audio", "download", "--stdin", "--backfill-lyrics", "--yes"],
                input="song_001\nsong_002\nsong_003\n",
                env=WIDE_ENV,
            )
        assert result.exit_code == 0
        assert mock_single.call_count == 3
        called_ids = [c.kwargs.get("song_id") for c in mock_single.call_args_list]
        assert called_ids == ["song_001", "song_002", "song_003"]
        assert "3 backfilled" in result.output

    def test_stdin_backfill_lyrics_continues_after_failure(self):
        """A per-song typer.Exit(1) is caught; batch continues with summary."""
        fake_config = MagicMock()
        fake_db = MagicMock()
        with (
            patch(
                "stream_of_worship.admin.commands.audio.AdminConfig.load",
                return_value=fake_config,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.get_db_client",
                return_value=fake_db,
            ),
            patch(
                "stream_of_worship.admin.commands.audio._backfill_lyrics_for_song"
            ) as mock_single,
        ):
            mock_single.side_effect = [typer.Exit(1), True]
            result = runner.invoke(
                app,
                ["audio", "download", "--stdin", "--backfill-lyrics", "--yes"],
                input="song_001\nsong_002\n",
                env=WIDE_ENV,
            )
        assert result.exit_code == 0
        assert mock_single.call_count == 2
        assert "1 backfilled" in result.output
        assert "1 failed" in result.output

    def test_stdin_backfill_lyrics_confirmation_rejection_cancels(self):
        """Rejecting the batch backfill prompt cancels the run before any work."""
        fake_config = MagicMock()
        fake_db = MagicMock()
        with (
            patch(
                "stream_of_worship.admin.commands.audio.AdminConfig.load",
                return_value=fake_config,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.get_db_client",
                return_value=fake_db,
            ),
            patch(
                "stream_of_worship.admin.commands.audio._backfill_lyrics_for_song"
            ) as mock_single,
            patch(
                "stream_of_worship.admin.commands.audio._prompt_confirmation",
                return_value=False,
            ),
        ):
            result = runner.invoke(
                app,
                ["audio", "download", "--stdin", "--backfill-lyrics"],
                input="song_001\n",
                env=WIDE_ENV,
            )
        assert result.exit_code == 0
        assert "Batch backfill cancelled" in result.output
        mock_single.assert_not_called()


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
            id="song_001",
            title="測試歌曲",
            source_url="https://example.com/1",
            scraped_at="2024-01-01T00:00:00",
        )
        client.insert_song(song)

        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000000,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://bucket/aaaaaaaaaaaa/audio.mp3",
        )
        client.insert_recording(recording)

        config_path = tmp_path / "config.toml"
        config_path.write_text(f"""[database]
url = "{postgres_url}"

[r2]
bucket = "test-bucket"
endpoint_url = "https://test.r2.dev"
region = "auto"
""")

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
            id="song_001",
            title="測試",
            source_url="https://example.com",
            scraped_at="2024-01-01T00:00:00",
        )
        client.insert_song(song)

        config_path = tmp_path / "config.toml"
        config_path.write_text(f"""[database]
url = "{postgres_url}"

[r2]
bucket = "test-bucket"
endpoint_url = "https://test.r2.dev"
region = "auto"
""")

        result = runner.invoke(
            app,
            ["audio", "delete", "song_001", "--config", str(config_path), "--yes"],
        )

        assert result.exit_code == 1
        assert "No recording found" in result.output

        _drop_all_tables(make_test_provider)


class TestSubmitComponentAnalysisJobSkipBeatCache:
    """Helper-level tests for --skip-beat-cache flag threading.

    Verifies that ``_submit_component_analysis_job`` forwards ``skip_beat_cache``
    to ``client.submit_component_analysis``, and that the batch-mode invocation
    sites pass the command's flag value.
    """

    def _make_recording(self) -> Recording:
        return Recording(
            content_hash="a" * 64,
            hash_prefix="a" * 12,
            original_filename="test.mp3",
            file_size_bytes=1024,
            imported_at="2026-01-01T00:00:00Z",
            r2_audio_url="s3://bucket/test.mp3",
            analysis_status="completed",
            lrc_status="pending",
        )

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    @patch("stream_of_worship.admin.commands.audio.AdminConfig.load")
    def test_skip_beat_cache_forwarded_to_client(self, mock_config_load, mock_client_cls):
        """_submit_component_analysis_job(skip_beat_cache=True) forwards to submit."""
        from stream_of_worship.admin.commands.audio import _submit_component_analysis_job

        mock_config = MagicMock()
        mock_config.analysis_url = "http://localhost:8000"
        mock_config_load.return_value = mock_config

        mock_client = MagicMock()
        mock_client.get_cached_component_result.return_value = None
        mock_client.submit_component_analysis.return_value = JobInfo(
            job_id="job-1", status="queued", job_type="component_analysis"
        )
        mock_client.wait_for_completion.return_value = MagicMock(
            status="completed",
            result=MagicMock(components=[], component_source="none"),
        )
        mock_client_cls.return_value = mock_client

        db_client = MagicMock()
        console = MagicMock()
        recording = self._make_recording()

        _submit_component_analysis_job(
            recording,
            "song_001",
            "http://localhost:8000",
            db_client,
            console,
            config=mock_config,
            force=True,
            wait=True,
            snap_to_downbeat=True,
            skip_beat_cache=True,
        )

        # Verify skip_beat_cache=True was forwarded.
        call_kwargs = mock_client.submit_component_analysis.call_args.kwargs
        assert call_kwargs["skip_beat_cache"] is True

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    @patch("stream_of_worship.admin.commands.audio.AdminConfig.load")
    def test_default_skip_beat_cache_is_false(self, mock_config_load, mock_client_cls):
        """_submit_component_analysis_job default forwards skip_beat_cache=False."""
        from stream_of_worship.admin.commands.audio import _submit_component_analysis_job

        mock_config = MagicMock()
        mock_config.analysis_url = "http://localhost:8000"
        mock_config_load.return_value = mock_config

        mock_client = MagicMock()
        mock_client.get_cached_component_result.return_value = None
        mock_client.submit_component_analysis.return_value = JobInfo(
            job_id="job-1", status="queued", job_type="component_analysis"
        )
        mock_client.wait_for_completion.return_value = MagicMock(
            status="completed",
            result=MagicMock(components=[], component_source="none"),
        )
        mock_client_cls.return_value = mock_client

        db_client = MagicMock()
        console = MagicMock()
        recording = self._make_recording()

        _submit_component_analysis_job(
            recording,
            "song_001",
            "http://localhost:8000",
            db_client,
            console,
            config=mock_config,
            force=True,
            wait=True,
            snap_to_downbeat=True,
        )

        call_kwargs = mock_client.submit_component_analysis.call_args.kwargs
        assert call_kwargs["skip_beat_cache"] is False


class TestSegmentationModeFlag:
    """CLI-level tests for --segmentation-mode flag (v7 spec)."""

    def test_segmentation_mode_without_force_exits_2(self):
        """--segmentation-mode without --force → exit code 2."""
        result = runner.invoke(
            app,
            ["audio", "components", "song_001", "--segmentation-mode", "llm"],
            env=WIDE_ENV,
        )
        assert result.exit_code == 2
        assert "--segmentation-mode requires --force" in result.output

    def test_segmentation_mode_invalid_value_rejected(self):
        """Invalid --segmentation-mode value → validation error."""
        result = runner.invoke(
            app,
            [
                "audio",
                "components",
                "song_001",
                "--segmentation-mode",
                "invalid",
                "--force",
            ],
            env=WIDE_ENV,
        )
        assert result.exit_code != 0
        assert "must be one of" in result.output

    def test_segmentation_mode_forwarded_to_helper(self):
        """--segmentation-mode llm --force → forwarded to _submit_component_analysis_job."""
        captured = {}

        def fake_submit(*args, **kwargs):
            captured.update(kwargs)
            return []

        fake_recording = MagicMock()
        fake_recording.r2_audio_url = "https://example.com/audio.mp3"
        fake_recording.has_full_analysis = True
        fake_recording.has_lrc = True

        fake_db_client = MagicMock()
        fake_db_client.get_recording_by_song_id.return_value = fake_recording

        fake_config = MagicMock()
        fake_config.analysis_url = "https://analysis.example"

        with (
            patch(
                "stream_of_worship.admin.commands.audio.get_db_client",
                return_value=fake_db_client,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.AdminConfig.load",
                return_value=fake_config,
            ),
            patch(
                "stream_of_worship.admin.commands.audio._submit_component_analysis_job",
                side_effect=fake_submit,
            ),
        ):
            result = runner.invoke(
                app,
                [
                    "audio",
                    "components",
                    "song_001",
                    "--segmentation-mode",
                    "llm",
                    "--force",
                ],
                env=WIDE_ENV,
            )

        assert result.exit_code == 0
        assert captured.get("segmentation_mode") == "llm"

    def test_segmentation_mode_none_by_default(self):
        """Without --segmentation-mode, helper receives segmentation_mode=None."""
        captured = {}

        def fake_submit(*args, **kwargs):
            captured.update(kwargs)
            return []

        fake_recording = MagicMock()
        fake_recording.r2_audio_url = "https://example.com/audio.mp3"
        fake_recording.has_full_analysis = True
        fake_recording.has_lrc = True

        fake_db_client = MagicMock()
        fake_db_client.get_recording_by_song_id.return_value = fake_recording

        fake_config = MagicMock()
        fake_config.analysis_url = "https://analysis.example"

        with (
            patch(
                "stream_of_worship.admin.commands.audio.get_db_client",
                return_value=fake_db_client,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.AdminConfig.load",
                return_value=fake_config,
            ),
            patch(
                "stream_of_worship.admin.commands.audio._submit_component_analysis_job",
                side_effect=fake_submit,
            ),
        ):
            result = runner.invoke(
                app,
                ["audio", "components", "song_001", "--force"],
                env=WIDE_ENV,
            )

        assert result.exit_code == 0
        assert captured.get("segmentation_mode") is None


class TestSegmentationModeEchoVerification:
    """Helper-level tests for echo verification in _submit_component_analysis_job."""

    def _make_recording(self) -> Recording:
        return Recording(
            content_hash="a" * 64,
            hash_prefix="a" * 12,
            original_filename="test.mp3",
            file_size_bytes=1024,
            imported_at="2026-01-01T00:00:00Z",
            r2_audio_url="s3://bucket/test.mp3",
            analysis_status="completed",
            lrc_status="completed",
        )

    @patch("stream_of_worship.admin.commands.audio.R2Client")
    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    @patch("stream_of_worship.admin.commands.audio.AdminConfig.load")
    def test_echo_mismatch_returns_none(self, mock_config_load, mock_client_cls, mock_r2_cls):
        """Echo mismatch (resolved != requested) → returns None, does NOT persist."""
        from stream_of_worship.admin.commands.audio import _submit_component_analysis_job

        mock_config = MagicMock()
        mock_config.analysis_url = "http://localhost:8000"
        mock_config_load.return_value = mock_config

        mock_r2 = MagicMock()
        mock_r2.download_lrc_content.return_value = "[00:00.00]test line"
        mock_r2_cls.return_value = mock_r2

        mock_client = MagicMock()
        mock_client.get_cached_component_result.return_value = None
        mock_client.submit_component_analysis.return_value = JobInfo(
            job_id="job-1", status="queued", job_type="component_analysis"
        )
        # Backend echoed None (old backend that doesn't support segmentation_mode)
        mock_client.wait_for_completion.return_value = MagicMock(
            status="completed",
            result=MagicMock(
                components=[MagicMock()],
                component_source="llm_segmentation",
                segmentation_mode_resolved=None,
            ),
        )
        mock_client_cls.return_value = mock_client

        db_client = MagicMock()
        console = MagicMock()
        recording = self._make_recording()

        result = _submit_component_analysis_job(
            recording,
            "song_001",
            "http://localhost:8000",
            db_client,
            console,
            config=mock_config,
            force=True,
            wait=True,
            segmentation_mode="llm",
        )

        assert result is None
        # Verify warning was printed
        print_calls = [str(call) for call in console.print.call_args_list]
        assert any("WARNING" in c for c in print_calls)

    @patch("stream_of_worship.admin.commands.audio.R2Client")
    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    @patch("stream_of_worship.admin.commands.audio.AdminConfig.load")
    def test_echo_match_proceeds(self, mock_config_load, mock_client_cls, mock_r2_cls):
        """Echo match (resolved == requested) → proceeds normally."""
        from stream_of_worship.admin.commands.audio import _submit_component_analysis_job

        mock_config = MagicMock()
        mock_config.analysis_url = "http://localhost:8000"
        mock_config_load.return_value = mock_config

        mock_r2 = MagicMock()
        mock_r2.download_lrc_content.return_value = "[00:00.00]test line"
        mock_r2_cls.return_value = mock_r2

        mock_component = MagicMock()
        mock_component.to_dict.return_value = {"type": "chorus"}

        mock_client = MagicMock()
        mock_client.get_cached_component_result.return_value = None
        mock_client.submit_component_analysis.return_value = JobInfo(
            job_id="job-1", status="queued", job_type="component_analysis"
        )
        mock_client.wait_for_completion.return_value = MagicMock(
            status="completed",
            result=MagicMock(
                components=[mock_component],
                component_source="llm_segmentation",
                segmentation_mode_resolved="llm",
            ),
        )
        mock_client_cls.return_value = mock_client

        db_client = MagicMock()
        console = MagicMock()
        recording = self._make_recording()

        result = _submit_component_analysis_job(
            recording,
            "song_001",
            "http://localhost:8000",
            db_client,
            console,
            config=mock_config,
            force=True,
            wait=True,
            segmentation_mode="llm",
        )

        assert result is not None
        assert len(result) == 1

    @patch("stream_of_worship.admin.commands.audio.R2Client")
    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    @patch("stream_of_worship.admin.commands.audio.AdminConfig.load")
    def test_no_segmentation_mode_skips_echo_check(
        self, mock_config_load, mock_client_cls, mock_r2_cls
    ):
        """When segmentation_mode=None, echo check is skipped entirely."""
        from stream_of_worship.admin.commands.audio import _submit_component_analysis_job

        mock_config = MagicMock()
        mock_config.analysis_url = "http://localhost:8000"
        mock_config_load.return_value = mock_config

        mock_r2 = MagicMock()
        mock_r2.download_lrc_content.return_value = "[00:00.00]test line"
        mock_r2_cls.return_value = mock_r2

        mock_component = MagicMock()
        mock_component.to_dict.return_value = {"type": "chorus"}

        mock_client = MagicMock()
        mock_client.get_cached_component_result.return_value = None
        mock_client.submit_component_analysis.return_value = JobInfo(
            job_id="job-1", status="queued", job_type="component_analysis"
        )
        # Even with resolved=None, should proceed because segmentation_mode=None
        mock_client.wait_for_completion.return_value = MagicMock(
            status="completed",
            result=MagicMock(
                components=[mock_component],
                component_source="allin1_sections",
                segmentation_mode_resolved=None,
            ),
        )
        mock_client_cls.return_value = mock_client

        db_client = MagicMock()
        console = MagicMock()
        recording = self._make_recording()

        result = _submit_component_analysis_job(
            recording,
            "song_001",
            "http://localhost:8000",
            db_client,
            console,
            config=mock_config,
            force=True,
            wait=True,
        )

        assert result is not None


class TestSegmentationModeBatchBanner:
    """Batch banner printed when --stdin + --segmentation-mode both set."""

    def test_batch_banner_printed(self):
        """--stdin + --segmentation-mode → banner printed to stderr."""
        fake_recording = MagicMock()
        fake_recording.r2_audio_url = "https://example.com/audio.mp3"
        fake_recording.has_full_analysis = True
        fake_recording.has_lrc = True

        fake_db_client = MagicMock()
        fake_db_client.get_recording_by_song_id.return_value = fake_recording

        fake_config = MagicMock()
        fake_config.analysis_url = "https://analysis.example"

        with (
            patch(
                "stream_of_worship.admin.commands.audio.get_db_client",
                return_value=fake_db_client,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.AdminConfig.load",
                return_value=fake_config,
            ),
            patch(
                "stream_of_worship.admin.commands.audio._submit_component_analysis_job",
                return_value=[],
            ),
        ):
            result = runner.invoke(
                app,
                [
                    "audio",
                    "components",
                    "--stdin",
                    "--segmentation-mode",
                    "repetition",
                    "--force",
                ],
                input="song_001\n",
                env=WIDE_ENV,
            )

        assert result.exit_code == 0
        assert "NO-FALLBACK CONTRACT" in result.output


class TestReviewComponentsCommand:
    """Tests for 'audio review-components' command."""

    def test_review_components_without_config(self):
        """Fails cleanly when no config file exists."""
        with patch("stream_of_worship.admin.config.get_config_path") as mock_path:
            mock_path.side_effect = FileNotFoundError("No config")
            result = runner.invoke(app, ["audio", "review-components", "song_001"])
        assert result.exit_code == 1
        assert "Config file not found" in result.output

    def test_review_components_without_database(self, tmp_path, monkeypatch):
        """Fails when the database url is not configured."""
        monkeypatch.delenv("SOW_DATABASE_URL", raising=False)
        config_path = tmp_path / "config.toml"
        config_path.write_text("[database]\n")
        result = runner.invoke(
            app,
            ["audio", "review-components", "song_001", "--config", str(config_path)],
            env={"COLUMNS": "200"},
        )
        assert result.exit_code != 0

    @pytest.mark.integration
    def test_review_components_unknown_song(self, setup_db):
        """Rejects unknown song_id with a warning."""
        config_path = setup_db["config_path"]
        result = runner.invoke(
            app,
            ["audio", "review-components", "nonexistent_song", "--config", str(config_path)],
            env=WIDE_ENV,
        )
        assert result.exit_code == 1
        assert "No song found" in result.output

    @pytest.mark.integration
    def test_review_components_no_components(self, setup_db, tmp_path):
        """Warns and skips song with no component analysis rows."""
        from stream_of_worship.admin.db.models import Recording

        db_client = setup_db["db_client"]
        song = setup_db["song"]
        recording = Recording(
            content_hash="d" * 64,
            hash_prefix="dddddddddddd",
            song_id=song.id,
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-01T00:00:00",
        )
        db_client.insert_recording(recording)
        config_path = setup_db["config_path"]

        with patch("stream_of_worship.admin.commands.audio.R2Client") as mock_r2_cls:
            mock_r2_cls.return_value = MagicMock()
            result = runner.invoke(
                app,
                [
                    "audio",
                    "review-components",
                    song.id,
                    "--config",
                    str(config_path),
                ],
                env=WIDE_ENV,
            )
        assert result.exit_code == 1
        assert "No component analysis" in result.output

    @pytest.mark.integration
    def test_review_components_launches_app(self, setup_db, tmp_path):
        """Launches the editor app when at least one valid song exists."""
        from stream_of_worship.admin.db.models import Recording, SongComponent

        db_client = setup_db["db_client"]
        song = setup_db["song"]
        recording = Recording(
            content_hash="e" * 64,
            hash_prefix="eeeeeeeeeeee",
            song_id=song.id,
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-01T00:00:00",
        )
        db_client.insert_recording(recording)

        entry = SongComponent(
            song_id=song.id,
            content_hash=recording.content_hash,
            component_type="chorus",
            occurrence_index=1,
            role="entry",
            start_time=10.0,
            end_time=20.0,
            theme="讚美",
        )
        exit_comp = SongComponent(
            song_id=song.id,
            content_hash=recording.content_hash,
            component_type="chorus",
            occurrence_index=1,
            role="exit",
            start_time=30.0,
            end_time=40.0,
            theme="感恩",
        )
        db_client.upsert_song_components(song.id, recording.content_hash, [entry, exit_comp])

        config_path = setup_db["config_path"]

        with (
            patch("stream_of_worship.admin.commands.audio.R2Client") as mock_r2_cls,
            patch(
                "stream_of_worship.admin.component_editor.app.ComponentEditorApp"
            ) as mock_app_cls,
        ):
            mock_r2_cls.return_value = MagicMock()
            mock_app_cls.return_value = MagicMock()
            result = runner.invoke(
                app,
                [
                    "audio",
                    "review-components",
                    song.id,
                    "--config",
                    str(config_path),
                ],
                env=WIDE_ENV,
            )
        assert result.exit_code == 0
        assert mock_app_cls.return_value.run.called


def test_compute_all_fields_does_not_set_all_components():
    """--compute-all-fields must NOT set all_components in the payload options."""
    captured = {}

    def fake_submit(*args, **kwargs):
        captured.update(kwargs)
        return []

    fake_recording = MagicMock()
    fake_recording.r2_audio_url = "https://example.com/audio.mp3"
    fake_recording.has_full_analysis = True
    fake_recording.has_lrc = True

    fake_db_client = MagicMock()
    fake_db_client.get_recording_by_song_id.return_value = fake_recording

    fake_config = MagicMock()
    fake_config.analysis_url = "https://analysis.example"

    with (
        patch(
            "stream_of_worship.admin.commands.audio.get_db_client",
            return_value=fake_db_client,
        ),
        patch(
            "stream_of_worship.admin.commands.audio.AdminConfig.load",
            return_value=fake_config,
        ),
        patch(
            "stream_of_worship.admin.commands.audio._submit_component_analysis_job",
            side_effect=fake_submit,
        ),
    ):
        result = runner.invoke(
            app,
            ["audio", "components", "song_001", "--compute-all-fields"],
            env=WIDE_ENV,
        )

    assert result.exit_code == 0
    assert captured["all_components"] is False
    assert captured["classify_theme"] is True
    assert captured["classify_vocal_posture"] is True
    assert captured["snap_to_downbeat"] is True
    assert captured["energy_aware_roles"] is True


class TestLlmLyricsFlags:
    """Tests for --llm/--no-llm flag wiring on audio download and backfill."""

    def test_backfill_lyrics_no_llm_passes_use_llm_false(self):
        """--backfill-lyrics --no-llm forwards use_llm=False to _backfill_lyrics_for_song."""
        fake_config = MagicMock()
        fake_db = MagicMock()
        with (
            patch(
                "stream_of_worship.admin.commands.audio.AdminConfig.load",
                return_value=fake_config,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.get_db_client",
                return_value=fake_db,
            ),
            patch(
                "stream_of_worship.admin.commands.audio._backfill_lyrics_for_song",
                return_value=True,
            ) as mock_single,
        ):
            result = runner.invoke(
                app,
                ["audio", "download", "song_001", "--backfill-lyrics", "--no-llm", "--yes"],
                env=WIDE_ENV,
            )
        assert result.exit_code == 0
        mock_single.assert_called_once()
        assert mock_single.call_args.kwargs.get("use_llm") is False

    def test_backfill_lyrics_llm_default_passes_use_llm_true(self):
        """--backfill-lyrics (default) forwards use_llm=True to _backfill_lyrics_for_song."""
        fake_config = MagicMock()
        fake_db = MagicMock()
        with (
            patch(
                "stream_of_worship.admin.commands.audio.AdminConfig.load",
                return_value=fake_config,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.get_db_client",
                return_value=fake_db,
            ),
            patch(
                "stream_of_worship.admin.commands.audio._backfill_lyrics_for_song",
                return_value=True,
            ) as mock_single,
        ):
            result = runner.invoke(
                app,
                ["audio", "download", "song_001", "--backfill-lyrics", "--yes"],
                env=WIDE_ENV,
            )
        assert result.exit_code == 0
        mock_single.assert_called_once()
        assert mock_single.call_args.kwargs.get("use_llm") is True

    def test_backfill_lyrics_explicit_llm_passes_use_llm_true(self):
        """--backfill-lyrics --llm forwards use_llm=True to _backfill_lyrics_for_song."""
        fake_config = MagicMock()
        fake_db = MagicMock()
        with (
            patch(
                "stream_of_worship.admin.commands.audio.AdminConfig.load",
                return_value=fake_config,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.get_db_client",
                return_value=fake_db,
            ),
            patch(
                "stream_of_worship.admin.commands.audio._backfill_lyrics_for_song",
                return_value=True,
            ) as mock_single,
        ):
            result = runner.invoke(
                app,
                ["audio", "download", "song_001", "--backfill-lyrics", "--llm", "--yes"],
                env=WIDE_ENV,
            )
        assert result.exit_code == 0
        mock_single.assert_called_once()
        assert mock_single.call_args.kwargs.get("use_llm") is True

    def test_download_no_llm_passes_use_llm_false_to_import(self):
        """audio download --no-llm forwards use_llm=False to import_youtube_audio_for_song."""
        fake_config = MagicMock()
        fake_db = MagicMock()
        with (
            patch(
                "stream_of_worship.admin.commands.audio.AdminConfig.load",
                return_value=fake_config,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.get_db_client",
                return_value=fake_db,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.import_youtube_audio_for_song",
                return_value=MagicMock(),
            ) as mock_import,
        ):
            result = runner.invoke(
                app,
                ["audio", "download", "song_001", "--no-llm", "--yes"],
                env=WIDE_ENV,
            )
        assert result.exit_code == 0
        mock_import.assert_called_once()
        assert mock_import.call_args.kwargs.get("use_llm") is False

    def test_download_llm_default_passes_use_llm_true_to_import(self):
        """audio download (default) forwards use_llm=True to import_youtube_audio_for_song."""
        fake_config = MagicMock()
        fake_db = MagicMock()
        with (
            patch(
                "stream_of_worship.admin.commands.audio.AdminConfig.load",
                return_value=fake_config,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.get_db_client",
                return_value=fake_db,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.import_youtube_audio_for_song",
                return_value=MagicMock(),
            ) as mock_import,
        ):
            result = runner.invoke(
                app,
                ["audio", "download", "song_001", "--yes"],
                env=WIDE_ENV,
            )
        assert result.exit_code == 0
        mock_import.assert_called_once()
        assert mock_import.call_args.kwargs.get("use_llm") is True

    def test_backfill_lyrics_llm_no_api_key_hard_fails(self, monkeypatch):
        """--backfill-lyrics (default) with SOW_LLM_API_KEY unset → exit 1, red error."""
        monkeypatch.delenv("SOW_LLM_API_KEY", raising=False)
        monkeypatch.delenv("SOW_LLM_MODEL", raising=False)

        fake_config = MagicMock()
        fake_db = MagicMock()
        fake_recording = MagicMock()
        fake_recording.youtube_url = "https://youtube.com/watch?v=test"
        fake_recording.hash_prefix = "testhash"
        fake_db.get_recording_by_song_id.return_value = fake_recording
        fake_db.get_song.return_value = MagicMock(title="Test Song")

        fake_metadata = MagicMock()
        fake_metadata.description = "[Verse]\nLine 1\nLine 2"

        with (
            patch(
                "stream_of_worship.admin.commands.audio.AdminConfig.load",
                return_value=fake_config,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.get_db_client",
                return_value=fake_db,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.extract_video_metadata",
                return_value=fake_metadata,
            ),
        ):
            result = runner.invoke(
                app,
                ["audio", "download", "song_001", "--backfill-lyrics", "--yes"],
                env=WIDE_ENV,
            )

        assert result.exit_code == 1
        assert "LLM lyrics extraction failed" in result.output
        assert "--no-llm" in result.output

    def test_backfill_lyrics_no_llm_does_not_hard_fail(self, monkeypatch):
        """--backfill-lyrics --no-llm with no API key → heuristic path, exit 0."""
        monkeypatch.delenv("SOW_LLM_API_KEY", raising=False)
        monkeypatch.delenv("SOW_LLM_MODEL", raising=False)

        fake_config = MagicMock()
        fake_db = MagicMock()
        fake_recording = MagicMock()
        fake_recording.youtube_url = "https://youtube.com/watch?v=test"
        fake_recording.hash_prefix = "testhash"
        fake_db.get_recording_by_song_id.return_value = fake_recording
        fake_db.get_song.return_value = MagicMock(title="Test Song")

        fake_metadata = MagicMock()
        fake_metadata.description = "[Verse]\nLine 1\nLine 2"

        with (
            patch(
                "stream_of_worship.admin.commands.audio.AdminConfig.load",
                return_value=fake_config,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.get_db_client",
                return_value=fake_db,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.extract_video_metadata",
                return_value=fake_metadata,
            ),
        ):
            result = runner.invoke(
                app,
                ["audio", "download", "song_001", "--backfill-lyrics", "--no-llm", "--yes"],
                env=WIDE_ENV,
            )

        assert result.exit_code == 0
        assert "Backfilled structured lyrics" in result.output

    def test_backfill_lyrics_llm_invoke_failure_hard_fails(self, monkeypatch):
        """--backfill-lyrics --llm with API key set but LLM .invoke() raises → exit 1."""
        monkeypatch.setenv("SOW_LLM_API_KEY", "fake-key")
        monkeypatch.setenv("SOW_LLM_MODEL", "fake-model")

        fake_config = MagicMock()
        fake_db = MagicMock()
        fake_recording = MagicMock()
        fake_recording.youtube_url = "https://youtube.com/watch?v=test"
        fake_recording.hash_prefix = "testhash"
        fake_db.get_recording_by_song_id.return_value = fake_recording
        fake_db.get_song.return_value = MagicMock(title="Test Song")

        fake_metadata = MagicMock()
        fake_metadata.description = "[Verse]\nLine 1\nLine 2"

        with (
            patch(
                "stream_of_worship.admin.commands.audio.AdminConfig.load",
                return_value=fake_config,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.get_db_client",
                return_value=fake_db,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.extract_video_metadata",
                return_value=fake_metadata,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.parse_structured_lyrics_smart"
            ) as mock_smart,
        ):
            mock_smart.side_effect = RuntimeError("LLM network error")
            result = runner.invoke(
                app,
                ["audio", "download", "song_001", "--backfill-lyrics", "--yes"],
                env=WIDE_ENV,
            )

        assert result.exit_code == 1
        assert "LLM lyrics extraction failed" in result.output
        assert "--no-llm" in result.output


class TestStructuredLyricsSource:
    """Tests for --structured-lyrics-source wiring and selection logic."""

    def test_download_default_forwards_auto_source(self):
        """audio download (default) forwards structured_lyrics_source='auto'."""
        fake_config = MagicMock()
        fake_db = MagicMock()
        with (
            patch(
                "stream_of_worship.admin.commands.audio.AdminConfig.load",
                return_value=fake_config,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.get_db_client",
                return_value=fake_db,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.import_youtube_audio_for_song",
                return_value=MagicMock(),
            ) as mock_import,
        ):
            result = runner.invoke(
                app, ["audio", "download", "song_001", "--yes"], env=WIDE_ENV
            )
        assert result.exit_code == 0
        mock_import.assert_called_once()
        assert mock_import.call_args.kwargs.get("structured_lyrics_source") == "auto"

    def test_download_forwards_zanmei_source(self):
        """audio download --structured-lyrics-source zanmei forwards 'zanmei'."""
        fake_config = MagicMock()
        fake_db = MagicMock()
        with (
            patch(
                "stream_of_worship.admin.commands.audio.AdminConfig.load",
                return_value=fake_config,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.get_db_client",
                return_value=fake_db,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.import_youtube_audio_for_song",
                return_value=MagicMock(),
            ) as mock_import,
        ):
            result = runner.invoke(
                app,
                ["audio", "download", "song_001", "--structured-lyrics-source", "zanmei", "--yes"],
                env=WIDE_ENV,
            )
        assert result.exit_code == 0
        mock_import.assert_called_once()
        assert mock_import.call_args.kwargs.get("structured_lyrics_source") == "zanmei"

    def test_download_invalid_source_rejected(self):
        """--structured-lyrics-source bogus → exit 1 with an error message."""
        fake_config = MagicMock()
        fake_db = MagicMock()
        with (
            patch(
                "stream_of_worship.admin.commands.audio.AdminConfig.load",
                return_value=fake_config,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.get_db_client",
                return_value=fake_db,
            ),
        ):
            result = runner.invoke(
                app,
                ["audio", "download", "song_001", "--structured-lyrics-source", "bogus", "--yes"],
                env=WIDE_ENV,
            )
        assert result.exit_code == 1
        assert "structured-lyrics-source" in result.output
        assert "auto" in result.output

    def test_backfill_forwards_source_to_single(self):
        """--backfill-lyrics --structured-lyrics-source zanmei forwards source."""
        fake_config = MagicMock()
        fake_db = MagicMock()
        with (
            patch(
                "stream_of_worship.admin.commands.audio.AdminConfig.load",
                return_value=fake_config,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.get_db_client",
                return_value=fake_db,
            ),
            patch(
                "stream_of_worship.admin.commands.audio._backfill_lyrics_for_song",
                return_value=True,
            ) as mock_single,
        ):
            result = runner.invoke(
                app,
                [
                    "audio",
                    "download",
                    "song_001",
                    "--backfill-lyrics",
                    "--structured-lyrics-source",
                    "zanmei",
                    "--yes",
                ],
                env=WIDE_ENV,
            )
        assert result.exit_code == 0
        mock_single.assert_called_once()
        assert mock_single.call_args.kwargs.get("structured_lyrics_source") == "zanmei"


class TestFetchStructuredLyricsSelect:
    """Tests for the _fetch_structured_lyrics source-selection helper."""

    def _call(self, *, source, yt_desc=None, zanmei_text=None, use_llm=False):
        from stream_of_worship.admin.commands import audio as audio_mod

        fake_console = MagicMock()
        fake_meta = MagicMock()
        fake_meta.description = yt_desc

        def fake_extract(url):
            if yt_desc is None:
                raise RuntimeError("no metadata")
            return fake_meta

        def fake_zanmei(title, band=None):
            if zanmei_text is None:
                raise RuntimeError("zanmei failed")
            return zanmei_text

        with (
            patch.object(audio_mod, "extract_video_metadata", side_effect=fake_extract),
            patch.object(audio_mod, "fetch_structured_lyrics_from_zanmei", side_effect=fake_zanmei),
        ):
            return audio_mod._fetch_structured_lyrics(
                youtube_url="https://youtube.com/watch?v=x",
                song_title="祢就是唯一",
                band="赞美之泉",
                source=source,
                use_llm=use_llm,  # heuristic-only to avoid LLM env
                console=fake_console,
            )

    def test_youtube_only_uses_youtube(self):
        """source='youtube' returns YouTube sections, never calls zanmei."""
        raw, _json_str, source = self._call(
            source="youtube",
            yt_desc="[Verse]\nLine 1\nLine 2",
            zanmei_text="[Verse]\nZanmei Line",
        )
        assert source == "youtube"
        assert "[Verse]" in raw
        assert "Zanmei Line" not in raw

    def test_zanmei_only_uses_zanmei(self):
        """source='zanmei' returns zanmei lyrics, ignores YouTube."""
        raw, _json_str, source = self._call(
            source="zanmei",
            zanmei_text="[Chorus]\n祢就是唯一",
        )
        assert source == "zanmei"
        assert "[Chorus]" in raw

    def test_auto_prefers_youtube_when_sections_present(self):
        """source='auto' keeps YouTube when it has section-tagged lyrics."""
        raw, _json_str, source = self._call(
            source="auto",
            yt_desc="[Verse]\nLine 1",
            zanmei_text="[Chorus]\nZanmei",
        )
        assert source == "youtube"
        assert "Line 1" in raw

    def test_auto_falls_back_to_zanmei_when_youtube_empty(self):
        """source='auto' falls back to zanmei when YouTube has no sections."""
        raw, _json_str, source = self._call(
            source="auto",
            yt_desc="channel promo, no brackets",
            zanmei_text="[Verse]\n祢就是唯一",
        )
        assert source == "zanmei"
        assert "[Verse]" in raw

    def test_zanmei_source_failure_returns_none(self):
        """source='zanmei' with zanmei fetch failure → (None, None, zanmei)."""
        raw, json_str, source = self._call(source="zanmei", zanmei_text=None)
        assert source == "zanmei"
        assert raw is None
        assert json_str is None
